"""PIE scenarios of the W4 harness: the same scripted input against the compiled (generated C++) or the interpreted
(DalLive) version of a .dal program.  Environment: DALTEST_SCRIPTS, DALTEST_MODE (compiled | interp),
DALTEST_SCENARIO (melee | lockon | gait | melee_swap | lockon_swap ...), DALTEST_NAME (result file name).
"""

import os
import shutil
import sys

SCRIPTS = os.environ.get("DALTEST_SCRIPTS", os.path.dirname(os.path.abspath(__file__)))
if SCRIPTS not in sys.path:
    sys.path.insert(0, SCRIPTS)

import unreal

import harness_common as H

MODE = os.environ.get("DALTEST_MODE", "interp")
SCENARIO = os.environ.get("DALTEST_SCENARIO", "melee")
NAME = os.environ.get("DALTEST_NAME", f"{SCENARIO}_{MODE}")
REPO_EXAMPLES = os.path.normpath(os.path.join(SCRIPTS, "..", "..", "..", "examples"))


def deploy(dal_name):
    """Copy the example into the project's Dal folder (a fresh copy for every run) and return its path."""
    os.makedirs(H.DAL_DIR, exist_ok=True)
    dst = os.path.join(H.DAL_DIR, dal_name)
    shutil.copyfile(os.path.join(REPO_EXAMPLES, dal_name), dst)
    return dst


def manager():
    import dal_live

    return dal_live.get_manager()


class Probe:
    """Reads the state of the logic (compiled: UPROPERTYs by name; interpreted: the interpreter's snapshot) and the host effects."""

    def __init__(self, ctx, comp, module, montages=()):
        self.ctx = ctx
        self.comp = comp
        self.module = module
        self.names = [i.name for i in module.inputs] + [s.name for s in module.states]
        self.types = {i.name: i.ty for i in module.inputs}
        self.types.update({s.name: s.ty for s in module.states})
        self.montages = dict(montages)
        pawn = ctx.pawn
        self.cmc = pawn.get_component_by_class(unreal.CharacterMovementComponent)
        self.mesh = pawn.get_component_by_class(unreal.SkeletalMeshComponent)
        self.arm = pawn.get_component_by_class(unreal.SpringArmComponent)

    def state(self):
        if MODE == "compiled":
            out = {}
            for n in self.names:
                out[n] = H.plain(self.comp.get_editor_property(H.pyname(self.comp, n)))
            return out
        session = manager().session_for(self.comp)
        if session is None or session.interp is None:
            return {}
        snap = session.interp.snapshot()
        types = {i.name: i.ty for i in session.module.inputs}
        types.update({st.name: st.ty for st in session.module.states})
        out = {}
        for n, v in snap.items():
            ty = types[n]
            if getattr(ty, "members", None) is not None and v is not None:
                v = ty.members[int(v)]
            out[n] = list(v) if isinstance(v, tuple) else v
        return out

    def effects(self):
        ctx = self.ctx
        anim = self.mesh.get_anim_instance()
        fx = {
            "max_walk_speed": self.cmc.max_walk_speed,
            "grounded": bool(self.cmc.is_moving_on_ground()),
            "location": H.plain(ctx.pawn.get_actor_location()),
            "control_rotation": H.plain(ctx.pc.get_control_rotation()),
            "arm_length": self.arm.target_arm_length if self.arm is not None else None,
        }
        mont = {}
        for name, asset in self.montages.items():
            if anim is None:
                continue
            mont[name] = [bool(anim.montage_is_playing(asset)), bool(anim.montage_is_active(asset)),
                          float(anim.montage_get_position(asset)), str(anim.montage_get_current_section(asset))]
        fx["montages"] = mont
        return fx

    def record(self, extra=None):
        ctx = self.ctx
        rec = {"f": ctx.frame, "t": unreal.GameplayStatics.get_time_seconds(ctx.world), "state": self.state(), "fx": self.effects()}
        if extra:
            rec.update(extra)
        ctx.records.append(rec)


