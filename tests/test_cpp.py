"""The C++ backend: golden files, structural invariants and the DalMath.h coverage check.

Regenerate the golden files with:  UPDATE_GOLDEN=1 python -I -m unittest discover -s tests -p "test_cpp.py"
(review the diff before committing).  The generated code is compiled inside Unreal later; these tests
pin its text and check the invariants that do not need the engine.
"""

import os
import re
import unittest

import _bootstrap  # noqa: F401
from _bootstrap import EXAMPLES, TESTS

import dal
from dal.builtins import BUILTINS
from dal.cppbackend import CppOptions, cpp_value, fmt_float, generate_cpp
from dal.types import BOOL, FLOAT, INT, QUAT, ROTATOR, VEC2, VEC3

GOLDEN = os.path.join(TESTS, "golden")
UPDATE = os.environ.get("UPDATE_GOLDEN") == "1"


def example(name):
    return dal.check_file(os.path.join(EXAMPLES, name)).module


def read(path):
    with open(path, "r", encoding="utf-8", newline="") as f:
        return f.read()


class GoldenTests(unittest.TestCase):
    def check_golden(self, files, wanted, prefix):
        os.makedirs(GOLDEN, exist_ok=True)
        for name in wanted:
            path = os.path.join(GOLDEN, f"{prefix}.{name}")
            text = files[name]
            if UPDATE:
                with open(path, "w", encoding="utf-8", newline="\n") as f:
                    f.write(text)
            self.assertTrue(os.path.isfile(path), f"missing golden file {path}; run with UPDATE_GOLDEN=1")
            self.assertEqual(text, read(path), f"{name} differs from {path}")

    def test_als_lean_with_api_macro(self):
        files = generate_cpp(example("als_lean.dal"), CppOptions(module_api="MYGAME_API"))
        self.check_golden(files, ["AlsLean.h", "AlsLean.cpp"], "als_lean")

    def test_hero_gait_defaults(self):
        files = generate_cpp(example("hero_gait.dal"))
        self.check_golden(files, ["HeroGait.h", "HeroGait.cpp"], "hero_gait")

    def test_math_header_is_copied_verbatim(self):
        files = generate_cpp(example("hero_gait.dal"))
        self.assertEqual(files["DalMath.h"], read(os.path.join(os.path.dirname(dal.__file__), "cpp", "DalMath.h")))

    def test_output_is_lf_only_and_deterministic(self):
        a = generate_cpp(example("hero_gait.dal"))
        b = generate_cpp(example("hero_gait.dal"))
        self.assertEqual(a, b)
        for name, text in a.items():
            self.assertNotIn("\r", text, name)
            self.assertTrue(text.endswith("\n") and not text.endswith("\n\n"), name)


