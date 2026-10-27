# The extra ALS takes: the other overlay modes, aiming (rotate in place, moving), several montage slots playing at once, slow motion / frames of different length.
#
# Loaded by g_record.py, which adds these takes to the driver of G:\DCC\AlsTruth\tools\r1_record.py the way it adds the ragdoll and the moving-base takes (the
# routes of the older takes are not touched).  The same driver runs against the original ALS (R1_ORIGINAL=1, host_als.py restore) and the generated one, so both
# record the same routes.  Truth takes: G:\DCC\AlsTruth\extra.
#
# New driver commands (keys of a step):
#   dilation=<x>                  UGameplayStatics::SetGlobalTimeDilation: the world's delta time is x times the fixed step from the next frame on
#   jitter=(lo, hi, seed) | None  every frame a pseudo-random dilation in [lo, hi] (a linear congruential sequence of the seed, the same in both runs): frames of
#                                 every length; None switches it off (dilation=<x> then applies again)
#   slot_anim={...}               PlaySlotAnimationAsDynamicMontage on the mesh's animation instance: asset (a key of SEQUENCES), slot, blend_in, blend_out, rate,
#                                 loops, trigger (blend out trigger time), start
#   overlay=<mode>                the 13 overlay modes (the driver only knew Default, Rifle, Injured)
# Frames of a different fixed length (-fps=30 / -fps=120) are separate editor runs: record_takes.ps1 -Fps.

# ---- the routes -----------------------------------------------------------------------------------------------------------------------------

SEQUENCES = {
    "Look_Up": "/ALS/ALS/Animations/View/A_Als_Look_Up",
    "Look_Down": "/ALS/ALS/Animations/View/A_Als_Look_Down",
    "Idle": "/ALS/ALS/Animations/Base/A_Als_Idle",
    "Rotate_90_Left": "/ALS/ALS/Animations/RotateInPlace/A_Als_Rotate_90_Left",
    "Rotate_90_Right": "/ALS/ALS/Animations/RotateInPlace/A_Als_Rotate_90_Right",
    "Stand_Transition_Left": "/ALS/ALS/Animations/Transitions/A_Als_Stand_Transition_Left",
    "Stand_Transition_Right": "/ALS/ALS/Animations/Transitions/A_Als_Stand_Transition_Right",
    "Rifle_Run_Arms": "/ALS/ALS/Animations/Overlays/Rifle/A_Als_Rifle_Run_Arms",
}

OVERLAYS_NEW = ["Masculine", "Feminine", "HandsTied", "PistolOneHanded", "PistolTwoHanded", "Bow", "Torch", "Binoculars", "Box", "Barrel"]


def S(name, dur, at=None, **cmd):
    return (name, dur, cmd, at or [])


def overlay_tour(mode):
    """One overlay mode: set while standing, walk, run, sprint, stop, strafe, crouch, crouch walk, stand, jump."""
    p = "ov_%s_" % mode
    return [
        S(p + "idle", 1.5, overlay=mode, move=(0, 0), stance="Standing", gait="Running"),
        S(p + "walk", 1.5, gait="Walking", move=(1, 0)),
        S(p + "run", 1.5, gait="Running"),
        S(p + "sprint", 1.2, gait="Sprinting"),
        S(p + "stop", 1.2, move=(0, 0), gait="Running"),
        S(p + "strafe", 1.2, move=(0, 1)),
        S(p + "stop2", 0.8, move=(0, 0)),
        S(p + "crouch", 1.2, stance="Crouching"),
        S(p + "crouch_walk", 1.2, gait="Walking", move=(1, 0)),
        S(p + "stand", 1.0, stance="Standing", move=(0, 0)),
        S(p + "jump", 2.0, jump=True),
    ]


