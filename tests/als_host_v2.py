"""The host commands of the ALS character that ``dal/hostapi.py`` does not list yet (als-dal-rewrite.md section 4.1, "V2").

``AlsCharacter.dal`` / ``AlsInput.dal`` issue ``host.set_actor_rotation(...)``, ``host.set_ground_friction(...)`` ... as the
specification names them.  The compiler core knows the commands of v1 / v2 only, so until the owner of ``dal/`` adds these rows
to its tables the tests register them here, at run time and only when the tables do not have them already:

* the Python side: a ``Command`` row in ``hostapi.COMMANDS`` (the checker, the interpreter and the generated classes are table
  driven) and a recorder on ``MockHost``;
* the C++ side: an entry in ``cppbackend.EXEC`` (how the game thread executes the command), the enum member and constructor in the
  generated ``DalHost.h`` and the logged properties of the stand-in engine -- ``patch_cpp_files`` / ``patched_stub_dir``.

Moving this into the compiler means: one row per command in ``hostapi.COMMANDS`` (+ the method on ``HostV1``), one ``EXEC`` entry,
one enum member and one constructor in ``dal/cpp/DalHost.h``, the executor's include / module dependency for the commands that need
the ``DalRuntime`` movement component, and the stand-in members (the strings below are the lines to add).
"""

from __future__ import annotations

import os
import re
import shutil
import tempfile

from dal import cppbackend, hostapi, mockhost
from dal.hostapi import Command
from dal.types import BOOL, FLOAT, ROTATOR

_ALS_MOVEMENT = [
    "if (UDalCharacterMovementComponent* DalAlsMovement = Cast<UDalCharacterMovementComponent>(DalMovement))",
    "{",
]

# name -> (parameter types, required, defaults, doc, ue, C++ executor guards, C++ executor body, C++ constructor kind)
COMMANDS = {
    "set_max_walk_speed_crouched": ((FLOAT,), None, (), "MaxWalkSpeedCrouched of the movement component",
                                    "CharacterMovement->MaxWalkSpeedCrouched = v",
                                    [cppbackend._G_MOVEMENT], ["DalMovement->MaxWalkSpeedCrouched = DalCommand.F;"], "float"),
    "set_max_acceleration_walking": ((FLOAT,), None, (), "MaxAccelerationWalking of ALS's movement component (the acceleration on the ground)",
                                     "UDalCharacterMovementComponent::MaxAccelerationWalking = v (DalRuntime)",
                                     [cppbackend._G_MOVEMENT], [*_ALS_MOVEMENT, "\tDalAlsMovement->MaxAccelerationWalking = DalCommand.F;", "}"], "float"),
    "set_ground_friction": ((FLOAT,), None, (), "GroundFriction of the movement component",
                            "CharacterMovement->GroundFriction = v",
                            [cppbackend._G_MOVEMENT], ["DalMovement->GroundFriction = DalCommand.F;"], "float"),
    "set_braking_friction_factor": ((FLOAT,), None, (), "BrakingFrictionFactor of the movement component",
                                    "CharacterMovement->BrakingFrictionFactor = v",
                                    [cppbackend._G_MOVEMENT], ["DalMovement->BrakingFrictionFactor = DalCommand.F;"], "float"),
    "set_input_blocked": ((BOOL,), None, (), "ALS's movement component drops the movement input while this is set",
                          "UDalCharacterMovementComponent::bInputBlocked = b (DalRuntime)",
                          [cppbackend._G_MOVEMENT], [*_ALS_MOVEMENT, "\tDalAlsMovement->bInputBlocked = DalCommand.B0;", "}"], "bool"),
    "set_actor_rotation": ((ROTATOR, BOOL), 1, (False,), "rotate the actor (no sweep); teleport = true is a teleport (resets foot locking)",
                           "Pawn->SetActorRotation(Rotation, bTeleport ? ETeleportType::TeleportPhysics : ETeleportType::None)",
                           [cppbackend._G_PAWN],
                           ["DalPawn->SetActorRotation(FRotator(DalCommand.R), DalCommand.B0 ? ETeleportType::TeleportPhysics : ETeleportType::None);"],
                           "rotator_bool"),
    "add_controller_yaw_input": ((FLOAT,), None, (), "turn the controller (APawn::AddControllerYawInput)",
                                 "Pawn->AddControllerYawInput(v)", [cppbackend._G_PAWN], ["DalPawn->AddControllerYawInput(DalCommand.F);"], "float"),
    "add_controller_pitch_input": ((FLOAT,), None, (), "pitch the controller (APawn::AddControllerPitchInput)",
                                   "Pawn->AddControllerPitchInput(v)", [cppbackend._G_PAWN], ["DalPawn->AddControllerPitchInput(DalCommand.F);"], "float"),
}