class StructureTests(unittest.TestCase):
    def setUp(self):
        self.lean = generate_cpp(example("als_lean.dal"), CppOptions(module_api="MYGAME_API"))
        self.gait = generate_cpp(example("hero_gait.dal"))

    def function_body(self, text, signature):
        start = text.index(signature)
        brace = text.index("{", start)
        depth = 0
        for i in range(brace, len(text)):
            if text[i] == "{":
                depth += 1
            elif text[i] == "}":
                depth -= 1
                if depth == 0:
                    return text[brace:i + 1]
        raise AssertionError("unbalanced")

    def test_class_shape(self):
        h = self.lean["AlsLean.h"]
        self.assertIn("class MYGAME_API UAlsLean : public UAnimInstance", h)
        self.assertIn("GENERATED_BODY()", h)
        self.assertTrue(h.index('#include "Animation/AnimInstance.h"') < h.index('#include "AlsLean.generated.h"'))
        self.assertTrue(h.rstrip().endswith("};"))
        self.assertIn("virtual void NativeInitializeAnimation() override;", h)
        self.assertIn("virtual void NativeUpdateAnimation(float DeltaSeconds) override;", h)
        self.assertIn("virtual void NativeThreadSafeUpdateAnimation(float DeltaSeconds) override;", h)
        self.assertNotIn("MYGAME_API", self.gait["HeroGait.h"])
        self.assertIn("class UHeroGait : public UAnimInstance", self.gait["HeroGait.h"])

    def test_parent_class_option(self):
        files = generate_cpp(example("hero_gait.dal"), CppOptions(parent_class="UMyBaseAnimInstance", parent_include="MyBase.h"))
        self.assertIn("class UHeroGait : public UMyBaseAnimInstance", files["HeroGait.h"])
        self.assertIn('#include "MyBase.h"', files["HeroGait.h"])
        self.assertNotIn("Animation/AnimInstance.h", files["HeroGait.h"])

    def test_properties_keep_their_dal_names(self):
        h = self.gait["HeroGait.h"]
        for name in ("Speed", "CurrentGait", "InAir", "Lean", "Steps", "AimOffset", "MoveDirection"):
            self.assertRegex(h, rf"\b{name} = ", name)
        self.assertIn('UPROPERTY(VisibleAnywhere, BlueprintReadOnly, Category = "DAL|State", Transient)\n\tfloat Speed = 0.0f;', h)
        self.assertIn('UPROPERTY(EditAnywhere, BlueprintReadOnly, Category = "DAL|Param")\n\tfloat SpeedInterpSpeed = 8.0f;', h)
        self.assertIn('UPROPERTY(VisibleAnywhere, BlueprintReadWrite, Category = "DAL|Input", Transient)\n\tbool Aiming = false;', h)
        self.assertIn("TEnumAsByte<EMovementMode> Mode = MOVE_None;", h)
        self.assertIn("EGait CurrentGait = EGait::Walk;", h)
        self.assertIn("enum class EGait : uint8", h)

    def test_entries_and_notifies_are_callable_by_dab(self):
        h = self.gait["HeroGait.h"]
        self.assertIn('UFUNCTION(BlueprintCallable, Category = "DAL|Entry", Meta = (BlueprintThreadSafe))\n\tvoid ResetSteps();', h)
        self.assertIn("UFUNCTION()\n\tvoid AnimNotify_Footstep();", h)
        c = self.gait["HeroGait.cpp"]
        self.assertIn("void UHeroGait::ResetSteps()", c)
        self.assertIn("const float dt = Dal_DeltaSeconds;", c)
        self.assertIn("void UHeroGait::AnimNotify_Footstep()", c)

    def test_game_thread_reads_worker_thread_computes(self):
        c = self.gait["HeroGait.cpp"]
        native_update = self.function_body(c, "void UHeroGait::NativeUpdateAnimation")
        self.assertIn("DalSampleInputs();", native_update)
        thread_safe = self.function_body(c, "void UHeroGait::NativeThreadSafeUpdateAnimation")
        self.assertIn("DalUpdate(DeltaSeconds);", thread_safe)
        self.assertIn("Super::NativeThreadSafeUpdateAnimation(DeltaSeconds);", thread_safe)
        sample = self.function_body(c, "void UHeroGait::DalSampleInputs")
        self.assertIn("TryGetPawnOwner()", sample)

    def test_worker_thread_code_never_touches_the_world(self):
        """DalUpdate, entries and the generated functions may use only this object's data."""
        banned = ("TryGetPawnOwner", "GetWorld", "Cast<", "GetOwningActor", "GetCharacterMovement", "->Velocity", "GetSkelMeshComponent")
        for files, cls, names in ((self.lean, "UAlsLean", ["DalUpdate"]), (self.gait, "UHeroGait", ["DalUpdate", "ResetSteps"])):
            text = files[cls[1:] + ".cpp"]
            bodies = [self.function_body(text, f"void {cls}::{n}(") for n in names]
            bodies += [m.group(0) for m in re.finditer(r"\bDal_Fn_\w+\([^)]*\)\n\t\{.*?\n\t\}", text, re.S)]
            self.assertTrue(bodies)
            for body in bodies:
                for token in banned:
                    self.assertNotIn(token, body, f"{cls}: {token} in worker-thread code")

    def test_host_reads_are_null_safe_and_use_pawn_owner(self):
        c = self.lean["AlsLean.cpp"]
        sample = self.function_body(c, "void UAlsLean::DalSampleInputs")
        self.assertIn("const APawn* Pawn = TryGetPawnOwner();", sample)
        self.assertIn("const ACharacter* Character = Cast<ACharacter>(Pawn);", sample)
        self.assertIn("IsValid(Character) ? Character->GetCharacterMovement() : nullptr", sample)
        self.assertIn("Velocity = FVector3f(Movement->Velocity);", sample)
        self.assertIn("ActorRotation = FQuat4f(Pawn->GetActorQuat());", sample)
        self.assertIn("Grounded = Movement->IsMovingOnGround();", sample)
        # every host read is guarded by a pointer check
        self.assertEqual(sample.count("if (Movement != nullptr)"), 4)

    def test_enhanced_input_actions(self):
        h, c = self.gait["HeroGait.h"], self.gait["HeroGait.cpp"]
        self.assertIn("TObjectPtr<UInputAction> DalAction_Move;", h)
        self.assertIn("class UInputAction;", h)
        self.assertIn("// Module dependencies: Core, CoreUObject, Engine, EnhancedInput", h)
        self.assertIn("MoveInput = FVector2f(PlayerInput->GetActionValue(DalAction_Move).Get<FVector2D>());", c)
        self.assertIn("SprintHeld = PlayerInput->GetActionValue(DalAction_Sprint).Get<bool>();", c)
        self.assertIn('#include "EnhancedInputSubsystems.h"', c)
        self.assertNotIn("EnhancedInput", self.lean["AlsLean.cpp"])

    def test_notify_flag_is_consumed_when_sampled(self):
        c = self.gait["HeroGait.cpp"]
        self.assertIn("FootstepFired = DalNotify_Footstep;\n\tDalNotify_Footstep = false;", c)

    def test_extern_fn_include_and_call(self):
        c = self.lean["AlsLean.cpp"]
        self.assertIn('#include "AlsLeanLibrary.h"', c)
        self.assertIn("AlsLeanLibrary::InAirLeanCurve(Velocity.Z)", c)

    def test_user_constants_functions_and_math_calls(self):
        c = self.lean["AlsLean.cpp"]
        self.assertIn("[[maybe_unused]] const float Dal_K_InAirLeanReferenceSpeed = 350.0f;", c)
        self.assertIn("[[maybe_unused]] FVector3f Dal_Fn_clamp_magnitude01(FVector3f v)", c)
        self.assertIn("DalMath::Swizzle2<0, 1>(", c)
        self.assertIn("UE_KINDA_SMALL_NUMBER", c)
        self.assertIn("DalMath::Div(", c)
        self.assertIn('#include "DalMath.h"', c)

    def test_braces_and_parentheses_balance(self):
        for files in (self.lean, self.gait):
            for name, text in files.items():
                if name == "DalMath.h":
                    continue
                stripped = re.sub(r'"[^"\n]*"', '""', re.sub(r"//[^\n]*", "", text))
                self.assertEqual(stripped.count("{"), stripped.count("}"), name)
                self.assertEqual(stripped.count("("), stripped.count(")"), name)

    def test_banner_names_the_source_and_forbids_editing(self):
        h = self.lean["AlsLean.h"]
        self.assertTrue(h.startswith("// Generated by dal 0.1.0 from als_lean.dal (sha256 "))
        self.assertIn("Do not edit", h)


