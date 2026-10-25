"""T3: the contract between the ALS animation Blueprints (.dab) and als/AlsAnimationInstance.dal (als-dal-rewrite.md 1.8, 7.2 item 5).

A .dab (``Parent = AlsAnimationInstance``) reads ``Main.GroundedState.VelocityBlend.ForwardAmount`` and calls ``Call(Self.RefreshGrounded)``,
``Main.PlayTransitionAnimation(Sequence = ..., PlayRate = ...)`` and ``Self.GetControlRigInput()``.  This test scans the 26 ALS corpus
files and checks the instance against what they use:

* the 82 state paths (22 roots) all exist in the exported state, with the type the ALS C++ header gives them
  (``RagdollingState.FinalRagdollPose`` is a ``FPoseSnapshot`` in C++ and the ``snapshot`` value type here);
* every ``Call`` target is an ``entry`` with the parameters (names, types, defaults) of the ALS function, the 17 of them the linked
  instance forwards are marked ``linked`` and ``AlsLinkedAnimationInstance.dal`` forwards exactly those;
* ``GetControlRigInput`` is a ``pub view`` returning the struct the Control Rig reads, member for member (21 scalar leaves);
* when the machine has them: the scan agrees with the DreamVEd .dab parser, and the tables below agree with the ALS C++ headers.

The corpus is read only.  Set DAL_DAB_CORPUS / DAL_ALS_SOURCE to point at other copies.
"""

import ast
import glob
import os
import re
import sys
import unittest

import _bootstrap  # noqa: F401

import als_sources as A
import dal
from dal import harness
from dal.pybackend import generate_python, load_class
from dal.types import EnumType, StructType

CORPUS = os.environ.get("DAL_DAB_CORPUS", r"I:\UnrealProject_Moon\DEV_58\DevTest\Plugins\DreamAnim\Tests\Corpus\Decompiled")
ALS_SOURCE = os.environ.get("DAL_ALS_SOURCE", r"I:\UnrealProject_Moon\DEV_58\DevTest\Plugins\ALS-Refactored\Source\ALS")
DREAMVED_MODULES = r"I:\DCC\blender-moon-engine\scripts\modules"
CONTROLRIG_NODE = os.path.join(DREAMVED_MODULES, "dreamved", "anim", "runtime", "controlrig_node.py")

HAVE_CORPUS = os.path.isdir(CORPUS) and bool(glob.glob(os.path.join(CORPUS, "*.dab")))
HAVE_ALS_SOURCE = os.path.isfile(os.path.join(ALS_SOURCE, "Public", "AlsAnimationInstance.h"))

INSTANCE_PARENTS = ("AlsAnimationInstance", "AlsLinkedAnimationInstance")

# ---- what the .dab read: the 82 paths and their ALS types -----------------------------------------------------------------------------
# A path is a float unless it is listed here.  (The classes come from the State/*.h headers; a test below checks them against the headers.)
BOOLS = {
    "GroundedState.MovementDirection.bBackward", "GroundedState.MovementDirection.bForward", "GroundedState.MovementDirection.bLeft",
    "GroundedState.MovementDirection.bRight", "InAirState.bJumped", "LocomotionState.bHasInput", "LocomotionState.bMoving",
    "LocomotionState.bMovingSmooth", "MovementBase.bHasRelativeLocation", "RotateInPlaceState.bRotatingLeft", "RotateInPlaceState.bRotatingRight",
    "StandingState.bPivotActive", "TransitionsState.bTransitionsAllowed",
}
TAGS = {"Gait": "AlsGait", "GroundedEntryMode": "AlsGroundedEntryMode", "LocomotionAction": "AlsLocomotionAction", "LocomotionMode": "AlsLocomotionMode",
        "RotationMode": "AlsRotationMode", "Stance": "AlsStance"}
ENUMS = {"GroundedState.HipsDirection": "AlsHipsDirection"}
POSE_SNAPSHOT = "RagdollingState.FinalRagdollPose"

# Paths a .dab reads that the instance cannot export (none since the snapshot value type and the snapshot_pose command of 0.4.0).
KNOWN_GAPS: dict = {}


def expected_type(path):
    if path in TAGS:
        return TAGS[path]
    if path in ENUMS:
        return ENUMS[path]
    if path in BOOLS:
        return "bool"
    if path == POSE_SNAPSHOT:
        return "snapshot"
    return "float"