def missing() -> list[str]:
    """The commands of this table the compiler does not know (yet)."""
    return [n for n in COMMANDS if n not in hostapi.COMMANDS]


ADDED: list[str] = []         # the commands this module registered in this process (the compiler did not know them)


def install() -> list[str]:
    """Register the missing commands; returns the names of all commands this module added (also by an earlier call).  Safe to call any number of times."""
    added = missing()
    ADDED.extend(added)
    for name in added:
        params, required, defaults, doc, ue, guards, body, _kind = COMMANDS[name]
        hostapi.COMMANDS[name] = Command(name, tuple(params), required, tuple(defaults), doc, ue)
        cppbackend.EXEC[name] = (list(guards), list(body))
    if added:
        mockhost._install_recorders()
    return list(ADDED)


def uninstall() -> None:
    """Take the registered commands out again (the other test modules check the protocol tables against ``HostV1`` / ``DalHost.h``)."""
    for name in ADDED:
        hostapi.COMMANDS.pop(name, None)
        cppbackend.EXEC.pop(name, None)
        if name in vars(mockhost.MockHost):
            delattr(mockhost.MockHost, name)
    ADDED.clear()


# ---- the C++ side: DalHost.h and the stand-in engine ------------------------------------------------------------------------

def _camel(name: str) -> str:
    return cppbackend.cmd_cpp(name)


def patch_cpp_files(files: dict[str, str], names: list[str]) -> dict[str, str]:
    """``generate_cpp`` output with the enum members and constructors of the commands in ``names`` added to ``DalHost.h``."""
    if "DalHost.h" not in files or not names:
        return files
    text = files["DalHost.h"]
    members = "".join(f"\t\t{_camel(n)},\n" for n in names)
    anchor = "\t\tSetMaxWalkSpeed,\n"
    if anchor not in text:
        raise RuntimeError("DalHost.h has no SetMaxWalkSpeed enum member to anchor on")
    text = text.replace(anchor, members + anchor, 1)
    ctors = []
    for n in names:
        kind, c = COMMANDS[n][-1], _camel(n)
        if kind == "float":
            ctors.append(f"\t\tinline FCommand {c}(float V) {{ return WithFloat(ECommand::{c}, V); }}\n")
        elif kind == "bool":
            ctors.append(f"\t\tinline FCommand {c}(bool B) {{ return WithBool(ECommand::{c}, B); }}\n")
        elif kind == "rotator_bool":
            ctors.append(f"\t\tinline FCommand {c}(const FRotator3f& R, bool bTeleport)\n\t\t{{\n\t\t\tFCommand C = Make(ECommand::{c});\n"
                         "\t\t\tC.R = R;\n\t\t\tC.B0 = bTeleport;\n\t\t\treturn C;\n\t\t}\n")
    anchor = "\t\tinline FCommand SetMaxWalkSpeed(float V) { return WithFloat(ECommand::SetMaxWalkSpeed, V); }\n"
    if anchor not in text:
        raise RuntimeError("DalHost.h has no SetMaxWalkSpeed constructor to anchor on")
    text = text.replace(anchor, anchor + "".join(ctors), 1)
    out = dict(files)
    out["DalHost.h"] = text
    return out


_STUB_PATCHES = (
    # (anchor, text added after the anchor)
    ("\tFLoggedFloat AirControl{\"set_air_control\"};\n",
     "\tFLoggedFloat MaxWalkSpeedCrouched{\"set_max_walk_speed_crouched\"};\n"
     "\tFLoggedFloat GroundFriction{\"set_ground_friction\"};\n"
     "\tFLoggedFloat BrakingFrictionFactor{\"set_braking_friction_factor\"};\n"),
    ("class UDalCharacterMovementComponent : public UCharacterMovementComponent\n{\npublic:\n",
     "\tFLoggedFloat MaxAccelerationWalking{\"set_max_acceleration_walking\"};\n"
     "\tFLoggedBool bInputBlocked{\"set_input_blocked\"};\n"),
    ("class AActor : public UObject\n{\npublic:\n",
     "\tvirtual void SetActorRotation(FRotator NewRotation, ETeleportType Teleport = ETeleportType::None)\n"
     "\t{\n\t\tStubLog::Emit(\"set_actor_rotation\", {NewRotation.Pitch, NewRotation.Yaw, NewRotation.Roll, Teleport != ETeleportType::None});\n\t}\n"),
    ("class APawn : public AActor\n{\npublic:\n",
     "\tvirtual void AddControllerYawInput(float Val) { StubLog::Emit(\"add_controller_yaw_input\", {Val}); }\n"
     "\tvirtual void AddControllerPitchInput(float Val) { StubLog::Emit(\"add_controller_pitch_input\", {Val}); }\n"),
    ("enum ECollisionChannel : int\n", "enum class ETeleportType : uint8\n{\n\tNone,\n\tTeleportPhysics,\n\tResetPhysics,\n};\n\n"),
)


