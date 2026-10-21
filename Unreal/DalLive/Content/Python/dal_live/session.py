"""One running .dal program on one host: load, tick, hot swap, keep the last good version.

No ``unreal`` import: the host (``HostV1``) and the reporter are injected, so the swap rules are tested in the
compiler repo with ``MockHost`` / ``SimHost`` (``tests/test_live_session.py``) and run in the editor with ``UeHost``.

The tick is the interpreter's ``update(dt)``: sample the inputs through the host, collect the events
(``host.poll_events()``), tick the timers, run the handlers, run the body, hand the queued commands to the host --
the same order as ``TickComponent`` of the generated actor component (``DalSampleInputs`` / ``DalTakeEvents`` /
``DalUpdate`` / ``DalFlushCommands``).  For ``anim`` logic the commands are *not* executed at the end of the update:
they wait for the start of the next one, like the generated ``NativeUpdateAnimation`` does.
"""

from __future__ import annotations

import os
import time
import traceback

from dal.interp import Interpreter

from . import loader
from .swap import MigrationReport, migrate_values


class Reporter:
    """Where a session talks to the user.  This one collects the messages (tests); the editor one logs and toasts."""

    def __init__(self):
        self.messages: list[tuple[str, str]] = []

    def info(self, session, text):
        self.messages.append(("info", text))

    def compile_failed(self, session, lines, keeping):
        self.messages.append(("compile_error", "\n".join(lines)))

    def reloaded(self, session, report, warnings, first):
        self.messages.append(("reloaded", report.summary() if report else "first load"))

    def runtime_error(self, session, text, trace):
        self.messages.append(("runtime_error", text))


class TickStats:
    """Cost of the update calls in nanoseconds (cheap enough to leave on)."""

    __slots__ = ("count", "total", "max", "last")

    def __init__(self):
        self.reset()

    def reset(self):
        self.count = 0
        self.total = 0
        self.max = 0
        self.last = 0

    def add(self, ns: int):
        self.count += 1
        self.total += ns
        self.last = ns
        if ns > self.max:
            self.max = ns

    @property
    def mean_us(self) -> float:
        return self.total / self.count / 1000.0 if self.count else 0.0

    def __repr__(self):
        return f"TickStats(n={self.count}, mean={self.mean_us:.1f}us, max={self.max / 1000.0:.1f}us)"


class Session:
    DEBOUNCE = 0.15          # seconds a changed file must stay unchanged before it is compiled (editors write in steps)

    def __init__(self, path: str, host, reporter: Reporter | None = None, overrides: dict | None = None):
        self.path = os.path.abspath(path)
        self.host = host
        self.reporter = reporter or Reporter()
        self.overrides = dict(overrides or {})       # param name -> text
        self.interp: Interpreter | None = None
        self.module = None
        self.generation = 0                          # number of successful loads
        self.fingerprint = None
        self.fault: str | None = None                # runtime error of the running program (cleared by the next good load)
        self.last_errors: list[str] = []             # compile errors of the last failed load (cleared by a good one)
        self.last_report: MigrationReport | None = None
        self.stats = TickStats()
        self._deferred: list = []
        self._candidate = None
        self._candidate_since = 0.0
        self._closed = False

    # ---- loading -------------------------------------------------------------------------------------------------------

    @property
    def name(self) -> str:
        return os.path.basename(self.path)

    @property
    def running(self) -> bool:
        return self.interp is not None and self.fault is None

    def start(self) -> bool:
        return self.reload()

    def _params_for(self, module) -> dict:
        params = {}
        for p in module.params:
            text = self.overrides.get(p.name)
            if text is None:
                continue
            try:
                params[p.name] = loader.parse_param(p.ty, text) if isinstance(text, str) else text
            except (ValueError, TypeError) as e:
                self.reporter.info(self, f"{self.name}: ignoring the override of param {p.name}: {e}")
        return params

    def reload(self) -> bool:
        """Compile the file and, if it checks, swap it in (keeping the matching state).  Returns True when it did."""
        res = loader.load_program(self.path)
        self.fingerprint = res.fingerprint
        interp = None
        if res.ok:
            try:
                interp = Interpreter(res.module, self.host, externs=res.externs, **self._params_for(res.module))
            except Exception as e:
                res.errors.append(f"{self.path}: error: cannot start the program: {type(e).__name__}: {e}")
        if interp is None:
            self.last_errors = res.errors
            self.reporter.compile_failed(self, res.errors, keeping=self.interp is not None)
            return False

        report = None
        first = self.interp is None
        if not first and self.module.kind == res.module.kind:
            report = migrate_values(self.module, self.interp.values, res.module, interp.values)
            interp._dt = self.interp._dt
            interp._pending = self.interp._pending
        self.last_report = report
        self.last_errors = []
        self.module = res.module
        self.interp = interp
        self.fault = None
        self.generation += 1
        self._deferred = []
        if res.module.kind == "anim":
            # anim logic: the commands of an update run at the start of the next one (generated NativeUpdateAnimation)
            interp.flush = self._defer_flush
        program_changed = getattr(self.host, "program_changed", None)
        if program_changed is not None:
            program_changed(res.module, self)
        self.reporter.reloaded(self, report, res.warnings, first)
        return True

    def _defer_flush(self):
        self._deferred.extend(self.interp.take_commands())

    # ---- watching ------------------------------------------------------------------------------------------------------------

    def poll(self, now: float | None = None) -> bool:
        """Look at the file; compile it once it has changed and been quiet for ``DEBOUNCE`` seconds.  True if it was swapped in."""
        if self._closed:
            return False
        now = time.monotonic() if now is None else now
        fp = loader.fingerprint(self.path)
        if fp is None or fp == self.fingerprint:
            self._candidate = None
            return False
        if fp != self._candidate:
            self._candidate = fp
            self._candidate_since = now
            return False
        if now - self._candidate_since < self.DEBOUNCE:
            return False
        self._candidate = None
        return self.reload()

    # ---- running -----------------------------------------------------------------------------------------------------------

    def tick(self, dt: float) -> None:
        interp = self.interp
        if interp is None or self.fault is not None or self._closed:
            return
        t0 = time.perf_counter_ns()
        try:
            begin = getattr(self.host, "begin_update", None)
            if begin is not None:
                begin()
            if self._deferred:
                pending, self._deferred = self._deferred, []
                for name, args in pending:
                    fn = getattr(self.host, name, None)
                    if fn is not None:
                        fn(*args)
            interp.update(dt)
            module = self.module
            if module is not None and module.kind == "anim" and (module.post_update is not None or module.post_flush):
                interp.post_update()               # the block after the graph update (the interpreter has no graph: right after update); its commands are deferred like the others
            after = getattr(self.host, "after_update", None)
            if after is not None:
                after(self)
        except Exception as e:
            self.fault = f"{type(e).__name__}: {e}"
            self.reporter.runtime_error(self, self.fault, traceback.format_exc())
            return
        self.stats.add(time.perf_counter_ns() - t0)

    def snapshot(self) -> dict:
        return self.interp.snapshot() if self.interp is not None else {}

    def close(self) -> None:
        self._closed = True
        closer = getattr(self.host, "close", None)
        if closer is not None:
            closer()