def overlay_aim(mode):
    """One overlay mode with the aiming flag: the rotation mode follows the flag; rotate in place, walk, strafe, crouch, rotate crouched."""
    p = "oa_%s_" % mode
    return [
        S(p + "aim_on", 1.5, overlay=mode, aiming=True, move=(0, 0), stance="Standing", gait="Running"),
        S(p + "yaw", 2.0, yaw_delta=70.0, yaw_time=0.35),
        S(p + "walk", 1.5, gait="Walking", move=(1, 0)),
        S(p + "strafe", 1.2, move=(0, 1)),
        S(p + "stop", 0.8, move=(0, 0)),
        S(p + "crouch", 1.5, stance="Crouching"),
        S(p + "crouch_yaw", 2.0, yaw_delta=-100.0, yaw_time=0.5),
        S(p + "stand", 1.0, stance="Standing"),
        S(p + "aim_off", 1.0, aiming=False),
    ]


def build_routes():
    r = {}
    r["OVERLAYS_A"] = [S("idle", 1.5)] + sum((overlay_tour(m) for m in ["Masculine", "Feminine", "Injured", "HandsTied", "PistolOneHanded", "PistolTwoHanded"]), []) \
        + [S("end", 1.0, overlay="Default", move=(0, 0))]
    switch = [S("sw_run", 1.5, overlay="Default", move=(1, 0), gait="Running")]
    for i, m in enumerate(["Rifle", "Bow", "Torch", "Binoculars", "Box", "Barrel", "PistolOneHanded", "Masculine", "Default"]):
        switch.append(S("sw_%d_%s" % (i, m), 1.0, overlay=m))
    r["OVERLAYS_B"] = [S("idle", 1.5)] + sum((overlay_tour(m) for m in ["Bow", "Torch", "Binoculars", "Box", "Barrel"]), []) + switch + [S("end", 1.5, move=(0, 0))]
    r["AIM_OVERLAYS"] = [S("idle", 1.5)] + sum((overlay_aim(m) for m in ["Rifle", "PistolOneHanded", "PistolTwoHanded", "Bow", "Torch", "Binoculars"]), []) \
        + [S("end", 1.0, overlay="Default")]

    steps = [S("idle", 1.5, rotmode="Aiming")]
    for yaw in (20.0, 45.0, 100.0, -30.0, -120.0, 170.0, -170.0, 0.0):          # instant steps of the view: below / above the thresholds, a wrap
        steps.append(S("aim_yaw_%d" % int(yaw), 2.5, yaw=yaw))
    steps += [S("aim_rate_30", 4.0, yaw_rate=30.0), S("aim_rate_m90", 3.0, yaw_rate=-90.0), S("aim_rate_200", 2.5, yaw_rate=200.0),
              S("aim_rate_360", 2.0, yaw_rate=360.0), S("aim_rate_0", 2.0, yaw_rate=0.0),
              S("aim_ramp_fast", 2.5, yaw_delta=150.0, yaw_time=0.3), S("aim_ramp_slow", 4.0, yaw_delta=-100.0, yaw_time=3.5),
              S("aim_sin", 5.0, yaw_sin=(80.0, 0.4)), S("aim_sin_off", 2.0, yaw_sin=(0.0, 0.0)),
              S("aim_pitch_down", 2.5, pitch_to=-50.0, pitch_time=1.5), S("aim_pitch_up", 3.0, pitch_to=50.0, pitch_time=2.0), S("aim_pitch_0", 1.5, pitch_to=0.0, pitch_time=1.0),
              S("crouch_aim", 1.5, stance="Crouching")]
    for yaw in (60.0, -90.0, 150.0, 0.0):
        steps.append(S("crouch_yaw_%d" % int(yaw), 2.5, yaw=yaw))
    steps += [S("crouch_rate", 4.0, yaw_rate=-70.0), S("crouch_rate_0", 1.5, yaw_rate=0.0), S("stand_aim", 1.5, stance="Standing"),
              S("fp_aim", 1.5, viewmode="FirstPerson")]
    for yaw in (50.0, -80.0, 160.0):
        steps.append(S("fp_yaw_%d" % int(yaw), 2.5, yaw=yaw))
    steps += [S("fp_rate", 3.0, yaw_rate=120.0), S("fp_rate_0", 1.5, yaw_rate=0.0), S("tp_back", 1.5, viewmode="ThirdPerson")]
    for mode in ("Rifle", "PistolOneHanded", "Bow"):
        steps.append(S("ov_%s" % mode, 1.5, overlay=mode))
        for yaw in (90.0, -90.0):
            steps.append(S("ov_%s_yaw_%d" % (mode, int(yaw)), 2.5, yaw=yaw))
    steps += [S("ov_default", 1.5, overlay="Default"), S("aim_flag_vel", 2.0, rotmode="VelocityDirection", aiming=True), S("aim_flag_yaw", 2.5, yaw=-40.0), S("aim_flag_off", 2.0, aiming=False, rotmode="ViewDirection"), S("end", 1.0)]
    r["AIMING_ROTATE"] = steps

    r["AIMING_MOVES"] = [
        S("idle", 1.0, rotmode="Aiming"), S("walk", 2.0, gait="Walking", move=(1, 0)),
        S("walk_circle", 5.0, yaw_rate=45.0), S("walk_circle_back", 4.0, yaw_rate=-90.0), S("walk_circle_0", 1.0, yaw_rate=0.0),
        S("run", 2.0, gait="Running"), S("run_circle", 4.0, yaw_rate=120.0), S("run_circle_0", 1.0, yaw_rate=0.0),
        S("run_strafe_r", 2.0, move=(0, 1)), S("run_strafe_l", 2.0, move=(0, -1)), S("run_back", 2.0, move=(-1, 0)), S("run_diag", 2.0, move=(1, 1)), S("run_diag_b", 2.0, move=(-1, 1)),
        S("sprint_request", 2.5, gait="Sprinting", move=(1, 0)), S("stop", 1.5, move=(0, 0), gait="Running"),
        S("stop_go", 5.0, at=[(0.5, {"move": (1, 0)}), (1.0, {"move": (0, 0)}), (1.5, {"move": (0, 1)}), (2.5, {"move": (0, 0)}), (3.0, {"move": (-1, 0)}), (3.6, {"move": (1, 0)})]),
        S("yaw_while_walking", 6.0, gait="Walking", move=(1, 0), at=[(0.5, {"yaw_add": 90.0}), (2.0, {"yaw_add": -170.0}), (3.5, {"yaw_add": 120.0}), (4.5, {"yaw_add": 60.0})]),
        S("jump_aim", 3.0, gait="Running", jump=True), S("jump_aim_yaw", 3.5, at=[(0.2, {"jump": True}), (0.4, {"yaw_to": 130.0, "yaw_time": 0.5})]),
        S("crouch_walk", 2.5, stance="Crouching", gait="Walking"), S("crouch_circle", 3.0, yaw_rate=-60.0), S("crouch_circle_0", 0.5, yaw_rate=0.0),
        S("stand", 1.0, stance="Standing", move=(0, 0)), S("roll_aim", 3.0, roll=True), S("run_roll", 1.5, move=(1, 0), gait="Running"), S("roll_aim_run", 3.0, roll=True),
        S("stop2", 1.5, move=(0, 0)),
        S("rifle", 1.5, overlay="Rifle", move=(1, 0), gait="Walking"), S("rifle_circle", 4.0, yaw_rate=70.0), S("rifle_circle_0", 1.0, yaw_rate=0.0),
        S("fp", 1.5, viewmode="FirstPerson"), S("fp_run", 2.5, gait="Running"), S("fp_circle", 3.0, yaw_rate=-100.0), S("fp_circle_0", 1.0, yaw_rate=0.0),
        S("tp", 1.5, viewmode="ThirdPerson", overlay="Default"), S("end", 2.0, move=(0, 0)),
    ]

    def sa(asset, slot, **kw):
        d = {"asset": asset, "slot": slot}
        d.update(kw)
        return {"slot_anim": d}

    r["MONTAGE_SLOTS"] = [
        S("idle", 1.5),
        S("idle_slots", 4.0, at=[(0.2, sa("Stand_Transition_Left", "Transition")), (0.5, sa("Rotate_90_Right", "PostLocomotion")), (0.9, sa("Look_Up", "Head")),
                                  (1.3, sa("Idle", "Pelvis")), (1.6, sa("Idle", "ArmLeft", rate=1.5)), (2.0, sa("Look_Down", "Spine"))]),
        S("run", 2.0, gait="Running", move=(1, 0)),
        S("run_slots", 4.0, at=[(0.2, sa("Stand_Transition_Right", "Transition", blend_in=0.0, blend_out=0.5)), (0.4, sa("Look_Down", "Head", loops=2)),
                                 (0.7, sa("Rotate_90_Left", "PostLocomotion", rate=2.0)), (1.5, sa("Idle", "Legs", start=0.3)), (2.0, sa("Rifle_Run_Arms", "ArmRight"))]),
        S("same_slot", 4.0, at=[(0.2, sa("Stand_Transition_Left", "Transition")), (0.5, sa("Stand_Transition_Right", "Transition")), (0.8, sa("Look_Up", "Head")),
                                 (1.0, sa("Look_Down", "Head", blend_in=0.1))]),
        S("turn_with_slots", 5.0, move=(0, 0), at=[(0.1, sa("Look_Up", "Head", rate=0.5)), (0.3, {"yaw_delta": 120.0, "yaw_time": 0.3}), (1.5, sa("Idle", "Spine")),
                                                   (2.5, {"yaw_delta": -170.0, "yaw_time": 0.4})]),
        S("stop_with_slots", 3.0, move=(1, 0), at=[(0.3, {"move": (0, 0)}), (0.35, sa("Look_Down", "Head")), (0.4, sa("Stand_Transition_Left", "PostLocomotion"))]),
        S("roll_slots", 4.0, at=[(0.0, {"roll": True}), (0.2, sa("Look_Up", "Head")), (0.5, sa("Idle", "ArmLeft")), (0.8, sa("Rotate_90_Right", "PostLocomotion"))]),
        S("run2", 2.0, move=(1, 0), gait="Running"),
        S("roll_run_slots", 4.0, at=[(0.0, {"roll": True}), (0.4, sa("Look_Down", "Head", loops=3)), (1.0, sa("Idle", "Transition"))]),
        S("crouch", 1.5, stance="Crouching", move=(0, 0)),
        S("crouch_slots", 4.0, at=[(0.2, sa("Look_Up", "Head")), (0.5, sa("Idle", "TurnInPlaceCrouching")), (0.9, {"yaw_delta": 110.0, "yaw_time": 0.3})]),
        S("stand", 1.5, stance="Standing"),
        S("jump_slots", 3.5, jump=True, at=[(0.3, sa("Look_Up", "Head")), (0.5, sa("Idle", "ArmRight")), (0.7, sa("Idle", "Legs"))]),
        S("overlay_slots", 4.0, overlay="Rifle", at=[(0.2, sa("Rifle_Run_Arms", "ArmLeft", loops=2)), (1.0, {"overlay": "Default"}), (1.4, sa("Look_Down", "Head"))]),
        S("end", 2.0),
    ]

    # slow motion and frames of different length: the world's delta time is dilation * the fixed step
    r["SLOWMO"] = [
        S("idle", 1.0), S("run", 2.5, gait="Running", move=(1, 0)),
        S("half", 3.0, dilation=0.5), S("quarter", 3.0, dilation=0.25), S("tenth", 1.5, dilation=0.1),
        S("slow_turn", 3.0, dilation=0.25, yaw_delta=110.0, yaw_time=0.3), S("slow_jump", 3.0, jump=True),
        S("slow_stop", 2.0, move=(0, 0)), S("slow_crouch", 2.0, stance="Crouching"), S("slow_crouch_walk", 2.0, move=(1, 0), gait="Walking"),
        S("slow_stand", 2.0, stance="Standing", move=(0, 0)), S("slow_roll", 2.5, roll=True), S("slow_overlay", 2.0, overlay="Rifle", move=(1, 0), gait="Running"),
        S("normal", 2.0, dilation=1.0, overlay="Default"),
        S("fast2", 3.0, dilation=2.0), S("fast2_turn", 2.5, yaw_delta=-150.0, yaw_time=0.4), S("fast2_jump", 4.0, jump=True), S("fast2_stop", 2.0, move=(0, 0)),
        S("fast3", 2.0, dilation=3.0, move=(1, 0), gait="Sprinting"), S("fast3_jump", 3.0, jump=True), S("fast3_stop", 2.0, move=(0, 0)),
        S("normal2", 1.5, dilation=1.0),
        S("jitter_run", 6.0, jitter=(0.5, 1.8, 1234), move=(1, 0), gait="Running", at=[(1.0, {"jump": True}), (3.0, {"yaw_delta": 140.0, "yaw_time": 0.3}), (4.5, {"stance": "Crouching"})]),
        S("jitter_stop", 3.0, move=(0, 0), stance="Standing"), S("jitter_roll", 3.0, roll=True),
        S("jitter_aim", 5.0, jitter=(0.3, 2.5, 99), rotmode="Aiming", at=[(0.5, {"yaw": 100.0}), (2.0, {"move": (1, 0)}), (3.5, {"yaw_rate": 90.0})]),
        S("jitter_end", 2.0, jitter=None, dilation=1.0, rotmode="ViewDirection", yaw_rate=0.0, move=(0, 0)),
    ]

    # a short route over most of the character's states, for the runs at other fixed frame rates
    r["MIX"] = [
        S("idle", 1.5), S("walk", 2.0, gait="Walking", move=(1, 0)), S("run", 2.0, gait="Running"), S("sprint", 2.0, gait="Sprinting"),
        S("stop", 2.0, move=(0, 0)), S("turn_right", 2.0, yaw_delta=110.0, yaw_time=0.4), S("turn_left", 2.0, yaw_delta=-110.0, yaw_time=0.4),
        S("strafe", 2.0, gait="Running", move=(0, 1)), S("back", 2.0, move=(-1, 0)), S("run2", 1.5, move=(1, 0)), S("jump", 2.5, jump=True), S("stop2", 1.5, move=(0, 0)),
        S("crouch", 1.5, stance="Crouching"), S("crouch_walk", 2.0, gait="Walking", move=(1, 0)), S("stand", 1.5, stance="Standing", move=(0, 0)),
        S("roll", 3.0, roll=True), S("run3", 1.5, move=(1, 0), gait="Running"), S("roll_run", 3.0, roll=True),
        S("stop3", 1.5, move=(0, 0)), S("aim", 1.5, rotmode="Aiming"), S("aim_yaw", 3.0, yaw=-100.0), S("aim_walk", 2.5, gait="Walking", move=(1, 0), yaw_rate=60.0),
        S("aim_stop", 1.5, move=(0, 0), yaw_rate=0.0, rotmode="ViewDirection"), S("rifle", 2.0, overlay="Rifle"), S("rifle_run", 2.0, move=(1, 0), gait="Running"),
        S("default", 1.5, overlay="Default", move=(0, 0)), S("end", 1.5),
    ]
    return r