# ---- the entries: ALS's UFUNCTIONs (AlsAnimationInstance.h) ---------------------------------------------------------------------------
# name -> [(parameter, DAL type, default)]; a default of ... means "no default".  SetHipsDirection has none in C++ (an enum pin is its first
# member when it is not wired), which is what a .dab that leaves it out gets.
NO_DEFAULT = ...
FADE = [("BlendInDuration", "float", 0.2), ("BlendOutDuration", "float", 0.2), ("PlayRate", "float", 1.0), ("StartTime", "float", 0.0),
        ("bFromStandingIdleOnly", "bool", False)]
ENTRIES = {
    "InitializeHead": [], "RefreshHead": [], "InitializeLean": [], "InitializeGrounded": [], "RefreshGrounded": [], "ResetGroundedEntryMode": [],
    "RefreshGroundedMovement": [], "SetHipsDirection": [("NewHipsDirection", "AlsHipsDirection", "Forward")],
    "InitializeStandingMovement": [], "RefreshStandingMovement": [], "ActivatePivot": [], "ResetPivot": [], "RefreshCrouchingMovement": [],
    "RefreshInAir": [], "PlayQuickStopAnimation": [],
    "PlayTransitionAnimation": [("Sequence", "sequence", NO_DEFAULT)] + FADE,
    "PlayTransitionLeftAnimation": list(FADE), "PlayTransitionRightAnimation": list(FADE),
    "StopTransitionAndTurnInPlaceAnimations": [("BlendOutDuration", "float", -1.0)],
    "RefreshDynamicTransitions": [], "RefreshRotateInPlace": [], "InitializeTurnInPlace": [], "RefreshTurnInPlace": [],
}
# the 17 functions of UAlsLinkedAnimationInstance (LI:58-192): the ones Call(Self.X) reaches on a linked instance
LINKED = ["InitializeHead", "RefreshHead", "InitializeLean", "InitializeGrounded", "RefreshGrounded", "ResetGroundedEntryMode", "RefreshGroundedMovement",
          "SetHipsDirection", "InitializeStandingMovement", "RefreshStandingMovement", "ResetPivot", "RefreshCrouchingMovement", "RefreshInAir",
          "RefreshDynamicTransitions", "RefreshRotateInPlace", "InitializeTurnInPlace", "RefreshTurnInPlace"]
# the six called as Main.X(...) / Self.X(...) with arguments or as a state / notify function (not through Call), and GetControlRigInput
PARAMETERISED = ["PlayTransitionAnimation", "PlayTransitionLeftAnimation", "PlayTransitionRightAnimation", "StopTransitionAndTurnInPlaceAnimations",
                 "PlayQuickStopAnimation", "ActivatePivot"]

# the struct of the ControlRig node's RigInput pin (controlrig_node.STRUCT_SCHEMAS["AlsControlRigInput"]); b bool, f float, v vector, q quaternion
RIG_SCHEMA = {
    "bUseHandIkBones": "b", "bUseFootIkBones": "b", "bFootTransformsValid": "b", "VelocityBlendForwardAmount": "f", "VelocityBlendBackwardAmount": "f",
    "SpineYawAngle": "f", "PelvisOffsetAmount": "f", "FootLeftLocation": "v", "FootLeftRotation": "q", "FootRightLocation": "v", "FootRightRotation": "q",
}


# ---- the corpus scan -------------------------------------------------------------------------------------------------------------------------

TOKEN = re.compile(r"\b(Self\.GetParent\(\)|Main|Self)((?:\.[A-Za-z_]\w*(?:\(\))?)+)")


def balanced(text, open_at):
    """The text of the parentheses that open at ``open_at`` (without them)."""
    depth, i = 0, open_at
    while i < len(text):
        if text[i] == "(":
            depth += 1
        elif text[i] == ")":
            depth -= 1
            if depth == 0:
                return text[open_at + 1:i]
        i += 1
    raise ValueError("unbalanced parentheses")


def named_args(arg_text):
    """The ``Name`` of every ``Name = value`` at the top level of a call's argument text (``On`` is the node's own)."""
    out, depth, start = [], 0, 0
    parts = []
    for i, c in enumerate(arg_text):
        if c in "([{":
            depth += 1
        elif c in ")]}":
            depth -= 1
        elif c == "," and depth == 0:
            parts.append(arg_text[start:i])
            start = i + 1
    parts.append(arg_text[start:])
    for part in parts:
        m = re.match(r"\s*(\w+)\s*=\s*(.+)$", part, re.S)
        if m and m.group(1) != "On":
            out.append((m.group(1), m.group(2).strip()))
    return out


