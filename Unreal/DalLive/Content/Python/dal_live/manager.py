"""The editor side of DalLive: connects the C++ host components to ``Session`` objects and watches the files.

``install()`` (called from ``init_unreal.py``) binds three delegates of ``UDalLiveSubsystem``:

* ``on_host_begin(host)``  -> create a ``Session`` for the component, load its program;
* ``on_host_tick(host, dt)`` -> ``session.tick(dt)`` -- from the component's own ``TickComponent``, in its tick group;
* ``on_host_end(host)``   -> drop the session;

and a Slate post-tick callback that, ten times a second, looks at the files of the running sessions and swaps in the ones
that changed.  Errors never stop the game: a program that does not compile is reported (Output Log, Message Log, toast) and
the last good version keeps running.
"""

from __future__ import annotations

import os
import time

import unreal

from . import bootstrap
from .host import UeHost
from .session import Reporter, Session

_LEVELS = {
    "info": unreal.DalLiveMessageLevel.INFO,
    "success": unreal.DalLiveMessageLevel.SUCCESS,
    "warning": unreal.DalLiveMessageLevel.WARNING,
    "error": unreal.DalLiveMessageLevel.ERROR,
}


class EditorReporter(Reporter):
    """Output Log + Message Log page "DAL Live" + a toast for the things the user should notice."""

    def __init__(self, manager):
        super().__init__()
        self.manager = manager

    def _toast(self, text, level, seconds=6.0):
        sub = self.manager.subsystem
        if sub is not None:
            sub.show_toast(text, _LEVELS[level], seconds)

    def _message_log(self, text, level):
        sub = self.manager.subsystem
        if sub is not None:
            sub.add_message_log_line(text, _LEVELS[level])

    def info(self, session, text):
        unreal.log(f"DalLive: {text}")

    def compile_failed(self, session, lines, keeping):
        errors = [ln for ln in lines if ": error" in ln and not ln.startswith("    ")]
        for ln in lines:
            unreal.log_error(ln)
            if not ln.startswith("    "):
                self._message_log(ln, "error")
        first = errors[0].split(": error", 1)[-1].lstrip(" ").split(": ", 1)[-1] if errors else "see the log"
        tail = f" - still running generation {session.generation}" if keeping else " - not running"
        unreal.log_warning(f"DalLive: {session.name}: {len(errors)} error(s){tail}")
        self._toast(f"DAL: {len(errors)} error(s) in {session.name}{tail}\n{first}", "error", 9.0)
        self.manager.update_status(session)

    def reloaded(self, session, report, warnings, first):
        for ln in warnings:
            unreal.log_warning(ln)
        if first:
            unreal.log(f"DalLive: loaded {session.name} (generation {session.generation}, {session.module.kind} logic {session.module.name})")
        else:
            summary = report.summary() if report else "logic kind changed - restarted from the defaults"
            unreal.log(f"DalLive: reloaded {session.name} -> generation {session.generation}: {summary}")
            self._toast(f"DAL: reloaded {session.name} (gen {session.generation})\n{summary}", "success", 4.0)
        self.manager.update_status(session)

    def runtime_error(self, session, text, trace):
        unreal.log_error(f"DalLive: {session.name}: runtime error, the program is stopped until the file changes: {text}")
        for ln in trace.rstrip().splitlines():
            unreal.log_error("    " + ln)
        self._toast(f"DAL: runtime error in {session.name}\n{text}", "error", 9.0)
        self.manager.update_status(session)