ROUTES = build_routes()

# ---- mantling every kind of ledge: boxes in a row along +X (the platform machinery of base_takes.py spawns them in the editor world, never saved) ----------------
# AlsMantlingSettingsKind: Low (a ledge under the high threshold), High, LowBox (Low with the Box overlay), HighInAir (the auto mantle while falling).  The route runs
# at each box in turn (a step whose name ends in `_mantle` tries StartMantling every frame from 0.2 s into it, like the `mantle` take), over the top and on to the next.
MANTLE_BOXES = [("A", 180.0, 70.0), ("B", 800.0, 115.0), ("C", 1400.0, 160.0), ("D", 2000.0, 210.0), ("E", 2600.0, 80.0), ("F", 3200.0, 105.0), ("G", 3800.0, 280.0)]
MANTLE_VARIANTS_ROUTE = """
MANTLE_VARIANTS = [
    step("idle", 1.5),
    step("A_low70_mantle", 3.0, move=(1, 0), gait="Running", mantle_try=True),
    step("A_over", 2.0),
    step("B_low115_mantle", 3.0, mantle_try=True),
    step("B_over", 2.0),
    step("C_high160_mantle", 3.5, mantle_try=True),
    step("C_over", 2.0),
    step("D_high210_mantle", 3.5, mantle_try=True),
    step("D_over", 2.0),
    step("E_box80_mantle", 3.0, overlay="Box", mantle_try=True),
    step("E_over", 2.0, overlay="Default"),
    step("F_inair105", 5.0, at=[(0.2, {"jump": True}), (1.0, {"jump": True}), (1.8, {"jump": True}), (2.6, {"jump": True})]),
    step("F_over", 2.0),
    step("G_too_high_mantle", 3.0, mantle_try=True),
    step("end", 1.5, move=(0, 0)),
]

"""