def scan_corpus(directory=CORPUS):
    """``(paths, calls)``: ``paths`` is ``{state path: {file}}`` over the files of the instance classes (monolithic file included), ``calls`` is
    ``[(function, file, via, args)]`` with ``via`` 'Call' for ``Call(Self.F, ...)`` and 'direct' for ``Main.F(...)`` / ``Self.F(...)`` /
    ``Self.GetParent().F(...)``; ``args`` is ``[(name, value text)]``."""
    paths, calls = {}, []
    for f in sorted(glob.glob(os.path.join(directory, "*.dab"))):
        with open(f, encoding="utf-8") as fh:
            text = fh.read()
        parent = re.search(r"Parent\s*=\s*(\w+)", text).group(1)
        if parent not in INSTANCE_PARENTS:
            continue
        base = os.path.basename(f)
        for m in TOKEN.finditer(text):
            segs = m.group(2)[1:].split(".")
            nxt = text[m.end():m.end() + 1]
            if segs[-1].endswith("()"):
                if segs[-1] == "GetParent()" and len(segs) == 1:
                    continue                                                    # the alias: Main = Self.GetParent();
                calls.append((segs[-1][:-2], base, "direct", []))                # Main.ActivatePivot(): no arguments
                continue
            if nxt == "(" and len(segs) == 1:
                calls.append((segs[0], base, "direct", named_args(balanced(text, m.end()))))
                continue
            before = text[max(0, m.start() - 5):m.start()]
            if len(segs) == 1 and before.endswith("Call("):
                call_text = balanced(text, m.start() - 1)
                calls.append((segs[0], base, "Call", named_args(call_text[call_text.index(",") + 1:] if "," in call_text else "")))
                continue
            paths.setdefault(".".join(segs), set()).add(base)
    return paths, calls


class CorpusScanTests(unittest.TestCase):
    """The scan itself: what the .dab files contain, as the numbers of als-dal-rewrite.md 1.8."""

    @classmethod
    def setUpClass(cls):
        if not HAVE_CORPUS:
            raise unittest.SkipTest("the ALS .dab corpus is not on this machine")
        cls.paths, cls.calls = scan_corpus()

    def test_82_state_paths_under_22_roots(self):
        self.assertEqual(len(self.paths), 82, sorted(self.paths))
        self.assertEqual(len({p.split(".")[0] for p in self.paths}), 22)

    def test_the_split_blueprints_read_the_same_paths_as_the_monolith(self):
        without = {p for p, files in self.paths.items() if files - {"Character_AB_Als_Monolithic.dab"}}
        self.assertEqual(without, set(self.paths))                              # the monolithic copy adds nothing

    def test_38_call_nodes_to_17_functions(self):
        sites = [c for c in self.calls if c[2] == "Call" and c[1] != "Character_AB_Als_Monolithic.dab"]
        self.assertEqual(len(sites), 38)
        self.assertEqual(sorted({c[0] for c in sites}), sorted(LINKED))
        # every Call target is one the linked instance forwards
        self.assertEqual({c[0] for c in self.calls if c[2] == "Call"}, set(LINKED))

    def test_six_parameterised_functions_and_the_rig_getter(self):
        direct = {c[0] for c in self.calls if c[2] == "direct"}
        self.assertEqual(direct, set(PARAMETERISED) | {"GetControlRigInput"})

    def test_the_scan_agrees_with_the_dreamved_parser(self):
        if not os.path.isdir(DREAMVED_MODULES):
            self.skipTest("blender-moon-engine is not on this machine")
        sys.path.insert(0, DREAMVED_MODULES)
        try:
            from dreamved.anim import dab
            from dreamved.anim.graph_build import var_key
        except Exception as e:                                                  # numpy missing, layout changed ...
            self.skipTest(f"the DreamVEd .dab parser cannot be imported: {e}")
        finally:
            sys.path.remove(DREAMVED_MODULES)
        read, sites = set(), set()
        for f in sorted(glob.glob(os.path.join(CORPUS, "*.dab"))):
            with open(f, encoding="utf-8") as fh:
                parent = re.search(r"Parent\s*=\s*(\w+)", fh.read()).group(1)
            if parent not in INSTANCE_PARENTS:
                continue
            model = dab.DabFile.load(f).model()
            read |= {var_key(v.split(".")) for v in model.variables_read()}
            for cs in model.call_sites:
                if cs.function != "GetParent":
                    sites.add((cs.function, tuple(sorted(str(a[0]) for a in cs.args if a[0] not in (None, "On")))))
        self.assertEqual(read - {"GetControlRigInput"}, set(self.paths))        # GetControlRigInput() is read as a variable by the parser
        mine = {(c[0], tuple(sorted(n for n, _ in c[3]))) for c in self.calls if c[0] != "GetControlRigInput"}
        for function, args in mine:
            self.assertIn((function, args), sites, f"{function}{args} is not a call site for the DreamVEd parser")