class LiteralTests(unittest.TestCase):
    def test_float_literals_are_shortest_float32(self):
        for value, text in ((0.2, "0.2f"), (350.0, "350.0f"), (0.30000000000000004, "0.3f"), (1e-8, "1e-8f"),
                            (-0.5, "-0.5f"), (0.0, "0.0f"), (1e7, "1e7f"), (123456.789, "123456.79f"), (0.0001, "0.0001f")):
            self.assertEqual(fmt_float(value), text)

    def test_value_literals(self):
        from dal import runtime as rt

        self.assertEqual(cpp_value(INT, 3), "3")
        self.assertEqual(cpp_value(BOOL, True), "true")
        self.assertEqual(cpp_value(VEC2, rt.Vec2(1, 2)), "FVector2f(1.0f, 2.0f)")
        self.assertEqual(cpp_value(VEC3, rt.Vec3(0, 0, -1)), "FVector3f(0.0f, 0.0f, -1.0f)")
        self.assertEqual(cpp_value(ROTATOR, rt.Rotator(0, 90, 0)), "FRotator3f(0.0f, 90.0f, 0.0f)")
        self.assertEqual(cpp_value(QUAT, rt.QUAT_IDENTITY), "FQuat4f(0.0f, 0.0f, 0.0f, 1.0f)")