def _register_mantle_variants():
    import base_takes as B
    platforms = [B.box("R1B_Ledge" + n, (front + 150.0, 0.0, h / 2.0), (150.0, 300.0, h / 2.0), mover=False) for n, front, h in MANTLE_BOXES]
    B.TAKES["mantle_variants"] = {"desc": "mantling ledges of 70 / 115 (Low), 160 / 210 (High), 80 with the Box overlay (LowBox), in the air, and one too high", "platforms": platforms}
    B.ROUTES["mantle_variants"] = "MANTLE_VARIANTS"
    if "MANTLE_VARIANTS = [" not in B.STEPS:
        B.STEPS += MANTLE_VARIANTS_ROUTE


_register_mantle_variants()

# take name -> (route, description, run's fixed fps)
TAKES = {
    "overlays_a": ("OVERLAYS_A", "overlay modes Masculine / Feminine / Injured / HandsTied / PistolOneHanded / PistolTwoHanded: a tour of the ground states each", 60),
    "overlays_b": ("OVERLAYS_B", "overlay modes Bow / Torch / Binoculars / Box / Barrel: the tour, then switching overlays while running", 60),
    "aim_overlays": ("AIM_OVERLAYS", "aiming (the flag) with Rifle / PistolOneHanded / PistolTwoHanded / Bow / Torch / Binoculars", 60),
    "aiming_rotate": ("AIMING_ROTATE", "the Aiming rotation mode standing and crouched: view steps and sweeps, rotate in place, first person, overlays", 60),
    "aiming_moves": ("AIMING_MOVES", "the Aiming rotation mode while moving: walk / run / strafe / sprint request, view sweeps beyond the aiming limit, jump, crouch, roll", 60),
    "montage_slots": ("MONTAGE_SLOTS", "dynamic montages in several slots at once (and two in the same slot), with turns, stops, rolls, jumps and overlay changes", 60),
    "slowmo": ("SLOWMO", "global time dilation 0.1 .. 3 and frames of random length", 60),
    "fps30_mix": ("MIX", "the mixed route at a fixed 30 fps", 30),
    "fps120_mix": ("MIX", "the mixed route at a fixed 120 fps", 120),
}