# ---- the instance -------------------------------------------------------------------------------------------------------------------------

def flatten(module):
    """``{dotted path: leaf Type}`` of the exported state of a module (struct members flattened, as ``snapshot()`` does)."""
    out = {}

    def walk(prefix, ty):
        if isinstance(ty, StructType):
            for f in ty.fields:
                walk(prefix + "." + f.name, f.ty)
        else:
            out[prefix] = ty

    for s in module.states:
        walk(s.name, s.ty)
    return out


def type_name(ty):
    return ty.name


class ContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if not A.als_files_present():
            raise unittest.SkipTest("als/*.dal is not there")
        cls.mods = A.compile_system(linked=True)
        cls.inst = cls.mods[A.INSTANCE]
        cls.linked = cls.mods[A.LINKED]
        cls.state = flatten(cls.inst)
        cls.paths = scan_corpus()[0] if HAVE_CORPUS else None

    def contract_paths(self):
        if self.paths is None:
            self.skipTest("the ALS .dab corpus is not on this machine")
        return self.paths

    # -- state paths ------------------------------------------------------------------------------------------------------------------------
    def test_every_path_a_dab_reads_is_exported_with_its_type(self):
        paths = self.contract_paths()
        missing, wrong = [], []
        for path in sorted(paths):
            if path in KNOWN_GAPS:
                continue
            ty = self.state.get(path)
            if ty is None:
                missing.append(path)
            elif type_name(ty) != expected_type(path):
                wrong.append((path, type_name(ty), expected_type(path)))
        self.assertEqual(missing, [])
        self.assertEqual(wrong, [])
        self.assertEqual(len(paths) - len(set(KNOWN_GAPS) & set(paths)), 82)         # all of the 82

    def test_the_known_gaps_are_exactly_the_paths_that_are_not_exported(self):
        paths = self.contract_paths()
        not_exported = {p for p in paths if p not in self.state}
        self.assertEqual(not_exported, set(KNOWN_GAPS), "a gap was closed (or opened): update KNOWN_GAPS and the report")

    def test_the_tag_sets_and_the_enum_are_the_ALS_ones(self):
        tags = self.inst.tagsets
        self.assertEqual({t.name: t.prefix for t in tags if t.name in TAGS.values()},
                         {"AlsGait": "Als.Gait", "AlsStance": "Als.Stance", "AlsRotationMode": "Als.RotationMode", "AlsLocomotionMode": "Als.LocomotionMode",
                          "AlsLocomotionAction": "Als.LocomotionAction", "AlsGroundedEntryMode": "Als.GroundedEntryMode"})
        enum = self.state["GroundedState.HipsDirection"]
        self.assertIsInstance(enum, EnumType)
        self.assertEqual(enum.members, ["Forward", "Backward", "LeftForward", "LeftBackward", "RightForward", "RightBackward"])

    def test_state_defaults_match_the_ALS_members(self):
        snap = load_class(self.inst, generate_python(self.inst))(harness.ScriptedHost()).snapshot()
        self.assertEqual(snap["ViewMode"], "Als.ViewMode.ThirdPerson")
        self.assertEqual(snap["LocomotionMode"], "Als.LocomotionMode.Grounded")
        self.assertEqual(snap["RotationMode"], "Als.RotationMode.ViewDirection")
        self.assertEqual(snap["Stance"], "Als.Stance.Standing")
        self.assertEqual(snap["Gait"], "Als.Gait.Walking")
        self.assertEqual(snap["LocomotionAction"], "")
        self.assertEqual(snap["GroundedEntryMode"], "")

    def test_the_tags_are_compared_by_their_full_names_like_a_dab_does(self):
        # Main.Stance == "Als.Stance.Standing" (AB_Als_Grounded): the exported value is the full name, `!IsValid(Main.Stance)` is the empty string
        snap = load_class(self.inst, generate_python(self.inst))(harness.ScriptedHost()).snapshot()
        self.assertEqual(snap["Stance"], "Als.Stance.Standing")
        self.assertEqual(self.inst.tagsets[[t.name for t in self.inst.tagsets].index("AlsStance")].full_name("Standing"), "Als.Stance.Standing")

    # -- entries ------------------------------------------------------------------------------------------------------------------------------
    def test_the_entries_are_exactly_the_ALS_functions(self):
        self.assertEqual(sorted(e.name for e in self.inst.entries), sorted(ENTRIES))

    def test_entry_parameters_have_the_ALS_names_types_and_defaults(self):
        def default_of(p):
            if not p.has_default:
                return NO_DEFAULT
            return p.ty.members[p.default] if isinstance(p.ty, EnumType) else p.default

        for e in self.inst.entries:
            got = [(p.name, p.ty.name, default_of(p)) for p in e.params]
            want = ENTRIES[e.name]
            self.assertEqual(len(got), len(want), e.name)
            for (gn, gt, gd), (wn, wt, wd) in zip(got, want):
                self.assertEqual((gn, gt), (wn, wt), e.name)
                if wd is NO_DEFAULT:
                    self.assertIs(gd, NO_DEFAULT, f"{e.name}.{gn} must not have a default")
                else:
                    self.assertEqual(gd, wd, f"{e.name}.{gn}")

    def test_the_17_forwarded_entries_are_marked_linked(self):
        self.assertEqual(sorted(e.name for e in self.inst.entries if e.linked), sorted(LINKED))

    def test_every_call_site_of_the_corpus_has_an_entry_to_run(self):
        self.contract_paths()
        calls = scan_corpus()[1]
        entries = {e.name: e for e in self.inst.entries}
        for function, base, via, args in calls:
            if function == "GetControlRigInput":
                continue
            self.assertIn(function, entries, f"{base}: {function}")
            names = {p.name for p in entries[function].params}
            for name, value in args:
                self.assertIn(name, names, f"{base}: {function}({name} = ...) is not a parameter")
            if via == "Call":
                self.assertTrue(entries[function].linked, f"{base}: Call(Self.{function}) needs a forwarder on the linked instance")
            for p in entries[function].params:
                if p.name not in {n for n, _ in args}:
                    self.assertTrue(p.has_default, f"{base}: {function} is called without {p.name}, which has no default")

    def test_enum_arguments_of_the_corpus_are_members_and_asset_arguments_are_assets(self):
        self.contract_paths()
        members = self.state["GroundedState.HipsDirection"].members
        seen = set()
        for function, base, via, args in scan_corpus()[1]:
            for name, value in args:
                if name == "NewHipsDirection":
                    self.assertIn(value, members, base)
                    seen.add(value)
                if name == "Sequence":
                    self.assertRegex(value, r"^A_Als_\w+$", base)           # an asset name: the pack's clips are found by it
        self.assertTrue({"LeftForward", "LeftBackward", "RightForward", "RightBackward", "Backward"} <= seen)

    # -- the linked instance --------------------------------------------------------------------------------------------------------------------
    def test_the_linked_instance_forwards_exactly_the_17(self):
        self.assertIsNotNone(self.linked.linked)
        self.assertEqual(self.linked.linked.parent, A.INSTANCE)
        self.assertEqual(sorted(e.name for e in self.linked.linked.entries), sorted(LINKED))
        for e in self.linked.linked.entries:                                    # same parameters as the main entry, defaults included
            main = next(m for m in self.inst.entries if m.name == e.name)
            self.assertEqual([(p.name, p.ty.name, p.has_default, p.default) for p in e.params], [(p.name, p.ty.name, p.has_default, p.default) for p in main.params])
        self.assertEqual(self.linked.kind, "anim")

    def test_a_forwarded_call_reaches_the_main_instance(self):
        """Call(Self.RefreshGrounded) on the linked instance runs the main instance's entry (LI:90: Parent->RefreshGrounded())."""
        hosts = {n: harness.ScriptedHost() for n in self.mods}
        objs = {n: load_class(m, generate_python(m))(hosts[n]) for n, m in self.mods.items()}
        for n, m in self.mods.items():
            for p in m.peers:
                objs[n].connect(p.name, objs[p.logic])
            if m.linked is not None:
                objs[n].connect("Parent", objs[m.linked.parent])
        main, linked = objs[A.INSTANCE], objs[A.LINKED]
        linked.call_entry("SetHipsDirection", NewHipsDirection="RightBackward")
        self.assertEqual(main.GroundedState.HipsDirection, self.state["GroundedState.HipsDirection"].members.index("RightBackward"))
        linked.call_entry("SetHipsDirection")
        self.assertEqual(main.GroundedState.HipsDirection, 0)                   # the default Forward, as a .dab that leaves the argument out
        main.StandingState.bPivotActive = True
        linked.call_entry("ResetPivot")
        self.assertFalse(main.StandingState.bPivotActive)
        main.GroundedEntryMode = "Als.GroundedEntryMode.FromRoll"
        linked.call_entry("ResetGroundedEntryMode")
        self.assertEqual(main.GroundedEntryMode, "")

    def test_the_main_entries_not_forwarded_are_not_on_the_linked_instance(self):
        self.assertEqual({e.name for e in self.linked.entries}, set())          # the linked instance has no entries of its own
        forwarded = {e.name for e in self.linked.linked.entries}
        for name in PARAMETERISED:
            self.assertNotIn(name, forwarded)

    # -- the ControlRig input -------------------------------------------------------------------------------------------------------------------
    def test_get_control_rig_input_is_a_pub_view_returning_the_rig_struct(self):
        views = {m.name: m for m in self.inst.methods if m.is_view and m.pub}
        self.assertIn("GetControlRigInput", views)
        v = views["GetControlRigInput"]
        self.assertEqual(v.params, [])
        self.assertEqual(v.ret.name, "AlsControlRigInput")
        self.assertEqual([f.name for f in v.ret.fields], list(RIG_SCHEMA))

    def rig_schema(self):
        """The schema of the DreamVEd node when its source is there (parsed, not imported), else the copy above."""
        if os.path.isfile(CONTROLRIG_NODE):
            with open(CONTROLRIG_NODE, encoding="utf-8") as f:
                tree = ast.parse(f.read())
            for node in ast.walk(tree):
                if isinstance(node, ast.AnnAssign) and getattr(node.target, "id", "") == "STRUCT_SCHEMAS":
                    return ast.literal_eval(node.value)["AlsControlRigInput"]
        return RIG_SCHEMA

    def test_the_rig_struct_is_the_one_the_control_rig_node_reads(self):
        schema = self.rig_schema()
        self.assertEqual(schema, RIG_SCHEMA)
        fields = {f.name: f.ty.name for f in self.state_type("GetControlRigInput").fields}
        kind = {"bool": "b", "float": "f", "vec3": "v", "quat": "q"}
        self.assertEqual({n: kind[t] for n, t in fields.items()}, schema)
        leaves = sum({"b": 1, "f": 1, "v": 3, "q": 4}[k] for k in schema.values())
        self.assertEqual(leaves, 21)

    def state_type(self, view_name):
        return next(m for m in self.inst.methods if m.name == view_name).ret

    def test_the_getter_returns_21_leaves_of_the_right_shape(self):
        obj = load_class(self.inst, generate_python(self.inst))(harness.ScriptedHost())
        rig = obj.GetControlRigInput()
        leaves = []
        for name, kind in RIG_SCHEMA.items():
            v = getattr(rig, name)
            if kind == "b":
                self.assertIsInstance(v, bool, name)
                leaves.append(v)
            elif kind == "f":
                self.assertIsInstance(v, float, name)
                leaves.append(v)
            elif kind == "v":
                self.assertEqual(len(v), 3, name)
                leaves.extend(v)
            else:
                self.assertEqual(len(v), 4, name)
                self.assertAlmostEqual(sum(c * c for c in v), 1.0, places=9, msg=name)       # a unit quaternion, also while the feet are not valid
                leaves.extend(v)
        self.assertEqual(len(leaves), 21)

    def test_the_post_update_block_runs_the_queued_montages_in_the_frame(self):
        self.assertIsNotNone(self.inst.post_update)                              # NativePostUpdateAnimation via the proxy (AlsAnimationInstanceProxy.cpp:9)
        self.assertTrue(self.inst.post_flush)                                    # decision 2 (B): the commands of the frame run right after it

    def test_pub_methods_the_character_and_the_notifies_call(self):
        pub = {m.name: m for m in self.inst.methods if m.pub and not m.is_view}
        self.assertEqual(sorted(pub), ["Jump", "MarkTeleported", "MarkTeleportedAt", "SetGroundedEntryMode", "SnapshotFinalRagdollPose"])
        self.assertEqual([(n, t.name) for n, t in pub["MarkTeleportedAt"].params], [("Time", "float")])             # the character calls it with the time of the teleport (MarkTeleported() reads the world time)
        self.assertEqual([(n, t.name) for n, t in pub["SetGroundedEntryMode"].params], [("NewGroundedEntryMode", "AlsGroundedEntryMode")])


