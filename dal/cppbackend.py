"""C++ backend: IR -> an Unreal Engine 5.8 ``UAnimInstance`` parent class.

Generated files (``generate_cpp``):

* ``<Logic>.h`` / ``<Logic>.cpp`` -- ``UCLASS() class [API] U<Logic> : public UAnimInstance``;
* ``DalMath.h`` -- the static math library (``dal/cpp/DalMath.h``).

Threading model (matches ``UAnimInstance``):

* ``NativeUpdateAnimation`` (game thread) reads the host -- owning pawn, character movement,
  enhanced input, world time -- into the input properties (``DalSampleInputs``);
* ``NativeThreadSafeUpdateAnimation`` (worker thread) runs the logic (``DalUpdate``), which only
  touches the class's own properties, params, constants and pure functions;
* entries are ``BlueprintThreadSafe`` UFUNCTIONs that ``.dab`` can name in ``Call(Self.Entry)``.

The output is deterministic (no timestamps) and tab-indented like UE code.
"""

from __future__ import annotations

import os
import struct
from dataclasses import dataclass

from . import ir
from .builtins import BUILTIN_CONSTS, BUILTINS
from .types import (
    BOOL,
    FLOAT,
    INT,
    MOVEMENT_MODE,
    QUAT,
    ROTATOR,
    VEC2,
    VEC3,
    EnumType,
    Type,
)

T = "\t"


@dataclass
class CppOptions:
    module_api: str = ""                           # e.g. "MYGAME_API"
    parent_class: str = "UAnimInstance"
    parent_include: str = "Animation/AnimInstance.h"


# ---- literals and types ---------------------------------------------------------------------

def _f32(x: float) -> float:
    return struct.unpack("f", struct.pack("f", x))[0]


def fmt_float(x: float) -> str:
    """Shortest decimal that rounds to the same float32, with an 'f' suffix."""
    from decimal import Decimal

    x = float(x)
    if x != x or x in (float("inf"), float("-inf")):
        raise ValueError("non-finite float literal")
    target = _f32(x)
    s = repr(x)
    for p in range(1, 10):
        cand = f"{x:.{p}g}"
        if _f32(float(cand)) == target:
            s = cand
            break
    if x != 0.0 and 1e-4 <= abs(x) < 1e7:
        s = format(Decimal(s), "f")
    elif "e" in s:
        mant, exp = s.split("e")
        s = f"{mant}e{int(exp)}"
    if "e" not in s and "." not in s:
        s += ".0"
    return s + "f"


def unparen(s: str) -> str:
    """Drop one redundant pair of outer parentheses: '(a + b)' -> 'a + b'."""
    if not (s.startswith("(") and s.endswith(")")):
        return s
    depth = 0
    for i, ch in enumerate(s):
        if ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
            if depth == 0:
                return s[1:-1] if i == len(s) - 1 else s
    return s


def cpp_type(ty: Type) -> str:
    if ty is FLOAT:
        return "float"
    if ty is INT:
        return "int32"
    if ty is BOOL:
        return "bool"
    if ty is VEC2:
        return "FVector2f"
    if ty is VEC3:
        return "FVector3f"
    if ty is ROTATOR:
        return "FRotator3f"
    if ty is QUAT:
        return "FQuat4f"
    if isinstance(ty, EnumType):
        return ty.cpp_type
    raise ValueError(ty)


def cpp_prop_type(ty: Type) -> str:
    return ty.cpp_prop_type if isinstance(ty, EnumType) else cpp_type(ty)


def cpp_value(ty: Type, v) -> str:
    if ty is FLOAT:
        return fmt_float(v)
    if ty is INT:
        return str(int(v))
    if ty is BOOL:
        return "true" if v else "false"
    if ty is VEC2:
        return "FVector2f(%s)" % ", ".join(fmt_float(c) for c in v)
    if ty is VEC3:
        return "FVector3f(%s)" % ", ".join(fmt_float(c) for c in v)
    if ty is ROTATOR:
        return "FRotator3f(%s)" % ", ".join(fmt_float(c) for c in v)
    if ty is QUAT:
        return "FQuat4f(%s)" % ", ".join(fmt_float(c) for c in v)
    if isinstance(ty, EnumType):
        return ty.cpp_members[ty.members[int(v)]]
    raise ValueError(ty)