def patch_stub_text(text: str) -> str:
    """The stand-in engine header with the logged members of the commands above (idempotent)."""
    if "set_actor_rotation" in text and "ETeleportType" in text:
        return text
    for anchor, addition in _STUB_PATCHES:
        if anchor not in text:
            raise RuntimeError(f"StubEngine.h has no {anchor.strip()!r} to anchor on")
        if anchor.startswith("enum ECollisionChannel"):
            text = text.replace(anchor, addition + anchor, 1)         # a type: before the anchor
        else:
            text = text.replace(anchor, anchor + addition, 1)
    return text


def patched_stub_dir(stub_dir: str) -> str:
    """A temporary copy of the stub folder with StubEngine.h patched; the caller deletes it."""
    work = tempfile.mkdtemp(prefix="dal_als_stub_")
    for name in os.listdir(stub_dir):
        src = os.path.join(stub_dir, name)
        if os.path.isfile(src):
            shutil.copy(src, os.path.join(work, name))
    path = os.path.join(work, "StubEngine.h")
    with open(path, encoding="utf-8", newline="") as f:
        text = f.read()
    with open(path, "w", encoding="utf-8", newline="") as f:
        f.write(patch_stub_text(text))
    return work


# ---- running the C++ tests with these commands ---------------------------------------------------------------------------------

class cpp_environment:
    """Context manager: ``cpp_driver`` / ``cpp_system`` build against a stand-in engine and a ``DalHost.h`` that know the commands above.

    ``settings`` is ``{logic: {param: data}}``: the data of the settings params, given to the logics before BeginPlay.  ``driver_patch`` is an
    optional ``(driver text, modules, frames) -> driver text``.  ``run`` builds and runs a system (``cpp_system.build_and_run_system``); when
    that function takes the settings itself (``params=``) it is used, otherwise the driver text is patched here."""

    def __init__(self, settings: dict[str, dict] | None = None, driver_patch=None):
        self.settings = settings or {}
        self.driver_patch = driver_patch
        self.added: list[str] = []
        self._saved: dict = {}
        self._stub = None

    def __enter__(self):
        import inspect

        import cpp_driver
        import cpp_system

        self.added = install()
        self._saved = {"stub": cpp_driver.STUB, "sys_gen": cpp_system.generate_cpp, "drv_gen": cpp_driver.generate_cpp,
                       "driver": cpp_system.make_system_driver}
        self._stub = patched_stub_dir(cpp_driver.STUB)
        cpp_driver.STUB = self._stub

        def generate(module, options=None, _orig=self._saved["sys_gen"]):
            return patch_cpp_files(_orig(module, options) if options is not None else _orig(module), self.added)

        cpp_system.generate_cpp = generate
        cpp_driver.generate_cpp = generate
        self.native = "params" in inspect.signature(cpp_system.build_and_run_system).parameters

        def driver(modules, frames, schedule, *args, _orig=self._saved["driver"], **kwargs):
            text = _orig(modules, frames, schedule, *args, **kwargs)
            if not self.native:
                for name, params in self.settings.items():
                    lines = [ln.replace("Obj.", f"L_{name}.") for ln in cpp_driver.settings_setup(modules[name], params)]
                    marker = f"\tL_{name}.BeginPlay();\n"
                    if marker not in text:
                        raise RuntimeError(f"the system driver has no {marker.strip()!r} to put the settings before")
                    text = text.replace(marker, "\n".join(lines) + "\n" + marker, 1)
            if self.driver_patch is not None:
                text = self.driver_patch(text, modules, frames)
            return text

        cpp_system.make_system_driver = driver
        return self

    def run(self, modules, frames, schedule=None, **kwargs):
        import cpp_system

        if self.native and self.settings:
            kwargs["params"] = self.settings
        return cpp_system.build_and_run_system(modules, frames, schedule, **kwargs)

    def __exit__(self, *exc):
        import cpp_driver
        import cpp_system

        cpp_driver.STUB = self._saved["stub"]
        cpp_system.generate_cpp = self._saved["sys_gen"]
        cpp_driver.generate_cpp = self._saved["drv_gen"]
        cpp_system.make_system_driver = self._saved["driver"]
        if self._stub:
            shutil.rmtree(self._stub, ignore_errors=True)
        return False