# ---- against the ALS C++ headers -------------------------------------------------------------------------------------------------------------

def header_text(*parts):
    with open(os.path.join(ALS_SOURCE, *parts), encoding="utf-8-sig") as f:
        return f.read()


def header_leaf_types():
    """``{dotted path: C++ type}`` of the members of UAlsAnimationInstance, structs flattened (State/*.h)."""
    structs = {}
    for f in glob.glob(os.path.join(ALS_SOURCE, "Public", "State", "*.h")):
        with open(f, encoding="utf-8-sig") as fh:
            text = fh.read()
        for sm in re.finditer(r"struct\s+ALS_API\s+(\w+)\s*\n\{(.*?)\n\};", text, re.S):
            structs[sm.group(1)] = [(m.group(2), m.group(1).strip()) for m in
                                    re.finditer(r"UPROPERTY\([^\n]*\)\s*\n\s*([\w:<>]+(?:\s+[\w:<>]+)?)\s+(\w+)(?:\s*:\s*1)?\s*(?:\{[^}]*\})?;", sm.group(2))]
    roots = {m.group(2): m.group(1) for m in re.finditer(r"UPROPERTY\([^\n]*\)\s*\n\s*([\w:<>]+)\s+(\w+)(?:\{[^}]*\})?;", header_text("Public", "AlsAnimationInstance.h"))}
    out = {}

    def walk(prefix, ty):
        if ty in structs:
            for n, t in structs[ty]:
                walk(prefix + "." + n, t)
        else:
            out[prefix] = ty

    for r, ty in roots.items():
        walk(r, ty)
    return out