def _is_nonzero_number(e: ir.Expr) -> bool:
    if isinstance(e, (ir.Lit, ir.ConstRef)) and isinstance(e.value, (int, float)) and not isinstance(e.value, bool):
        return e.value != 0
    return False


_SWIZ_IDX = {"x": 0, "y": 1, "z": 2}
_COMP_CPP = {"x": "X", "y": "Y", "z": "Z", "w": "W", "pitch": "Pitch", "yaw": "Yaw", "roll": "Roll"}


class CppExprGen:
    def __init__(self, module: ir.Module):
        self.m = module

    def x(self, e: ir.Expr) -> str:
        k = type(e)
        if k is ir.Lit:
            return cpp_value(e.ty, e.value)
        if k is ir.EnumLit:
            return e.ty.cpp_members[e.member]  # type: ignore[attr-defined]
        if k is ir.ConstRef:
            if e.name in BUILTIN_CONSTS:
                return BUILTIN_CONSTS[e.name].cpp
            return f"Dal_K_{e.name}"
        if k in (ir.ParamRef, ir.InputRef, ir.StateRef):
            if e.ty is MOVEMENT_MODE:
                return f"static_cast<EMovementMode>({e.name})"
            return e.name
        if k is ir.LocalRef:
            return e.name
        if k is ir.ToFloat:
            return f"static_cast<float>({self.x(e.x)})"
        if k is ir.Unary:
            return f"(!{self.x(e.x)})" if e.op == "!" else f"(-{self.x(e.x)})"
        if k is ir.Binary:
            return self.binary(e)
        if k is ir.Cond:
            return f"({self.x(e.c)} ? {self.x(e.a)} : {self.x(e.b)})"
        if k is ir.Member:
            return self.member(e)
        if k is ir.Make:
            a = [unparen(self.x(v)) for v in e.args]
            if e.ctor == "vec2":
                return f"FVector2f({', '.join(a)})"
            if e.ctor == "vec3":
                return f"DalMath::MakeVec3({', '.join(a)})" if e.sig_index == 1 else f"FVector3f({', '.join(a)})"
            if e.ctor == "rotator":
                return f"FRotator3f({', '.join(a)})"
            return f"FQuat4f({', '.join(a)})"
        if k is ir.CallBuiltin:
            return f"DalMath::{BUILTINS[e.name].cpp}({', '.join(unparen(self.x(a)) for a in e.args)})"
        if k is ir.CallFn:
            return f"Dal_Fn_{e.name}({', '.join(unparen(self.x(a)) for a in e.args)})"
        if k is ir.CallExtern:
            ext = self.m.find_extern(e.name)
            return f"{ext.cpp}({', '.join(unparen(self.x(a)) for a in e.args)})"  # type: ignore[union-attr]
        raise AssertionError(k)

    def member(self, e: ir.Member) -> str:
        base = self.x(e.x)
        if len(e.comps) == 1 or e.comps in ("pitch", "yaw", "roll"):
            return f"{base}.{_COMP_CPP[e.comps]}"
        idx = ", ".join(str(_SWIZ_IDX[c]) for c in e.comps)
        fn = "Swizzle2" if len(e.comps) == 2 else "Swizzle3"
        return f"DalMath::{fn}<{idx}>({unparen(base)})"

    def binary(self, e: ir.Binary) -> str:
        a, b, op = self.x(e.a), self.x(e.b), e.op
        ta, tb = e.a.ty, e.b.ty
        if op in ("&&", "||", "==", "!=", "<", "<=", ">", ">="):
            return f"({a} {op} {b})"
        if op == "/":
            if _is_nonzero_number(e.b) and not (ta is INT and tb is INT and e.b.value == -1):  # type: ignore[attr-defined]
                return f"({a} / {b})"
            return f"DalMath::Div({a}, {b})"
        if op == "%":
            if _is_nonzero_number(e.b) and e.b.value != -1:  # type: ignore[attr-defined]
                return f"({a} % {b})"
            return f"DalMath::Mod({a}, {b})"
        return f"({a} {op} {b})"