def _src(name, route):
    lines = ["%s = [" % name]
    for sname, dur, cmd, at in route:
        args = ", ".join("%s=%r" % (k, v) for k, v in cmd.items())
        at_s = ""
        if at:
            at_s = ", at=[%s]" % ", ".join("(%r, %r)" % (t, c) for t, c in at)
        lines.append("    step(%r, %r%s%s)," % (sname, dur, at_s, (", " + args) if args else ""))
    lines.append("]")
    return "\n".join(lines) + "\n"


def routes_source():
    nl = "\n"
    return nl + "".join(_src(n, r) + nl for n, r in ROUTES.items())


# ---- the driver source ----------------------------------------------------------------------------------------------------------------

def _replace_once(src, old, new):
    if src.count(old) != 1:
        raise RuntimeError("r1_record.py has changed: anchor not found once: " + old[:70])
    return src.replace(old, new)


def add_als_takes(src):
    """The driver source with the extra takes, the extra tags and the commands dilation / jitter / slot_anim (each anchor must be there once; the ragdoll and the
    moving-base takes are added first)."""
    nl = chr(10)
    entries = "".join('    "%s": (%s, %r),' % (t, r, d) + nl for t, (r, d, _fps) in TAKES.items())
    src = _replace_once(src, "SCRIPTED = {" + nl, routes_source() + "SEQUENCES = %r" % SEQUENCES + nl + nl + "SCRIPTED = {" + nl)
    anchor = '    "ragdoll": (RAGDOLL, "ragdolling from a run, at rest and in the air, with the get up"),' + nl
    src = _replace_once(src, anchor, anchor + entries)
    # the tags of the other overlay modes
    src = _replace_once(src, '"Als.ViewMode.ThirdPerson", "Als.ViewMode.FirstPerson"):',
                        '"Als.ViewMode.ThirdPerson", "Als.ViewMode.FirstPerson", ' + ", ".join('"Als.OverlayMode.%s"' % m for m in OVERLAYS_NEW) + "):")
    # the commands
    apply_anchor = '        if c.get("jump"):' + nl + "            ch.jump()" + nl
    new_cmds = (
        '        if "dilation" in c:' + nl
        + '            state["jitter"] = None' + nl
        + '            unreal.GameplayStatics.set_global_time_dilation(world, float(c["dilation"]))' + nl
        + '            cmd("time_dilation", value=float(c["dilation"]))' + nl
        + '        if "jitter" in c and "dilation" not in c:' + nl
        + '            j = c["jitter"]' + nl
        + '            state["jitter"] = None if j is None else {"lo": j[0], "hi": j[1], "x": int(j[2]) & 0x7fffffff}' + nl
        + '            cmd("time_dilation_jitter", enabled=j is not None, lo=(j[0] if j else 0.0), hi=(j[1] if j else 0.0), seed=(j[2] if j else 0))' + nl
        + '        if "slot_anim" in c:' + nl
        + '            sa = c["slot_anim"]' + nl
        + '            seq = unreal.load_asset(SEQUENCES[sa["asset"]])' + nl
        + '            mesh.get_anim_instance().play_slot_animation_as_dynamic_montage(seq, sa["slot"], sa.get("blend_in", 0.25), sa.get("blend_out", 0.25),' + nl
        + '                                                                             sa.get("rate", 1.0), sa.get("loops", 1), sa.get("trigger", -1.0), sa.get("start", 0.0))' + nl
        + '            cmd("play_slot_animation", asset=sa["asset"], slot=sa["slot"], blend_in=sa.get("blend_in", 0.25), blend_out=sa.get("blend_out", 0.25),' + nl
        + '                rate=sa.get("rate", 1.0), loops=sa.get("loops", 1), trigger=sa.get("trigger", -1.0), start=sa.get("start", 0.0))' + nl
    )
    src = _replace_once(src, apply_anchor, new_cmds + apply_anchor)
    # the jitter: a dilation for the next frame, every frame
    drive_anchor = '        state["t"] += dt' + nl + '        if s["cmd"].get("mantle_try")'
    jitter = (
        '        state["t"] += dt' + nl
        + '        if state.get("jitter"):' + nl
        + '            jt = state["jitter"]' + nl
        + '            jt["x"] = (jt["x"] * 1103515245 + 12345) & 0x7fffffff' + nl
        + '            unreal.GameplayStatics.set_global_time_dilation(world, jt["lo"] + (jt["hi"] - jt["lo"]) * ((jt["x"] >> 8) / float(1 << 23)))' + nl
        + '        if s["cmd"].get("mantle_try")'
    )
    src = _replace_once(src, drive_anchor, jitter)
    return src