def attach(ctx, program, props_compiled, props_interp, compiled_cls):
    """Add the logic component to the pawn: the generated one (compiled) or the DalLive host (interpreted)."""
    pawn = ctx.pawn
    if MODE == "compiled":
        return H.add_component(pawn, compiled_cls, props_compiled, ctx)
    props = dict(props_interp)
    props["dal_file"] = unreal.FilePath(file_path=program)
    return H.add_component(pawn, unreal.DalInterpHost, props, ctx)


def percentile(sorted_values, q):
    if not sorted_values:
        return 0.0
    k = min(len(sorted_values) - 1, max(0, int(round(q * (len(sorted_values) - 1)))))
    return sorted_values[k]


def comp_stats(comp):
    """Cost of one tick of the logic component as measured around TickComponent / the delegate broadcast (microseconds)."""
    total, longest, calls = comp.get_tick_stats()
    samples = sorted(float(x) for x in comp.get_tick_samples())
    out = {"total_us": total, "max_us": longest, "calls": calls, "mean_us": total / calls if calls else 0.0,
           "p50_us": percentile(samples, 0.50), "p95_us": percentile(samples, 0.95), "p99_us": percentile(samples, 0.99)}
    if MODE == "interp":
        session = manager().session_for(comp)
        if session is not None:
            out["python_update_mean_us"] = session.stats.mean_us
            out["python_update_max_us"] = session.stats.max / 1000.0
    return out


# ---- melee ---------------------------------------------------------------------------------------------------------------------

MELEE_FRAMES = 540
MELEE_TAPS = {20, 62, 101, 131, 222, 241, 282, 362, 401}
MELEE_JUMP = 272


def scenario_melee(ctx):
    program = deploy("melee_combo.dal")
    module = H.load_module_info(program)
    attack = H.asset("/Game/DalTest/IA_Attack")
    montages = {f"Light{i}": H.asset(f"/Game/DalTest/AM_Light{i}") for i in (1, 2, 3)}
    yield 40                                              # the character lands
    comp = attach(
        ctx, program,
        {"dal_action_attack": attack, **{f"dal_montage_light{i}": montages[f"Light{i}"] for i in (1, 2, 3)}},
        {"actions": {"Attack": attack}, "montages": montages},
        unreal.DalTimedMelee)
    probe = Probe(ctx, comp, module, montages)
    ctx.frame = 0
    if hasattr(comp, "reset_tick_stats"):
        comp.reset_tick_stats()
    prof = None
    if os.environ.get("DALTEST_PROFILE") and MODE == "interp":
        import cProfile

        prof = cProfile.Profile()
        sess = manager().session_for(comp)
        orig_tick = sess.tick
        sess.tick = lambda dt: prof.runcall(orig_tick, dt)
    for i in range(MELEE_FRAMES):
        probe.record()
        if i in MELEE_TAPS:
            ctx.inject(attack, 1.0)
            ctx.mark("tap", action="Attack")
        if i == MELEE_JUMP:
            ctx.pawn.jump()
            ctx.mark("jump")
        if i == MELEE_JUMP + 3:
            ctx.pawn.stop_jumping()
        if 5 <= i < 120:
            ctx.pawn.add_movement_input(unreal.Vector(1, 0, 0), 1.0, False)
        yield 1
    ctx.notes["tick_stats"] = comp_stats(comp)
    if prof is not None:
        import io
        import pstats

        buf = io.StringIO()
        pstats.Stats(prof, stream=buf).sort_stats("tottime").print_stats(18)
        ctx.notes["profile"] = buf.getvalue()


# ---- lock-on --------------------------------------------------------------------------------------------------------------------

LOCKON_FRAMES = 700