class CppGen:
    def __init__(self, module: ir.Module, options: CppOptions):
        self.m = module
        self.o = options
        self.ex = CppExprGen(module)
        self.cls = "U" + module.name
        self.api = (options.module_api + " ") if options.module_api else ""

    # ---- statements ---------------------------------------------------------------------------
    def stmts(self, body: list[ir.Stmt], depth: int) -> list[str]:
        pad = T * depth
        out: list[str] = []
        for s in body:
            if isinstance(s, ir.Let):
                const = "" if s.mutable else "const "
                out.append(f"{pad}{const}{cpp_type(s.ty)} {s.name} = {unparen(self.ex.x(s.init))};")
            elif isinstance(s, ir.Assign):
                out.append(f"{pad}{s.name} = {unparen(self.ex.x(s.value))};")
            elif isinstance(s, ir.If):
                out.extend(self.if_chain(s, depth))
            elif isinstance(s, ir.Return):
                out.append(f"{pad}return" + (f" {unparen(self.ex.x(s.value))}" if s.value is not None else "") + ";")
        return out

    def if_chain(self, s: ir.If, depth: int) -> list[str]:
        pad = T * depth
        out = [f"{pad}if ({unparen(self.ex.x(s.cond))})", f"{pad}{{"]
        out.extend(self.stmts(s.then, depth + 1))
        out.append(f"{pad}}}")
        cur = s
        while cur.orelse:
            if len(cur.orelse) == 1 and isinstance(cur.orelse[0], ir.If):
                cur = cur.orelse[0]
                out.append(f"{pad}else if ({unparen(self.ex.x(cur.cond))})")
                out.append(f"{pad}{{")
                out.extend(self.stmts(cur.then, depth + 1))
                out.append(f"{pad}}}")
                continue
            out.append(f"{pad}else")
            out.append(f"{pad}{{")
            out.extend(self.stmts(cur.orelse, depth + 1))
            out.append(f"{pad}}}")
            break
        return out

    # ---- analysis ------------------------------------------------------------------------------------
    def host_items(self) -> list[tuple[ir.InputDef, ir.HostUse]]:
        return [(i, i.host) for i in self.m.inputs if i.host is not None]

    def action_names(self) -> list[str]:
        seen: list[str] = []
        for _i, h in self.host_items():
            if h.item == "action" and h.arg not in seen:
                seen.append(h.arg)  # type: ignore[arg-type]
        return seen

    def notify_names(self) -> list[str]:
        seen: list[str] = []
        for _i, h in self.host_items():
            if h.item == "notify" and h.arg not in seen:
                seen.append(h.arg)  # type: ignore[arg-type]
        return seen

    def uses(self, *items: str) -> bool:
        return any(h.item in items for _i, h in self.host_items())

    def modules(self) -> list[str]:
        mods = ["Core", "CoreUObject", "Engine"]
        if self.action_names():
            mods += ["EnhancedInput"]
        return mods

    # ---- header -------------------------------------------------------------------------------------------
    def banner(self) -> list[str]:
        from . import __version__

        m = self.m
        return [
            f"// Generated by dal {__version__} from {m.filename} (sha256 {m.source_sha256[:16]}...).",
            f"// Do not edit: change the .dal source and recompile.",
        ]

    def header(self) -> str:
        m, o = self.m, self.o
        L = self.banner()
        L.append(f"// Module dependencies: {', '.join(self.modules())}")
        L.append("#pragma once")
        L.append("")
        L.append('#include "CoreMinimal.h"')
        L.append(f'#include "{o.parent_include}"')
        if any(i.ty is MOVEMENT_MODE for i in [*m.params, *m.inputs, *m.states]) or self.uses("movement_mode"):
            L.append('#include "Engine/EngineTypes.h"')
        L.append(f'#include "{m.name}.generated.h"')
        L.append("")
        if self.action_names():
            L.append("class UInputAction;")
            L.append("")
        for en in m.enums:
            L.append("UENUM(BlueprintType)")
            L.append(f"enum class {en.cpp_type} : uint8")
            L.append("{")
            for mem in en.members:
                L.append(f"{T}{mem},")
            L.append("};")
            L.append("")
        L.append("UCLASS()")
        L.append(f"class {self.api}{self.cls} : public {o.parent_class}")
        L.append("{")
        L.append(f"{T}GENERATED_BODY()")
        L.append("")
        L.append("public:")
        if m.params:
            L.append(f"{T}// ---- params: tunable in the editor, read-only for the logic ----")
            for p in m.params:
                L.append(f'{T}UPROPERTY(EditAnywhere, BlueprintReadOnly, Category = "DAL|Param")')
                L.append(f"{T}{cpp_prop_type(p.ty)} {p.name} = {cpp_value(p.ty, p.default)};")
                L.append("")
        if m.inputs:
            L.append(f"{T}// ---- inputs: filled from the host each frame (or set by game code when free) ----")
            for i in m.inputs:
                if i.host is not None:
                    h = i.host
                    src = f"host.{h.item}" + (f'("{h.arg}")' if h.arg is not None else "")
                    L.append(f"{T}// <- {src}")
                    L.append(f'{T}UPROPERTY(VisibleAnywhere, BlueprintReadOnly, Category = "DAL|Input", Transient)')
                else:
                    L.append(f'{T}UPROPERTY(VisibleAnywhere, BlueprintReadWrite, Category = "DAL|Input", Transient)')
                L.append(f"{T}{cpp_prop_type(i.ty)} {i.name} = {cpp_value(i.ty, i.default)};")
                L.append("")
        if m.states:
            L.append(f"{T}// ---- state: written by the logic, readable from the animation graph ----")
            for s in m.states:
                L.append(f'{T}UPROPERTY(VisibleAnywhere, BlueprintReadOnly, Category = "DAL|State", Transient)')
                L.append(f"{T}{cpp_prop_type(s.ty)} {s.name} = {cpp_value(s.ty, s.default)};")
                L.append("")
        actions = self.action_names()
        if actions:
            L.append(f"{T}// ---- enhanced input assets read by host.action(...); assign them in the class defaults ----")
            for a in actions:
                L.append(f'{T}UPROPERTY(EditAnywhere, BlueprintReadOnly, Category = "DAL|Host")')
                L.append(f"{T}TObjectPtr<UInputAction> DalAction_{a};")
                L.append("")
        L.append(f"{T}virtual void NativeInitializeAnimation() override;")
        L.append(f"{T}virtual void NativeUpdateAnimation(float DeltaSeconds) override;")
        L.append(f"{T}virtual void NativeThreadSafeUpdateAnimation(float DeltaSeconds) override;")
        L.append("")
        L.append(f"{T}/** Restore every input and state to its declared default. */")
        L.append(f"{T}void DalResetState();")
        L.append("")
        for e in m.entries:
            L.append(f"{T}/** Entry: call from a .dab with Call(Self.{e.name}). Uses the delta time of the current update. */")
            L.append(f'{T}UFUNCTION(BlueprintCallable, Category = "DAL|Entry", Meta = (BlueprintThreadSafe))')
            L.append(f"{T}void {e.name}();")
            L.append("")
        for n in self.notify_names():
            L.append(f"{T}/** Animation notify '{n}': read through host.notify(\"{n}\"). */")
            L.append(f"{T}UFUNCTION()")
            L.append(f"{T}void AnimNotify_{n}();")
            L.append("")
        L.append("protected:")
        L.append(f"{T}/** Game thread: read the host into the input properties. */")
        L.append(f"{T}void DalSampleInputs();")
        L.append("")
        L.append(f"{T}/** Worker thread: the update body. Touches only this object's properties. */")
        L.append(f"{T}void DalUpdate(float DeltaSeconds);")
        L.append("")
        L.append("private:")
        L.append(f"{T}float Dal_DeltaSeconds = 0.0f;")
        for n in self.notify_names():
            L.append(f"{T}bool DalNotify_{n} = false;")
        L.append("};")
        return "\n".join(L) + "\n"

    # ---- source -------------------------------------------------------------------------------------------------
    def source(self) -> str:
        m = self.m
        L = self.banner()
        L.append(f'#include "{m.name}.h"')
        L.append("")
        L.append('#include "DalMath.h"')
        if self.uses("time"):
            L.append('#include "Engine/World.h"')
        movement_items = ("velocity", "acceleration", "max_acceleration", "max_braking_deceleration",
                          "max_walk_speed", "movement_mode", "is_grounded", "is_crouching")
        if self.uses(*movement_items):
            L.append('#include "GameFramework/Character.h"')
            L.append('#include "GameFramework/CharacterMovementComponent.h"')
        if self.uses("actor_rotation", "control_rotation", "action"):
            L.append('#include "GameFramework/Pawn.h"')
        if self.action_names():
            for inc in ("Engine/LocalPlayer.h", "GameFramework/PlayerController.h", "EnhancedInputSubsystems.h",
                        "EnhancedPlayerInput.h", "InputAction.h", "InputActionValue.h"):
                L.append(f'#include "{inc}"')
        for x in m.externs:
            if x.include:
                L.append(f'#include "{x.include}"')
        L.append("")
        L.append(f"#include UE_INLINE_GENERATED_CPP_BY_NAME({m.name})")
        L.append("")

        if m.consts or m.funcs or self.action_names():
            L.append("namespace")
            L.append("{")
            for c in m.consts:
                L.append(f"{T}// const {c.name}: {c.ty} = {c.source.rstrip(';').split('=', 1)[-1].strip()}")
                L.append(f"{T}[[maybe_unused]] const {cpp_type(c.ty)} Dal_K_{c.name} = {cpp_value(c.ty, c.value)};")
            if m.consts:
                L.append("")
            for f in m.funcs:
                params = ", ".join(f"{cpp_type(t)} {n}" for n, t in f.params)
                L.append(f"{T}[[maybe_unused]] {cpp_type(f.ret)} Dal_Fn_{f.name}({params})")
                L.append(f"{T}{{")
                L.extend(self.stmts(f.body, 2))
                L.append(f"{T}}}")
                L.append("")
            if self.action_names():
                L.append(f"{T}const UEnhancedPlayerInput* DalGetPlayerInput(const APawn* Pawn)")
                L.append(f"{T}{{")
                L.append(f"{T}{T}const APlayerController* Controller = Pawn != nullptr ? Cast<APlayerController>(Pawn->GetController()) : nullptr;")
                L.append(f"{T}{T}const ULocalPlayer* LocalPlayer = Controller != nullptr ? Controller->GetLocalPlayer() : nullptr;")
                L.append(f"{T}{T}const UEnhancedInputLocalPlayerSubsystem* Subsystem =")
                L.append(f"{T}{T}{T}LocalPlayer != nullptr ? LocalPlayer->GetSubsystem<UEnhancedInputLocalPlayerSubsystem>() : nullptr;")
                L.append(f"{T}{T}return Subsystem != nullptr ? Subsystem->GetPlayerInput() : nullptr;")
                L.append(f"{T}}}")
                L.append("")
            while L[-1] == "":
                L.pop()
            L.append("}")
            L.append("")

        cls = self.cls
        L.append(f"void {cls}::NativeInitializeAnimation()")
        L.append("{")
        L.append(f"{T}Super::NativeInitializeAnimation();")
        L.append("")
        L.append(f"{T}DalResetState();")
        L.append("}")
        L.append("")
        L.append(f"void {cls}::NativeUpdateAnimation(const float DeltaSeconds)")
        L.append("{")
        L.append(f"{T}Super::NativeUpdateAnimation(DeltaSeconds);")
        L.append("")
        L.append(f"{T}// Game thread: gather the host values; the worker thread only reads this object.")
        L.append(f"{T}DalSampleInputs();")
        L.append("}")
        L.append("")
        L.append(f"void {cls}::NativeThreadSafeUpdateAnimation(const float DeltaSeconds)")
        L.append("{")
        L.append(f"{T}Super::NativeThreadSafeUpdateAnimation(DeltaSeconds);")
        L.append("")
        L.append(f"{T}Dal_DeltaSeconds = DeltaSeconds;")
        L.append(f"{T}DalUpdate(DeltaSeconds);")
        L.append("}")
        L.append("")
        L.append(f"void {cls}::DalResetState()")
        L.append("{")
        L.append(f"{T}Dal_DeltaSeconds = 0.0f;")
        for n in self.notify_names():
            L.append(f"{T}DalNotify_{n} = false;")
        for i in m.inputs:
            L.append(f"{T}{i.name} = {cpp_value(i.ty, i.default)};")
        for s in m.states:
            L.append(f"{T}{s.name} = {cpp_value(s.ty, s.default)};")
        L.append("}")
        L.append("")
        L.extend(self.sample_function())
        L.append("")
        u = m.update
        L.append(f"void {cls}::DalUpdate(const float {u.dt_name if u else 'DeltaSeconds'})")
        L.append("{")
        if u is not None:
            L.extend(self.stmts(u.body, 1))
        else:
            L.append(f"{T}// (no update body)")
        L.append("}")
        L.append("")
        for e in m.entries:
            L.append(f"void {cls}::{e.name}()")
            L.append("{")
            L.append(f"{T}const float {e.dt_name} = Dal_DeltaSeconds;")
            L.extend(self.stmts(e.body, 1))
            L.append("}")
            L.append("")
        for n in self.notify_names():
            L.append(f"void {cls}::AnimNotify_{n}()")
            L.append("{")
            L.append(f"{T}DalNotify_{n} = true;")
            L.append("}")
            L.append("")
        while L[-1] == "":
            L.pop()
        return "\n".join(L) + "\n"

    def sample_function(self) -> list[str]:
        m = self.m
        L = [f"void {self.cls}::DalSampleInputs()", "{"]
        host = self.host_items()
        if not host:
            L.append(f"{T}// (no host-bound inputs)")
            L.append("}")
            return L
        movement_items = ("velocity", "acceleration", "max_acceleration", "max_braking_deceleration",
                          "max_walk_speed", "movement_mode", "is_grounded", "is_crouching")
        needs_movement = self.uses(*movement_items)
        needs_pawn = needs_movement or self.uses("actor_rotation", "control_rotation", "action")
        if needs_pawn:
            L.append(f"{T}const APawn* Pawn = TryGetPawnOwner();")
        if needs_movement:
            L.append(f"{T}const ACharacter* Character = Cast<ACharacter>(Pawn);")
            L.append(f"{T}const UCharacterMovementComponent* Movement = IsValid(Character) ? Character->GetCharacterMovement() : nullptr;")
        if self.action_names():
            L.append(f"{T}const UEnhancedPlayerInput* PlayerInput = DalGetPlayerInput(Pawn);")
        if self.uses("time"):
            L.append(f"{T}const UWorld* World = GetWorld();")
        L.append("")
        for i, h in host:
            default = cpp_value(i.ty, i.default)
            name = i.name
            it = h.item
            if it == "time":
                L.append(f"{T}{name} = World != nullptr ? static_cast<float>(World->GetTimeSeconds()) : 0.0f;")
                continue
            if it == "notify":
                L.append(f"{T}{name} = DalNotify_{h.arg};")
                L.append(f"{T}DalNotify_{h.arg} = false;")
                continue
            movement_expr = {
                "velocity": "FVector3f(Movement->Velocity)",
                "acceleration": "FVector3f(Movement->GetCurrentAcceleration())",
                "max_acceleration": "Movement->GetMaxAcceleration()",
                "max_braking_deceleration": "Movement->GetMaxBrakingDeceleration()",
                "max_walk_speed": "Movement->MaxWalkSpeed",
                "movement_mode": "Movement->MovementMode",
                "is_grounded": "Movement->IsMovingOnGround()",
                "is_crouching": "Movement->IsCrouching()",
            }
            pawn_expr = {
                "actor_rotation": "FQuat4f(Pawn->GetActorQuat())",
                "control_rotation": "FRotator3f(Pawn->GetControlRotation())",
            }
            if it in movement_expr:
                L.append(f"{T}{name} = {default};")
                L.append(f"{T}if (Movement != nullptr)")
                L.append(f"{T}{{")
                L.append(f"{T}{T}{name} = {movement_expr[it]};")
                L.append(f"{T}}}")
            elif it in pawn_expr:
                L.append(f"{T}{name} = {default};")
                L.append(f"{T}if (Pawn != nullptr)")
                L.append(f"{T}{{")
                L.append(f"{T}{T}{name} = {pawn_expr[it]};")
                L.append(f"{T}}}")
            elif it == "action":
                getter = {
                    BOOL: ".Get<bool>()",
                    FLOAT: ".Get<float>()",
                    VEC2: ".Get<FVector2D>()",
                    VEC3: ".Get<FVector>()",
                }[i.ty]
                value = f"PlayerInput->GetActionValue(DalAction_{h.arg}){getter}"
                if i.ty is VEC2:
                    value = f"FVector2f({value})"
                elif i.ty is VEC3:
                    value = f"FVector3f({value})"
                L.append(f"{T}{name} = {default};")
                L.append(f"{T}if (PlayerInput != nullptr && DalAction_{h.arg} != nullptr)")
                L.append(f"{T}{{")
                L.append(f"{T}{T}{name} = {value};")
                L.append(f"{T}}}")
            else:
                raise AssertionError(it)
        L.append("}")
        return L


def generate_cpp(module: ir.Module, options: CppOptions | None = None) -> dict[str, str]:
    """Return ``{filename: text}`` for the generated header, source and the math header."""
    options = options or CppOptions()
    g = CppGen(module, options)
    math_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "cpp", "DalMath.h")
    with open(math_path, "r", encoding="utf-8", newline="") as f:
        math_text = f.read().replace("\r\n", "\n")
    return {
        f"{module.name}.h": g.header(),
        f"{module.name}.cpp": g.source(),
        "DalMath.h": math_text,
    }
