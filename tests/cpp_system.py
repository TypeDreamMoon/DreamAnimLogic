"""Compile several generated logics (an actor with peers, an animation instance ...) into one program against the stand-in
engine, wire them to one actor the way the engine does, and run frames through them in a schedule.

``build_and_run_system(modules, frames, schedule)`` returns, per frame, ``{logic: snapshot}`` with ``"__cmds__"`` (the host
effects that logic produced in the frame, in order) -- the layout of ``dal.harness.run_system``.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import tempfile

import cpp_driver as cd
from dal import harness, ir
from dal.cppbackend import CppGen, CppOptions, generate_cpp


def make_system_driver(modules: dict[str, ir.Module], frames: list[dict], schedule: list[tuple[str, str | None]],
                       params: dict[str, dict] | None = None) -> str:
    """``params`` is ``{logic: {param name: settings data}}``: the data of the settings params (as for ``cpp_driver.settings_setup``)."""
    gens = {n: CppGen(m, CppOptions()) for n, m in modules.items()}
    L = [
        '#include "StubEngine.h"',
    ]
    for n, m in modules.items():
        parent = gens[n].parent
        L += ["#undef DAL_STUB_SUPER", f"#define DAL_STUB_SUPER {parent}", f'#include "{m.name}.h"']
    L += [
        "",
        "#include <initializer_list>",
        "",
        "[[maybe_unused]] static void Pv(const char* Name, std::initializer_list<double> Values)",
        "{",
        '	std::printf("%s", Name);',
        "	for (double V : Values)",
        "	{",
        '		if (V != V) { std::printf(" nan"); }',
        '		else if (V > 1.7e308) { std::printf(" inf"); }',
        '		else if (V < -1.7e308) { std::printf(" -inf"); }',
        '		else { std::printf(" %.17g", V); }',
        "	}",
        '\tstd::printf("\\n");',
        "}",
        "",
        "[[maybe_unused]] static void PvS(const char* Name, const std::string& Value)",
        "{",
        '	std::printf("%s %s\\n", Name, Value.empty() ? "-" : Value.c_str());',
        "}",
        "",
        *cd.ASSET_HELPERS,
        "static void Pcmds(const char* Logic)",
        "{",
        "	for (const std::string& Line : StubLog::Lines())",
        "	{",
        '		std::printf("CMD %s %s\\n", Logic, Line.c_str());',
        "	}",
        "	StubLog::Lines().clear();",
        "}",
        "",
        "int main()",
        "{",
        "\tUWorld World;",
        "\tACharacter Pawn;",
        "\tUDalCharacterMovementComponent Move;",
        "\tUCapsuleComponent Capsule;",
        "\tAPlayerController Controller;",
        "\tUSkeletalMeshComponent Mesh;",
        "\tPawn.StubMovement = &Move;",
        "\tPawn.StubCapsule = &Capsule;",
        "\tPawn.StubController = &Controller;",
    ]
    for n, m in modules.items():
        L.append(f"\tU{m.name} L_{n};")
    actors = [n for n, m in modules.items() if m.kind == "actor"]
    anims = [n for n, m in modules.items() if m.kind == "anim"]
    L.append("\tPawn.StubComponents = {&Mesh" + "".join(f", &L_{n}" for n in actors) + "};")
    if anims:
        L.append(f"\tMesh.StubAnim = &L_{anims[0]};")
    for n in actors:
        L += [f"\tL_{n}.StubOwner = &Pawn;", f"\tL_{n}.StubWorld = &World;"]
    for n in anims:
        L += [f"\tL_{n}.StubPawn = &Pawn;", f"\tL_{n}.StubWorld = &World;", f"\tL_{n}.StubSkelMesh = &Mesh;"]
    for n, m in modules.items():                                            # settings params: data assets built from the given data
        for line in cd.settings_setup(m, (params or {}).get(n, {})):
            L.append(line.replace("Dal_P_", f"Dal_P_{n}_").replace("Obj.", f"L_{n}."))
    for n in actors:
        if gens[n].phased:
            L.append(f"\tL_{n}.RegisterComponentTickFunctions(true);")
    for n in actors:
        L.append(f"\tL_{n}.BeginPlay();")
    for n in anims:
        L.append(f"\tL_{n}.NativeInitializeAnimation();")
        if gens[n].begin_handlers:
            L.append(f"\tL_{n}.NativeBeginPlay();")
    for k, f in enumerate(frames):
        L.append(f"\t// ---- frame {k}")
        L.append("\t{")
        for key, fmt in cd._SIMPLE.items():
            if key in f:
                L.append("\t\t" + fmt(f[key]))
        if "time" in f:
            L.append(f"\t\tWorld.Time = {cd._num(f['time'])};")
        for cname, cv in f.get("curves", {}).items():                          # the animation curves of the previous evaluation
            for n in anims:
                L.append(f'\t\tL_{n}.StubCurves["{cname}"] = {cd._num(cv)}f;')
        for n, m in modules.items():
            for name, v in f.get("free", {}).get(n, {}).items():
                inp = m.find_input(name)
                L.append(f"\t\tL_{n}.{name} = {cd._lit(inp.ty, v)};")
        L.append("\t\tStubLog::Lines().clear();")
        dt = cd._num(f["dt"]) + "f"
        for si, (n, slot) in enumerate(schedule):
            for ev in f.get("step_events", {}).get(si, []):                # hook events that arrive between two steps (the movement component raised them)
                L.append("\t\t" + cd.hook_event(ev))
            g = gens[n]
            if modules[n].kind == "actor":
                if g.phased:
                    L.append(f"\t\tL_{n}.DalRunPhase({g.slots.index(slot)}, {dt});")
                else:
                    L.append(f"\t\tL_{n}.TickComponent({dt}, LEVELTICK_All, nullptr);")
            else:
                L.append(f"\t\tL_{n}.NativeUpdateAnimation({dt});")
                L.append(f"\t\tL_{n}.NativeThreadSafeUpdateAnimation({dt});")
            L.append(f'\t\tPcmds("{n}");')
        for n in modules:
            for call in f.get("calls", {}).get(n, []):
                L.append("\t\t" + cd.entry_call(modules[n], f"L_{n}", call))
            if gens[n].post:                                              # the graph has updated: the proxy's PostUpdate (post_update, post_flush)
                L.append(f"\t\tL_{n}.StubPostUpdate();")
            L.append(f'\t\tPcmds("{n}");')
        L.append(f'\t\tstd::printf("FRAME {k}\\n");')
        for n, m in modules.items():
            L.append(f'\t\tstd::printf("LOGIC {n}\\n");')
            for d in [*m.inputs, *m.states]:
                for path, ty in cd.leaves(d.name, d.ty):
                    L.append("\t\t" + cd._print_value(path, ty, f"L_{n}"))
        L.append("\t}")
    L.append("\treturn 0;")
    L.append("}")
    return "\n".join(L) + "\n"


def parse_system_output(modules: dict[str, ir.Module], text: str) -> list[dict]:
    types = {}
    for n, m in modules.items():
        t = {}
        for d in [*m.inputs, *m.states]:
            t.update(dict(cd.leaves(d.name, d.ty)))
        types[n] = t
    frames: list[dict] = []
    pending: dict[str, list] = {n: [] for n in modules}
    current = None
    for line in text.splitlines():
        parts = line.split()
        if not parts:
            continue
        if parts[0] == "CMD":
            toks = []
            for t in parts[3:]:
                try:
                    toks.append(float(t))
                except ValueError:
                    toks.append(t)
            pending[parts[1]].append((parts[2], *toks))
            continue
        if parts[0] == "FRAME":
            frames.append({n: {"__cmds__": pending[n]} for n in modules})
            pending = {n: [] for n in modules}
            continue
        if parts[0] == "LOGIC":
            current = parts[1]
            continue
        name, vals = parts[0], parts[1:]
        frames[-1][current][name] = cd.parse_value(types[current][name], vals)
    return frames


def build_and_run_system(modules: dict[str, ir.Module], frames: list[dict], schedule: list[tuple[str, str | None]] | None = None,
                         warnings_as_errors: bool = True, keep_dir: str | None = None, params: dict[str, dict] | None = None):
    compiler = cd.find_compiler()
    if compiler is None:
        raise RuntimeError("no C++ compiler")
    kind, env, path = compiler
    schedule = schedule or harness.default_schedule(modules)
    work = keep_dir or tempfile.mkdtemp(prefix="dal_sys_")
    try:
        gen, inc = os.path.join(work, "gen"), os.path.join(work, "inc")
        os.makedirs(gen, exist_ok=True)
        os.makedirs(inc, exist_ok=True)
        files: dict[str, str] = {}
        owner: dict[str, str] = {}
        for n, m in modules.items():
            for name, text in generate_cpp(m, CppOptions()).items():
                files[name] = text
                owner.setdefault(name, n)
        for name, text in files.items():
            with open(os.path.join(gen, name), "w", encoding="utf-8", newline="\n") as f:
                f.write(text)
        for name in ("CoreMinimal.h", "StubEngine.h"):
            shutil.copy(os.path.join(cd.STUB, name), os.path.join(inc, name))
        known = set(files) | {"CoreMinimal.h", "StubEngine.h", "stub_empty.h"}
        known |= {f"{m.name}.generated.h" for m in modules.values()}
        wanted = ["stub_empty.h"]
        for text in files.values():
            import re
            for mt in re.finditer(r'^#include "([^"]+)"', text, re.M):
                if mt.group(1) not in known and mt.group(1) not in wanted:
                    wanted.append(mt.group(1))
        for name in wanted:
            target = os.path.join(inc, name)
            os.makedirs(os.path.dirname(target), exist_ok=True)
            with open(target, "w", encoding="utf-8", newline="\n") as f:
                f.write('#pragma once\n#include "StubEngine.h"\n' if name != "stub_empty.h" else "// intentionally empty\n")
        for name in files:
            if name.endswith(".h") and name[:-2] not in ("DalMath", "DalHost"):
                with open(os.path.join(gen, name[:-2] + ".generated.h"), "w", encoding="utf-8", newline="\n") as f:
                    f.write("// intentionally empty\n")
        driver = os.path.join(work, "driver.cpp")
        with open(driver, "w", encoding="utf-8", newline="\n") as f:
            f.write(make_system_driver(modules, frames, schedule, params))
        includes = [inc, gen]
        sources = [(driver, "UObject")]
        for name in sorted(files):
            if name.endswith(".cpp"):
                logic = next((n for n, m in modules.items() if m.name + ".cpp" == name), None)
                parent = CppGen(modules[logic], CppOptions()).parent if logic else "UObject"
                sources.append((os.path.join(gen, name), parent))
        exe = os.path.join(work, "run.exe" if kind == "msvc" else "run")
        objs = []
        output = ""
        for k, (src, parent) in enumerate(sources):
            obj = os.path.join(work, f"o{k}.obj" if kind == "msvc" else f"o{k}.o")
            if kind == "msvc":
                cmd = [path, "/nologo", "/std:c++20", "/EHsc", "/W4", f"/DDAL_STUB_SUPER={parent}", *[f"/I{d}" for d in includes], "/c", src, f"/Fo:{obj}"]
                if warnings_as_errors:
                    cmd.insert(5, "/WX")
            else:
                cmd = [path, "-std=c++20", "-Wall", "-Wextra", f"-DDAL_STUB_SUPER={parent}", *[f"-I{d}" for d in includes], "-c", src, "-o", obj]
                if warnings_as_errors:
                    cmd.append("-Werror")
            r = subprocess.run(cmd, cwd=work, env=env, capture_output=True, text=True, errors="replace")
            output += r.stdout + r.stderr
            if r.returncode != 0:
                raise cd.CompileFailed(r.stdout + r.stderr)
            objs.append(obj)
        link = [path, "/nologo", *objs, f"/Fe:{exe}", "/F67108864"] if kind == "msvc" else [path, *objs, "-o", exe]        # a 64 MB stack: main holds every frame's locals
        r = subprocess.run(link, cwd=work, env=env, capture_output=True, text=True, errors="replace")
        if r.returncode != 0:
            raise cd.CompileFailed(r.stdout + r.stderr)
        run = subprocess.run([exe], cwd=work, capture_output=True, text=True)
        if run.returncode != 0:
            raise cd.CompileFailed(f"driver exited with {run.returncode}\n{run.stderr}")
        return parse_system_output(modules, run.stdout), output
    finally:
        if keep_dir is None:
            shutil.rmtree(work, ignore_errors=True)