class Manager:
    POLL_INTERVAL = 0.1

    def __init__(self):
        self.sessions: dict[int, Session] = {}
        self.subsystem = None
        self.reporter = EditorReporter(self)
        self.installed = False
        self.connect_error: str | None = None
        self._slate_handle = None
        self._last_poll = 0.0
        self.last_exception: str | None = None

    # ---- install ---------------------------------------------------------------------------------------------------

    def install(self) -> bool:
        """Bind the delegates (idempotent).  Returns False when the compiler or the subsystem is missing."""
        if self.installed:
            return True
        try:
            project_dir = unreal.Paths.project_dir()
            dal = bootstrap.ensure_dal(project_dir)
        except RuntimeError as e:
            self.connect_error = str(e)
            unreal.log_error(f"DalLive: {e}")
            return False
        sub = unreal.get_engine_subsystem(unreal.DalLiveSubsystem)
        if sub is None:
            self.connect_error = "UDalLiveSubsystem is not available yet"
            return False
        self.subsystem = sub
        sub.on_host_begin.add_callable(self._on_begin)
        sub.on_host_end.add_callable(self._on_end)
        sub.on_host_tick.add_callable(self._on_tick)
        sub.set_python_ready(True)
        if self._slate_handle is None:
            self._slate_handle = unreal.register_slate_post_tick_callback(self._on_slate)
        self.installed = True
        self.connect_error = None
        unreal.log(f"DalLive: ready (dal {dal.__version__} from {os.path.dirname(os.path.dirname(dal.__file__))})")
        for host in sub.get_active_hosts():          # components that began play before Python connected
            self._on_begin(host)
        return True

    def uninstall(self) -> None:
        sub = self.subsystem
        if sub is not None:
            sub.on_host_begin.remove_callable(self._on_begin)
            sub.on_host_end.remove_callable(self._on_end)
            sub.on_host_tick.remove_callable(self._on_tick)
            sub.set_python_ready(False)
        if self._slate_handle is not None:
            unreal.unregister_slate_post_tick_callback(self._slate_handle)
            self._slate_handle = None
        self.sessions.clear()
        self.installed = False

    # ---- delegates --------------------------------------------------------------------------------------------------

    def resolve_path(self, text: str) -> str:
        text = text.strip().strip('"')
        if not text:
            return ""
        if not os.path.isabs(text):
            text = os.path.join(unreal.Paths.project_dir(), text)
        return os.path.normpath(text)

    def _on_begin(self, comp) -> None:
        try:
            sid = int(comp.get_editor_property("session_id"))
            if sid in self.sessions:
                return
            path = self.resolve_path(comp.get_editor_property("dal_file").file_path)
            if not path:
                comp.set_status("no .dal file set (DalFile)", "", "", 0)
                unreal.log_warning(f"DalLive: {comp.get_name()} on {comp.get_owner().get_name()} has no DalFile")
                return
            overrides = {str(k): str(v) for k, v in comp.get_editor_property("param_overrides").items()}
            session = Session(path, UeHost(comp), self.reporter, overrides)
            session.comp = comp
            self.sessions[sid] = session
            session.start()
            self.update_status(session)
        except Exception as e:
            self._note_exception("begin", e)

    def _on_end(self, comp) -> None:
        try:
            session = self.sessions.pop(int(comp.get_editor_property("session_id")), None)
            if session is not None:
                session.close()
        except Exception as e:
            self._note_exception("end", e)

    def _on_tick(self, comp, delta_seconds, pending_events, pending_flags) -> None:
        try:
            session = self.sessions.get(comp.session_id)
            if session is not None:
                host = session.host
                host.pending_events = pending_events
                host.pending_flags = pending_flags
                session.tick(delta_seconds)
        except Exception as e:
            self._note_exception("tick", e)

    def _on_slate(self, delta_seconds) -> None:
        now = time.monotonic()
        if now - self._last_poll < self.POLL_INTERVAL:
            return
        self._last_poll = now
        self.poll(now)

    def poll(self, now: float | None = None) -> int:
        """Look at the files of all sessions whose component wants hot reload; returns how many programs were swapped in."""
        now = time.monotonic() if now is None else now
        swapped = 0
        for session in list(self.sessions.values()):
            try:
                if session.comp.get_editor_property("hot_reload") and session.poll(now):
                    swapped += 1
            except Exception as e:
                self._note_exception("poll", e)
        return swapped

    # ---- helpers ----------------------------------------------------------------------------------------------------------

    def _note_exception(self, where: str, e: Exception) -> None:
        text = f"{type(e).__name__}: {e}"
        if text != self.last_exception:
            self.last_exception = text
            unreal.log_error(f"DalLive: {where}: {text}")

    def update_status(self, session: Session) -> None:
        comp = getattr(session, "comp", None)
        if comp is None:
            return
        if session.fault:
            status = f"RUNTIME ERROR (stopped): {session.fault}"
        elif session.last_errors:
            first = next((ln for ln in session.last_errors if ": error" in ln), session.last_errors[0])
            status = (f"ERROR, running generation {session.generation}: " if session.interp else "ERROR, not running: ") + first
        elif session.interp is not None:
            status = f"running {session.name} (generation {session.generation})"
        else:
            status = "not loaded"
        name = session.module.name if session.module else ""
        kind = session.module.kind if session.module else ""
        comp.set_status(status, name, kind, session.generation)

    def session_for(self, comp) -> Session | None:
        return self.sessions.get(int(comp.get_editor_property("session_id")))

    def reload_all(self) -> int:
        """Recompile every session's file now (menu / script)."""
        done = 0
        for session in self.sessions.values():
            done += 1 if session.reload() else 0
        return done

    def stats(self) -> list[dict]:
        out = []
        for sid, s in self.sessions.items():
            total, longest, calls = s.comp.get_tick_stats()
            out.append({
                "session": sid, "file": s.name, "generation": s.generation, "running": s.running,
                "python_update_us": round(s.stats.mean_us, 2), "python_update_max_us": round(s.stats.max / 1000.0, 2),
                "ticks": s.stats.count,
                "broadcast_us": round(total / calls, 2) if calls else 0.0, "broadcast_max_us": round(longest, 2),
            })
        return out


manager = Manager()