def header_functions(header, cls):
    """``{name: [(type, parameter name, default or None)]}`` of the ``UFUNCTION(BlueprintCallable ...)`` void functions of a header."""
    text = re.sub(r"\s+", " ", header_text("Public", header))
    out = {}
    for m in re.finditer(r"UFUNCTION\(BlueprintCallable[^)]*\)(?:\)|)\s*void (\w+)\(([^)]*)\);", text):
        params = []
        for part in [p.strip() for p in m.group(2).split(",") if p.strip()]:
            lhs, _, default = part.partition("=")
            tokens = lhs.split()
            params.append((" ".join(tokens[:-1]), tokens[-1], default.strip() or None))
        out[m.group(1)] = params
    return out


@unittest.skipUnless(HAVE_ALS_SOURCE, "the ALS C++ source is not on this machine")
class AgainstTheCppHeadersTests(unittest.TestCase):
    def test_the_expected_types_are_the_header_types(self):
        types = header_leaf_types()
        classes = {"float": "float", "bool": "bool", "uint8": "bool", "FGameplayTag": "tag", "EAlsHipsDirection": "enum", "FPoseSnapshot": "FPoseSnapshot"}
        checked = 0
        for path in BOOLS | set(TAGS) | set(ENUMS) | {POSE_SNAPSHOT}:
            want = expected_type(path)
            got = classes[types[path]]
            self.assertEqual({"bool": "bool", "snapshot": "FPoseSnapshot"}.get(want, "tag" if want in TAGS.values() else "enum"), got, path)
            checked += 1
        self.assertEqual(checked, 13 + 6 + 1 + 1)
        if HAVE_CORPUS:
            for path in scan_corpus()[0]:
                if path not in BOOLS and path not in TAGS and path not in ENUMS and path != POSE_SNAPSHOT:
                    self.assertEqual(types[path], "float", path)

    def test_the_callable_functions_are_the_entries(self):
        funcs = header_functions("AlsAnimationInstance.h", "UAlsAnimationInstance")
        self.assertEqual(sorted(funcs), sorted(ENTRIES))
        for name, params in funcs.items():
            want = ENTRIES[name]
            self.assertEqual([p[1] for p in params], [w[0] for w in want], name)
            for (ctype, pname, default), (wn, wt, wd) in zip(params, want):
                if default is not None:
                    self.assertEqual(float(default.rstrip("f")) if wt == "float" else (default == "true"), wd, f"{name}.{pname}")

    def test_the_linked_instance_functions_are_the_17(self):
        funcs = header_functions("AlsLinkedAnimationInstance.h", "UAlsLinkedAnimationInstance")
        self.assertEqual(sorted(funcs), sorted(LINKED))

    def test_the_control_rig_input_struct_is_the_header_struct(self):
        text = header_text("Public", "State", "AlsControlRigInput.h")
        members = [m.group(1) for m in re.finditer(r"\n\s*(?:bool|float|FVector|FQuat)\s+(\w+)\s*\{", text)]
        self.assertEqual(members, list(RIG_SCHEMA))