CPP_TO_DAL = {"float": FLOAT, "int32": INT, "bool": BOOL, "FVector2f": VEC2, "FVector3f": VEC3, "FRotator3f": ROTATOR, "FQuat4f": QUAT}


def parse_dalmath_header():
    """{function name: set of (param types, return type)} from the declarations in DalMath.h."""
    text = read(os.path.join(os.path.dirname(dal.__file__), "cpp", "DalMath.h"))
    out = {}
    pattern = re.compile(r"\[\[nodiscard\]\]\s+inline\s+([\w:]+)\s+(\w+)\(([^)]*)\)")
    for m in pattern.finditer(text):
        ret, name, params = m.groups()
        types = []
        for p in [x.strip() for x in params.split(",") if x.strip()]:
            p = re.sub(r"\bconst\b|&", "", p).strip()
            ty = p.rsplit(" ", 1)[0].strip()
            types.append(ty)
        if ret in CPP_TO_DAL and all(t in CPP_TO_DAL for t in types):
            out.setdefault(name, set()).add((tuple(CPP_TO_DAL[t] for t in types), CPP_TO_DAL[ret]))
    return out


class DalMathCoverageTests(unittest.TestCase):
    def test_every_builtin_signature_has_a_cpp_overload(self):
        header = parse_dalmath_header()
        missing = []
        for name, b in BUILTINS.items():
            for sig in b.sigs:
                if (tuple(sig.params), sig.ret) not in header.get(b.cpp, set()):
                    missing.append(f"{name}: DalMath::{b.cpp}({', '.join(t.name for t in sig.params)}) -> {sig.ret.name}")
        self.assertEqual(missing, [])

    def test_generated_code_only_calls_declared_dalmath_functions(self):
        header_names = set(parse_dalmath_header()) | {"Swizzle2", "Swizzle3", "MakeVec3"}
        for module in (example("als_lean.dal"), example("hero_gait.dal")):
            files = generate_cpp(module)
            for text in files.values():
                for fn in re.findall(r"DalMath::(\w+)", text):
                    self.assertIn(fn, header_names)

    def test_header_has_a_version_and_pragma_once(self):
        text = read(os.path.join(os.path.dirname(dal.__file__), "cpp", "DalMath.h"))
        self.assertIn("#pragma once", text)
        self.assertIn("#define DAL_MATH_VERSION 1", text)


class CompileEveryBuiltinTests(unittest.TestCase):
    def test_a_program_using_every_builtin_generates_cpp(self):
        """Smoke test of the expression generator on all built-in signatures."""
        lines = []
        for name, b in sorted(BUILTINS.items()):
            for idx, sig in enumerate(b.sigs):
                ident = f"R_{name}_{idx}"
                args = ", ".join(f"P_{name}_{idx}_{i}" for i in range(len(sig.params)))
                lines.append((f"state {ident}: {sig.ret.name};", [f"input P_{name}_{idx}_{i}: {t.name};" for i, t in enumerate(sig.params)], f"{ident} = {name}({args});"))
        decls = "\n".join(d for d, _i, _s in lines) + "\n" + "\n".join(i for _d, ins, _s in lines for i in ins)
        src = "logic Everything;\n" + decls + "\nupdate(dt) {\n" + "\n".join("  " + s for _d, _i, s in lines) + "\n}\n"
        module = dal.check_source(src, "everything.dal").module
        files = generate_cpp(module)
        cpp = files["Everything.cpp"]
        for name, b in BUILTINS.items():
            self.assertIn(f"DalMath::{b.cpp}(", cpp, name)


if __name__ == "__main__":
    unittest.main()