def scenario_lockon(ctx):
    program = deploy("lockon_camera.dal")
    module = H.load_module_info(program)
    lockon = H.asset("/Game/DalTest/IA_LockOn")
    yield 40
    comp = attach(ctx, program, {"dal_action_lock_on": lockon}, {"actions": {"LockOn": lockon}}, unreal.DalTimedLockOn)
    probe = Probe(ctx, comp, module)
    ctx.frame = 0
    base = ctx.pawn.get_actor_location()
    tag = "DalTarget"
    target_a = unreal.DalTestLibrary.spawn_cube(ctx.world, unreal.Vector(base.x + 600, base.y + 150, base.z), unreal.Vector(0.5, 0.5, 1.5), tag, False)
    target_b = unreal.DalTestLibrary.spawn_cube(ctx.world, unreal.Vector(base.x + 400, base.y - 300, base.z), unreal.Vector(0.5, 0.5, 1.5), tag, False)
    target_c = unreal.DalTestLibrary.spawn_cube(ctx.world, unreal.Vector(base.x - 500, base.y, base.z), unreal.Vector(0.5, 0.5, 1.5), tag, False)
    blocker = None
    a0 = target_a.get_actor_location()
    for i in range(LOCKON_FRAMES):
        probe.record({"target_a": H.plain(target_a.get_actor_location()) if target_a else None})
        if i == 20:
            ctx.inject(lockon, 1.0)
            ctx.mark("tap", action="LockOn")
        if 100 <= i < 220 and target_a:                  # the locked target walks sideways and away
            t = (i - 100) / 120.0
            target_a.set_actor_location(unreal.Vector(a0.x + 300 * t, a0.y + 400 * t, a0.z), False, False)
        if i == 240:                                     # something solid between player and target
            la = target_a.get_actor_location()
            mid = unreal.Vector(base.x + (la.x - base.x) * 0.5, base.y + (la.y - base.y) * 0.5, base.z + 60)
            blocker = unreal.DalTestLibrary.spawn_cube(ctx.world, mid, unreal.Vector(1.0, 3.0, 3.0), "", True)
            ctx.mark("blocker")
        if i == 400:
            ctx.inject(lockon, 1.0)                      # try again: still hidden
            ctx.mark("tap", action="LockOn")
        if i == 460 and blocker is not None:
            blocker.destroy_actor()
            blocker = None
            ctx.mark("blocker_removed")
        if i == 490:
            ctx.inject(lockon, 1.0)
            ctx.mark("tap", action="LockOn")
        if i == 600 and target_a is not None:
            target_a.destroy_actor()
            target_a = None
            ctx.mark("target_destroyed")
        yield 1
    ctx.notes["tick_stats"] = comp_stats(comp)


# ---- hot swap: edit the .dal file while PIE runs ---------------------------------------------------------------------------------