# ---- the real character ------------------------------------------------------------------------------------------------------------------------

@unittest.skipUnless(A.als_files_present() and A.real_character_present(), "als/AlsCharacter.dal is not there yet")
class RealCharacterTests(unittest.TestCase):
    """The instance next to the real character (als/AlsCharacter.dal) instead of the stand-in of the behaviour tests."""

    @classmethod
    def setUpClass(cls):
        try:                                            # the host commands the character issues and the compiler does not list yet (tests only)
            import als_host_v2
            als_host_v2.install()
            cls.addClassCleanup(als_host_v2.uninstall)
        except ImportError:
            pass
        try:
            dal.check_file(os.path.join(A.ALS_DIR, A.CHARACTER + ".dal"))
        except dal.DalError as e:                                                       # the character is somebody else's work in progress
            raise unittest.SkipTest("als/AlsCharacter.dal does not compile yet: " + e.diagnostics[0].format().splitlines()[0])
        cls.mods = {n: dal.check_file(os.path.join(A.ALS_DIR, n + ".dal")).module for n in (A.CHARACTER, A.INSTANCE, A.LINKED)}

    def test_the_character_has_the_state_the_instance_reads(self):
        char = self.mods[A.CHARACTER]
        have = {s.name: s.ty for s in char.states}
        for name, ty in (("ViewMode", "AlsViewMode"), ("LocomotionMode", "AlsLocomotionMode"), ("RotationMode", "AlsRotationMode"), ("Stance", "AlsStance"),
                         ("Gait", "AlsGait"), ("OverlayMode", "AlsOverlayMode"), ("LocomotionAction", "AlsLocomotionAction"), ("LocomotionState", "AlsLocomotionState"),
                         ("ViewState", "AlsViewState"), ("RagdollingState", "AlsRagdollingState")):
            self.assertIn(name, have)
            self.assertEqual(have[name].name, ty, name)
        self.assertIn("Jump", {m.name for m in char.methods if m.pub})                  # AAlsCharacter::Jump() -> Anim.Jump()

    def test_interpreter_and_python_agree_on_the_real_pair(self):
        for seed in (1, 2):
            self.assertEqual(harness.verify_system({n: self.mods[n] for n in (A.CHARACTER, A.INSTANCE, A.LINKED)}, frames=60, seed=seed, tol=1e-9), [])

    def test_the_instance_follows_the_character(self):
        mods = {n: self.mods[n] for n in (A.CHARACTER, A.INSTANCE)}
        frames = []
        for i in range(90):
            speed = min(500.0, 8.0 * i)
            frames.append({"dt": 1 / 60, "time": i / 60, "velocity": (speed, 0.0, 0.0), "acceleration": (2000.0, 0.0, 0.0) if i < 70 else (0.0, 0.0, 0.0),
                           "max_acceleration": 2000.0, "max_braking_deceleration": 1500.0, "is_grounded": True, "actor_ready": True,
                           "actor_rotation": (0.0, 0.0, 0.0, 1.0), "control_rotation": (0.0, 0.0, 0.0), "capsule_radius": 30.0, "capsule_half_height": 90.0,
                           "actor_scale": 1.0, "walkable_floor_z": 0.71, "actor_location": (speed * i / 60, 0.0, 100.0), "free": {}, "calls": {},
                           "curves": {"PoseGrounded": 1.0, "PoseStanding": 1.0, "PoseGait": 2.0}})
        out = A.run_system(mods, frames)
        last = out[-1]
        self.assertAlmostEqual(last[A.INSTANCE]["LocomotionState.Speed"], 500.0, places=3)
        self.assertEqual(last[A.INSTANCE]["Gait"], last[A.CHARACTER]["Gait"])           # the instance copies what the character decided
        self.assertEqual(last[A.INSTANCE]["RotationMode"], last[A.CHARACTER]["RotationMode"])
        self.assertTrue(last[A.INSTANCE]["LocomotionState.bMoving"])
        self.assertAlmostEqual(last[A.INSTANCE]["GroundedState.VelocityBlend.ForwardAmount"], 0.0)       # nobody called RefreshGrounded: a graph does


if __name__ == "__main__":
    unittest.main()