def scenario_melee_swap(ctx):
    import time

    if MODE != "interp":
        raise RuntimeError("melee_swap only makes sense for the interpreted host")
    program = deploy("melee_combo.dal")
    original = open(program, encoding="utf-8").read()
    module = H.load_module_info(program)
    attack = H.asset("/Game/DalTest/IA_Attack")
    montages = {f"Light{i}": H.asset(f"/Game/DalTest/AM_Light{i}") for i in (1, 2, 3)}
    yield 40
    props = {"actions": {"Attack": attack}, "montages": montages}
    if os.environ.get("DALTEST_PARAM_OVERRIDE"):
        props["param_overrides"] = {"AttackMoveSpeed": "200"}      # must win over the defaults of every generation
    comp = attach(ctx, program, {}, props, None)
    probe = Probe(ctx, comp, module, montages)
    ctx.frame = 0
    mgr = manager()
    state = {"gen": 0}

    def session():
        return mgr.session_for(comp)

    def advance(n):
        for _ in range(n):
            s = session()
            gen = s.generation if s else 0
            if gen != state["gen"]:
                state["gen"] = gen
                ctx.mark("generation", generation=gen, report=s.last_report.summary() if s and s.last_report else "first load")
            probe.record({"gen": gen, "status": str(comp.get_editor_property("status")), "errors": len(s.last_errors) if s else 0})
            yield 1

    def edit(text, label):
        with open(program, "w", encoding="utf-8", newline="\n") as f:
            f.write(text)
        ctx.mark("edit", label=label)
        return time.time(), ctx.frame

    def wait(cond, label, limit=3000):
        for _ in range(limit):
            if cond():
                return
            yield from advance(1)
        raise RuntimeError("timeout waiting for " + label)

    def tap():
        ctx.inject(attack, 1.0)
        ctx.mark("tap", action="Attack")

    v2 = original.replace("Hits += 1;", "Hits += 10;").replace("param AttackMoveSpeed: float = 120.0;", "param AttackMoveSpeed: float = 60.0;")
    assert v2 != original
    broken = v2.replace("Hits += 10;", "Hits += Oops;").replace("state WasAttacking: bool;", "state WasAttacking: int = true;")
    v3 = v2.replace("state WasAttacking: bool;", "state WasAttacking: bool;\nstate Extra: int;").replace(
        "on notify Hit {", "on montage_ended Light1 {\n    Extra += 1;\n}\n\non notify Hit {")
    assert v3 != v2

    yield from advance(20)
    tap()
    yield from advance(110)                                    # combo 1 ends, Hits == 1 (one Hit notify), walk speed back to 600
    ctx.notes["before_swap"] = probe.state()

    t, f = edit(v2, "v2: Hits += 10, AttackMoveSpeed 60")
    yield from wait(lambda: session().generation >= 2, "generation 2")
    ctx.notes["swap1"] = {"wall_ms": round((time.time() - t) * 1000.0), "frames": ctx.frame - f, "state": probe.state()}
    yield from advance(20)
    tap()
    yield from advance(110)                                    # Hits == 11 and the walk speed dropped to 60 during the attack
    ctx.notes["after_v2_attack"] = probe.state()

    t, f = edit(broken, "broken: undefined name, type error")
    yield from wait(lambda: len(session().last_errors) > 0, "compile error report")
    ctx.notes["broken"] = {"wall_ms": round((time.time() - t) * 1000.0), "frames": ctx.frame - f, "generation": session().generation,
                           "errors": list(session().last_errors), "status": str(comp.get_editor_property("status"))}
    yield from advance(20)
    tap()
    yield from advance(110)                                    # still running v2: Hits == 21
    ctx.notes["after_broken_attack"] = probe.state()

    t, f = edit(v3, "v3: new state Extra, new handler")
    yield from wait(lambda: session().generation >= 3, "generation 3")
    ctx.notes["swap2"] = {"wall_ms": round((time.time() - t) * 1000.0), "frames": ctx.frame - f, "state": probe.state()}
    yield from advance(20)
    tap()
    yield from advance(110)                                    # Extra == 1 when Light1 ends, Hits == 31
    ctx.notes["after_v3_attack"] = probe.state()
    with open(program, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(original)
    ctx.notes["tick_stats"] = comp_stats(comp)


# ---- micro-benchmark of the unreal Python calls the host makes every tick ---------------------------------------------------------

def scenario_bench(ctx):
    import time

    program = deploy("melee_combo.dal")
    attack = H.asset("/Game/DalTest/IA_Attack")
    montages = {f"Light{i}": H.asset(f"/Game/DalTest/AM_Light{i}") for i in (1, 2, 3)}
    yield 40
    comp = attach(ctx, program, {}, {"actions": {"Attack": attack}, "montages": montages}, None)
    yield 10
    cmc = comp.get_movement()
    owner = comp.get_owner()
    anim = comp.get_anim_instance()
    m1 = montages["Light1"]
    N = 3000
    results = {}

    def bench(name, fn):
        fn()
        t = time.perf_counter()
        for _ in range(N):
            fn()
        results[name] = round((time.perf_counter() - t) / N * 1e6, 2)

    bench("noop python call", lambda: None)
    bench("comp.get_movement()", lambda: comp.get_movement())
    bench("comp.get_owner()", lambda: comp.get_owner())
    bench("comp.take_events() (empty)", lambda: comp.take_events())
    bench("comp.consume_notify_flag(name)", lambda: comp.consume_notify_flag("Hit"))
    bench("cmc.is_moving_on_ground()", lambda: cmc.is_moving_on_ground())
    bench("cmc.velocity", lambda: cmc.velocity)
    bench("cmc.get_editor_property('velocity')", lambda: cmc.get_editor_property("velocity"))
    bench("cmc.max_walk_speed", lambda: cmc.max_walk_speed)
    bench("cmc.set_editor_property('max_walk_speed')", lambda: cmc.set_editor_property("max_walk_speed", 500.0))
    bench("owner.get_actor_location()", lambda: owner.get_actor_location())
    bench("owner.get_actor_rotation().quaternion()", lambda: owner.get_actor_rotation().quaternion())
    bench("owner.get_control_rotation()", lambda: owner.get_control_rotation())
    bench("GameplayStatics.get_time_seconds(comp)", lambda: unreal.GameplayStatics.get_time_seconds(comp))
    bench("anim.montage_is_playing(m)", lambda: anim.montage_is_playing(m1))
    bench("comp.get_action_value('Attack')", lambda: comp.get_action_value("Attack"))
    bench("comp.trace_line(a, b)", lambda: comp.trace_line(unreal.Vector(0, 0, 100), unreal.Vector(500, 0, 100)))
    bench("comp.target_count()", lambda: comp.target_count())
    bench("unreal.Vector(x, y, z)", lambda: unreal.Vector(1.0, 2.0, 3.0))
    bench("publish_values([8 doubles])", lambda: comp.publish_values([0.0] * 8))
    ctx.notes["bench_us"] = results
    ctx.notes["n"] = N
    ctx.records.append({"f": 0, "t": 0.0, "state": {}, "fx": {}})


def scenario_overhead(ctx):
    """The cost of the delegate -> Python callable round trip alone: a host without a program."""
    yield 40
    comp = H.add_component(ctx.pawn, unreal.DalInterpHost, {}, ctx)
    yield 5
    comp.reset_tick_stats()
    for _ in range(1200):
        yield 1
    ctx.notes["overhead"] = comp_stats(comp)
    ctx.records.append({"f": 0, "t": 0.0, "state": {}, "fx": {}})


def scenario_bench_cb(ctx):
    """The same crossings as `bench`, but timed inside a delegate callback (where the host really makes them)."""
    import time

    yield 40
    comp = H.add_component(ctx.pawn, unreal.DalInterpHost, {}, ctx)
    yield 5
    sub = unreal.get_engine_subsystem(unreal.DalLiveSubsystem)
    results = {}
    done = []

    def on_tick(host, dt):
        if done:
            return
        done.append(1)
        cmc = host.get_movement()
        owner = host.get_owner()
        N = 2000

        def bench(name, fn):
            fn()
            t = time.perf_counter()
            for _ in range(N):
                fn()
            results[name] = round((time.perf_counter() - t) / N * 1e6, 2)

        bench("noop", lambda: None)
        bench("comp.get_movement()", lambda: host.get_movement())
        bench("comp.take_events()", lambda: host.take_events())
        bench("cmc.is_moving_on_ground()", lambda: cmc.is_moving_on_ground())
        bench("cmc.velocity", lambda: cmc.velocity)
        bench("owner.get_actor_location()", lambda: owner.get_actor_location())
        bench("comp.target_count()", lambda: host.target_count())

    sub.on_host_tick.add_callable(on_tick)
    for _ in range(5):
        yield 1
    sub.on_host_tick.remove_callable(on_tick)
    ctx.notes["bench_cb_us"] = results
    ctx.records.append({"f": 0, "t": 0.0, "state": {}, "fx": {}})


# ---- anim logic (option B): hero_gait through a property-bag AnimInstance -----------------------------------------------------------

GAIT_FRAMES = 520


class AnimProbe:
    """Reads the program's params / inputs / states as properties of the mesh's AnimInstance: the generated UHeroGait
    (compiled) or UHeroGaitBag (interpreted: the values DalLive wrote by name) -- what an Animation Blueprint would read."""

    def __init__(self, ctx, module):
        self.ctx = ctx
        self.names = [p.name for p in module.params] + [i.name for i in module.inputs] + [s.name for s in module.states]
        self.mesh = ctx.pawn.get_component_by_class(unreal.SkeletalMeshComponent)
        self.cmc = ctx.pawn.get_component_by_class(unreal.CharacterMovementComponent)

    def record(self, extra=None):
        ctx = self.ctx
        anim = self.mesh.get_anim_instance()
        state = {n: H.plain(anim.get_editor_property(H.pyname(anim, n))) for n in self.names}
        fx = {"location": H.plain(ctx.pawn.get_actor_location()), "velocity": H.plain(self.cmc.velocity),
              "control_rotation": H.plain(ctx.pc.get_control_rotation()), "grounded": bool(self.cmc.is_moving_on_ground())}
        rec = {"f": ctx.frame, "t": unreal.GameplayStatics.get_time_seconds(ctx.world), "state": state, "fx": fx}
        if extra:
            rec.update(extra)
        ctx.records.append(rec)


def scenario_gait(ctx):
    program = deploy("hero_gait.dal")
    module = H.load_module_info(program)
    move = H.asset("/Game/Input/Actions/IA_Move")
    sprint = H.asset("/Game/DalTest/IA_Sprint")
    pawn = ctx.pawn
    mesh = pawn.get_component_by_class(unreal.SkeletalMeshComponent)
    cmc = pawn.get_component_by_class(unreal.CharacterMovementComponent)
    yield 30
    mesh.add_tick_prerequisite_component(cmc)                 # the same movement-then-animation order in both runs
    comp = None
    if MODE == "compiled":
        mesh.set_anim_instance_class(unreal.HeroGait)
        anim = mesh.get_anim_instance()
        anim.set_editor_property("dal_action_move", move)
        anim.set_editor_property("dal_action_sprint", sprint)
    else:
        mesh.set_anim_instance_class(unreal.HeroGaitBag)
        comp = H.add_component(pawn, unreal.DalInterpHost, {"dal_file": unreal.FilePath(file_path=program), "actions": {"Move": move, "Sprint": sprint}}, ctx)
    yield 10
    probe = AnimProbe(ctx, module)
    ctx.frame = 0
    if comp is not None:
        comp.reset_tick_stats()
    for i in range(GAIT_FRAMES):
        probe.record()
        forward = pawn.get_actor_forward_vector()
        if 60 <= i < 200:
            pawn.add_movement_input(unreal.Vector(1, 0, 0), 1.0, False)
            ctx.inject(move, 0.0, 1.0)
        if 120 <= i < 200:
            ctx.inject(sprint, 1.0)
        if 200 <= i < 260:
            pawn.add_movement_input(unreal.Vector(0, 1, 0), 1.0, False)
            ctx.inject(move, 1.0, 0.0)
        if i == 262:
            pawn.jump()
            ctx.mark("jump")
        if i == 266:
            pawn.stop_jumping()
        if 300 <= i < 380:
            ctx.pc.set_control_rotation(unreal.Rotator(roll=0.0, pitch=(i - 300) * 0.5, yaw=(i - 300) * 1.5))
        if i == 320:
            # game code writes the free input on the AnimInstance (VisibleAnywhere: Python refuses to set it, so a C++ helper does)
            unreal.DalTestLibrary.set_bool_property(mesh.get_anim_instance(), "Aiming", True)
            ctx.mark("aiming", value=True)
        if i == 440:
            unreal.DalTestLibrary.set_bool_property(mesh.get_anim_instance(), "Aiming", False)
            ctx.mark("aiming", value=False)
        yield 1
    if comp is not None:
        ctx.notes["tick_stats"] = comp_stats(comp)


def scenario_gait_swap(ctx):
    """Hot swap of anim logic: the AnimInstance variables follow the edited file, Speed keeps its value across the swap."""
    import time

    if MODE != "interp":
        raise RuntimeError("gait_swap only makes sense for the interpreted host")
    program = deploy("hero_gait.dal")
    original = open(program, encoding="utf-8").read()
    module = H.load_module_info(program)
    move = H.asset("/Game/Input/Actions/IA_Move")
    sprint = H.asset("/Game/DalTest/IA_Sprint")
    pawn = ctx.pawn
    mesh = pawn.get_component_by_class(unreal.SkeletalMeshComponent)
    cmc = pawn.get_component_by_class(unreal.CharacterMovementComponent)
    yield 30
    mesh.add_tick_prerequisite_component(cmc)
    mesh.set_anim_instance_class(unreal.HeroGaitBag)
    comp = H.add_component(pawn, unreal.DalInterpHost, {"dal_file": unreal.FilePath(file_path=program), "actions": {"Move": move, "Sprint": sprint}}, ctx)
    yield 10
    probe = AnimProbe(ctx, module)
    ctx.frame = 0
    mgr = manager()
    state = {"gen": 0}

    def session():
        return mgr.session_for(comp)

    def advance(n):
        for _ in range(n):
            s_ = session()
            gen = s_.generation if s_ else 0
            if gen != state["gen"]:
                state["gen"] = gen
                ctx.mark("generation", generation=gen, report=s_.last_report.summary() if s_ and s_.last_report else "first load")
            pawn.add_movement_input(unreal.Vector(1, 0, 0), 1.0, False)
            probe.record({"gen": gen, "boost": s_.interp.values.get("Boost") if s_ and s_.interp else None})
            yield 1

    def edit(text, label):
        with open(program, "w", encoding="utf-8", newline="\n") as f:
            f.write(text)
        ctx.mark("edit", label=label)
        return time.time(), ctx.frame

    def wait_gen(n, limit=3000):
        for _ in range(limit):
            if session().generation >= n:
                return
            yield from advance(1)
        raise RuntimeError("timeout waiting for generation %d" % n)

    v2 = original.replace("const WalkSpeed: float = 175.0;", "const WalkSpeed: float = 400.0;")
    v3 = v2.replace("state Steps: int;", "state Steps: int;\nstate Boost: float;").replace(
        "    CurrentGait = Crouched", "    Boost = Speed * 2.0;\n    CurrentGait = Crouched")
    assert v2 != original and v3 != v2
    yield from advance(120)                                     # running: Speed ~ 500, gait Run
    ctx.notes["before_swap"] = {"speed": probe.ctx.records[-1]["state"]["Speed"], "gait": probe.ctx.records[-1]["state"]["CurrentGait"]}
    t, f = edit(v2, "v2: WalkSpeed 175 -> 400 (Run needs speed > 600)")
    yield from wait_gen(2)
    ctx.notes["swap1"] = {"wall_ms": round((time.time() - t) * 1000.0), "frames": ctx.frame - f}
    yield from advance(30)
    ctx.notes["after_v2"] = {"speed": ctx.records[-1]["state"]["Speed"], "gait": ctx.records[-1]["state"]["CurrentGait"]}
    t, f = edit(v3, "v3: new state Boost (the AnimInstance has no such property)")
    yield from wait_gen(3)
    ctx.notes["swap2"] = {"wall_ms": round((time.time() - t) * 1000.0), "frames": ctx.frame - f}
    yield from advance(30)
    ctx.notes["after_v3"] = {"speed": ctx.records[-1]["state"]["Speed"], "boost": ctx.records[-1]["boost"], "gait": ctx.records[-1]["state"]["CurrentGait"]}
    with open(program, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(original)
    ctx.notes["tick_stats"] = comp_stats(comp)


SCENARIOS = {"melee": scenario_melee, "lockon": scenario_lockon, "melee_swap": scenario_melee_swap, "bench": scenario_bench, "overhead": scenario_overhead, "bench_cb": scenario_bench_cb, "gait": scenario_gait, "gait_swap": scenario_gait_swap}


def run():
    fn = SCENARIOS[SCENARIO]
    H.Runner(NAME, fn).start()


run()
