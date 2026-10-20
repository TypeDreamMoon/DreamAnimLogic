"""The host interface: what a ``.dal`` program can read from, ask of, and tell the host.

Three layers, all driven by the tables below (the compiler checks programs against them and both
backends and every host implement them):

* **Reads** (``HOST_ITEMS``): values bound to inputs with ``input X: type <- host.item``; sampled once per
  update (v0, plus the montage reads of v1).
* **Commands** (``COMMANDS``): ``host.name(args);`` statements. They never take effect inside the update:
  they are queued, and the host executes them in order after the update (Unreal: on the game thread;
  Python: ``LogicBase.update`` calls the host methods when the update has finished).
* **Queries** (``QUERIES``): ``host.name(args)`` expressions that need an answer *now* (traces, target
  search). Only ``logic X : actor;`` (game thread) may use them.
* **Events**: the host tells the logic that something happened (``poll_events``); the logic reacts in
  ``on ...`` handlers at the start of its next update. ``EVENTS`` lists them.

``HostV1`` is the protocol a Python host (the Blender side) implements; ``HostV0`` is the read-only
subset. Defaults are neutral, so a host only overrides what it has. Unreal is the other implementation of
the same protocol (the generated C++, see ``cppbackend.py``).
"""

from __future__ import annotations

from dataclasses import dataclass

from . import runtime as rt
from .types import BOOL, FLOAT, HIT, INT, MOVEMENT_MODE, QUAT, ROTATOR, VEC2, VEC3, Type


# ---- reads ---------------------------------------------------------------------------------------------------

@dataclass(frozen=True)
class HostItem:
    name: str
    ty: Type | None
    arg: bool = False               # takes an argument: host.action("Move"), host.montage_playing(Light1)
    allowed: tuple[Type, ...] = ()  # accepted declared types when ty is None
    unit: str = ""
    doc: str = ""
    ue: str = ""                    # how the C++ backend reads it (documentation)
    arg_kind: str = ""              # 'string' (a name in quotes), 'action' (quoted name or a declared action), 'montage'
    section: bool = False           # the value is a section of the argument montage (type chosen by the montage)


HOST_ITEMS: dict[str, HostItem] = {
    i.name: i
    for i in (
        HostItem("time", FLOAT, unit="s", doc="world time in seconds",
                 ue="GetWorld()->GetTimeSeconds()"),
        HostItem("velocity", VEC3, unit="cm/s", doc="character velocity, world space",
                 ue="CharacterMovement->Velocity"),
        HostItem("acceleration", VEC3, unit="cm/s^2", doc="current input acceleration, world space",
                 ue="CharacterMovement->GetCurrentAcceleration()"),
        HostItem("max_acceleration", FLOAT, unit="cm/s^2", doc="CMC MaxAcceleration",
                 ue="CharacterMovement->GetMaxAcceleration()"),
        HostItem("max_braking_deceleration", FLOAT, unit="cm/s^2", doc="CMC braking deceleration",
                 ue="CharacterMovement->GetMaxBrakingDeceleration()"),
        HostItem("max_walk_speed", FLOAT, unit="cm/s", doc="CMC MaxWalkSpeed",
                 ue="CharacterMovement->MaxWalkSpeed"),
        HostItem("movement_mode", MOVEMENT_MODE, doc="CMC movement mode (EMovementMode)",
                 ue="CharacterMovement->MovementMode"),
        HostItem("is_grounded", BOOL, doc="moving on the ground (walking or nav-walking)",
                 ue="CharacterMovement->IsMovingOnGround()"),
        HostItem("is_crouching", BOOL, doc="character is crouched",
                 ue="CharacterMovement->IsCrouching()"),
        HostItem("actor_location", VEC3, unit="cm", doc="world location of the owning actor",
                 ue="Pawn->GetActorLocation()"),
        HostItem("actor_rotation", QUAT, doc="world rotation of the owning actor",
                 ue="Pawn->GetActorQuat()"),
        HostItem("control_rotation", ROTATOR, unit="deg", doc="controller rotation (pitch, yaw, roll)",
                 ue="Pawn->GetControlRotation()"),
        HostItem("action", None, arg=True, arg_kind="action", allowed=(BOOL, FLOAT, VEC2, VEC3),
                 doc='Enhanced Input action value: host.action("Move") or host.action(Move) for a declared action',
                 ue="UEnhancedPlayerInput::GetActionValue(Action)"),
        HostItem("notify", BOOL, arg=True, arg_kind="string",
                 doc='true for the one update after the animation notify fired: host.notify("Footstep")',
                 ue="UAnimInstance::AnimNotify_<Name>()"),
        # v1: montage state (the montage is a declared `montage`; Light1.playing etc. is the usual way to read these)
        HostItem("montage_playing", BOOL, arg=True, arg_kind="montage", doc="montage is playing (not blending out)",
                 ue="AnimInstance->Montage_IsPlaying(Montage)"),
        HostItem("montage_active", BOOL, arg=True, arg_kind="montage", doc="montage has an instance (also while blending out)",
                 ue="AnimInstance->Montage_IsActive(Montage)"),
        HostItem("montage_position", FLOAT, arg=True, arg_kind="montage", unit="s", doc="playback position in the montage",
                 ue="AnimInstance->Montage_GetPosition(Montage)"),
        HostItem("montage_section", None, arg=True, arg_kind="montage", section=True,
                 doc="current section of the montage (None when it is not playing)",
                 ue="AnimInstance->Montage_GetCurrentSection(Montage)"),
    )
}


# ---- commands --------------------------------------------------------------------------------------------------

@dataclass(frozen=True)
class Command:
    """``host.<name>(...)`` statement.

    ``params`` are types, or the markers ``"montage"`` (a declared montage) and ``"section"`` (a section of the
    montage in the first argument). The first ``required`` parameters must be written; the rest take
    ``defaults`` (``None`` for a section means no section).
    """

    name: str
    params: tuple
    required: int | None = None
    defaults: tuple = ()
    doc: str = ""
    ue: str = ""

    @property
    def min_args(self) -> int:
        return len(self.params) if self.required is None else self.required


def _c(name, params=(), required=None, defaults=(), doc="", ue=""):
    return Command(name, tuple(params), required, tuple(defaults), doc, ue)


COMMANDS: dict[str, Command] = {
    c.name: c
    for c in (
        # character movement (UCharacterMovementComponent / ACharacter)
        _c("set_max_walk_speed", [FLOAT], ue="CharacterMovement->MaxWalkSpeed = v"),
        _c("set_max_acceleration", [FLOAT], ue="CharacterMovement->MaxAcceleration = v"),
        _c("set_braking_deceleration_walking", [FLOAT], ue="CharacterMovement->BrakingDecelerationWalking = v"),
        _c("set_gravity_scale", [FLOAT], ue="CharacterMovement->GravityScale = v"),
        _c("set_jump_z_velocity", [FLOAT], ue="CharacterMovement->JumpZVelocity = v"),
        _c("set_air_control", [FLOAT], ue="CharacterMovement->AirControl = v"),
        _c("set_rotation_rate", [ROTATOR], ue="CharacterMovement->RotationRate = r"),
        _c("set_orient_rotation_to_movement", [BOOL], ue="CharacterMovement->bOrientRotationToMovement = b"),
        _c("set_use_controller_desired_rotation", [BOOL], ue="CharacterMovement->bUseControllerDesiredRotation = b"),
        _c("set_movement_mode", [MOVEMENT_MODE], ue="CharacterMovement->SetMovementMode(Mode)"),
        _c("jump", [], ue="Character->Jump()"),
        _c("stop_jumping", [], ue="Character->StopJumping()"),
        _c("crouch", [], ue="Character->Crouch()"),
        _c("uncrouch", [], ue="Character->UnCrouch()"),
        _c("add_movement_input", [VEC3, FLOAT], 1, (1.0,), ue="Pawn->AddMovementInput(Direction, Scale)"),
        _c("launch", [VEC3, BOOL, BOOL], 1, (False, False), ue="Character->LaunchCharacter(Velocity, bXYOverride, bZOverride)"),
        # montages (UAnimInstance::Montage_*)
        _c("montage_play", ["montage", FLOAT, "section"], 1, (1.0, None),
           doc="play a montage; optionally faster / slower and starting at a section",
           ue="AnimInstance->Montage_Play(Montage, Rate); Montage_JumpToSection(Section, Montage)"),
        _c("montage_stop", ["montage", FLOAT], 1, (-1.0,),
           doc="blend a montage out; a negative time uses the montage's own blend out",
           ue="AnimInstance->Montage_Stop(BlendOut, Montage)"),
        _c("montage_stop_all", [FLOAT], 0, (-1.0,), ue="AnimInstance->Montage_Stop(BlendOut)"),
        _c("montage_jump_to_section", ["montage", "section"], ue="AnimInstance->Montage_JumpToSection(Section, Montage)"),
        _c("montage_set_next_section", ["montage", "section", "section"],
           ue="AnimInstance->Montage_SetNextSection(From, To, Montage)"),
        _c("montage_set_play_rate", ["montage", FLOAT], ue="AnimInstance->Montage_SetPlayRate(Montage, Rate)"),
        # controller / camera (v2)
        _c("set_control_rotation", [ROTATOR], ue="Controller->SetControlRotation(Rotation)"),
        _c("camera_set_arm_length", [FLOAT], ue="SpringArm->TargetArmLength = v"),
        _c("camera_set_socket_offset", [VEC3], ue="SpringArm->SocketOffset = v"),
        _c("camera_set_fov", [FLOAT], ue="Camera->SetFieldOfView(v)"),
    )
}


# ---- queries (actor logic only) ------------------------------------------------------------------------------------

@dataclass(frozen=True)
class Query:
    name: str
    params: tuple
    ret: Type
    doc: str = ""
    ue: str = ""


QUERIES: dict[str, Query] = {
    q.name: q
    for q in (
        Query("trace_line", (VEC3, VEC3), HIT, "line trace from start to end against the world's blocking geometry",
              "World->LineTraceSingleByChannel(Hit, Start, End, ECC_Visibility, IgnoreOwner)"),
        Query("trace_sphere", (VEC3, VEC3, FLOAT), HIT, "sphere sweep from start to end",
              "World->SweepSingleByChannel(Hit, Start, End, FQuat::Identity, ECC_Visibility, Sphere(Radius), IgnoreOwner)"),
        Query("target_count", (), INT, "number of targetable actors (tag DalTarget in Unreal)", "tagged actors, refreshed each tick"),
        Query("target_id", (INT,), INT, "stable id of the i-th target", "AActor::GetUniqueID()"),
        Query("target_location", (INT,), VEC3, "location of the i-th target", "Actor->GetActorLocation()"),
        Query("target_index", (INT,), INT, "index of the target with this id, or -1 if it is gone", "lookup by id"),
    )
}


# ---- events ---------------------------------------------------------------------------------------------------------------------

EVENTS: dict[str, str] = {
    "notify": "an animation notify named N fired: ('notify', N)",
    "notify_begin": "a notify state / window named N began: ('notify_begin', N)",
    "notify_end": "a notify state / window named N ended: ('notify_end', N)",
    "montage_ended": "a montage ended: ('montage_ended', M, interrupted)",
    "montage_blending_out": "a montage started blending out: ('montage_blending_out', M, interrupted)",
    "input": "an input action changed phase: ('input', A, phase, value) with phase started / triggered / ongoing / completed / canceled",
}


# ---- the Python protocol --------------------------------------------------------------------------------------------------------------

class HostV0:
    """Base class for Python hosts that only supply values.  Every item returns a neutral default."""

    time = 0.0
    velocity = rt.VEC3_ZERO
    acceleration = rt.VEC3_ZERO
    max_acceleration = 0.0
    max_braking_deceleration = 0.0
    max_walk_speed = 0.0
    movement_mode = rt.MovementMode.None_
    is_grounded = False
    is_crouching = False
    actor_location = rt.VEC3_ZERO
    actor_rotation = rt.QUAT_IDENTITY
    control_rotation = rt.ROTATOR_ZERO

    def action(self, name: str, ty: str):
        """Value of the input action ``name`` as DAL type ``ty`` ('bool', 'float', 'vec2', 'vec3')."""
        return {"bool": False, "float": 0.0, "vec2": rt.VEC2_ZERO, "vec3": rt.VEC3_ZERO}[ty]

    def notify(self, name: str) -> bool:
        """True if animation notify ``name`` fired since the previous update (consumed by the call)."""
        return False


class HostV1(HostV0):
    """The full host protocol: reads, queries, events and commands.

    A Blender adapter subclasses this and wires it to DreamVEd's runtime; the comments name the call each
    method maps to (``manager`` = ``anim.runtime.montage.MontageManager``, ``cm`` = ``anim.c3.movement.CharacterMovement``,
    ``world`` = ``anim.c3.collision.CollisionWorld``, ``arm`` = ``anim.c3.camera.SpringArm``).  Montages and
    actions are identified by the names used in the ``.dal`` file; section names are the section names of the
    montage asset.
    """

    # ---- reads: montage state ------------------------------------------------------------------------------
    def montage_playing(self, name: str) -> bool:
        """manager.is_playing(montage)"""
        return False

    def montage_active(self, name: str) -> bool:
        """a montage instance exists (also while blending out): any(i.is_valid() for i in manager.instances if i.montage is montage)"""
        return False

    def montage_position(self, name: str) -> float:
        """manager.position(montage)"""
        return 0.0

    def montage_section(self, name: str):
        """manager.current_section(montage); None when not playing"""
        return None

    # ---- queries (actor logic) -------------------------------------------------------------------------------
    def trace_line(self, start, end):
        """world.line_trace(start, end) -> rt.Hit (blocking, point, normal, distance, time)"""
        return rt.Hit()

    def trace_sphere(self, start, end, radius: float):
        """world.sweep_sphere(start, end, radius) -> rt.Hit"""
        return rt.Hit()

    def target_count(self) -> int:
        return 0

    def target_id(self, index: int) -> int:
        return -1

    def target_location(self, index: int):
        return rt.VEC3_ZERO

    def target_index(self, target_id: int) -> int:
        return -1

    # ---- events -----------------------------------------------------------------------------------------------
    def poll_events(self) -> list:
        """Events since the last call, oldest first, as tuples (see EVENTS).  Called once per update."""
        return []

    # ---- commands: character movement ---------------------------------------------------------------------------
    def set_max_walk_speed(self, value: float) -> None:
        """cm.p.max_walk_speed = value"""

    def set_max_acceleration(self, value: float) -> None:
        """cm.p.max_acceleration = value"""

    def set_braking_deceleration_walking(self, value: float) -> None:
        """cm.p.braking_deceleration_walking = value"""

    def set_gravity_scale(self, value: float) -> None:
        """cm.p.gravity_scale = value"""

    def set_jump_z_velocity(self, value: float) -> None:
        """cm.p.jump_z_velocity = value"""

    def set_air_control(self, value: float) -> None:
        """cm.p.air_control = value"""

    def set_rotation_rate(self, rotation) -> None:
        """cm.p.rotation_rate = (pitch, yaw, roll)"""

    def set_orient_rotation_to_movement(self, value: bool) -> None:
        """cm.p.orient_rotation_to_movement = value"""

    def set_use_controller_desired_rotation(self, value: bool) -> None:
        """cm.p.use_controller_desired_rotation = value"""

    def set_movement_mode(self, mode) -> None:
        """cm.set_movement_mode(int(mode)) -- EMovementMode numbering (None 0, Walking 1, Falling 3 are modelled)"""

    def jump(self) -> None:
        """cm.jump()"""

    def stop_jumping(self) -> None:
        """cm.stop_jumping()"""

    def crouch(self) -> None:
        """cm.crouch()"""

    def uncrouch(self) -> None:
        """cm.uncrouch()"""

    def add_movement_input(self, direction, scale: float) -> None:
        """cm.add_movement_input(direction, scale)"""

    def launch(self, velocity, xy_override: bool, z_override: bool) -> None:
        """cm.launch(velocity, xy_override, z_override)"""

    # ---- commands: montages ------------------------------------------------------------------------------------------
    def montage_play(self, name: str, rate: float, section) -> None:
        """manager.play(montage, play_rate=rate, start_section=section)  (section is None for the start)"""

    def montage_stop(self, name: str, blend_out: float) -> None:
        """manager.stop(blend_out_time=None if blend_out < 0 else blend_out, montage=montage)"""

    def montage_stop_all(self, blend_out: float) -> None:
        """manager.stop(blend_out_time=None if blend_out < 0 else blend_out)"""

    def montage_jump_to_section(self, name: str, section: str) -> None:
        """manager.jump_to_section(section, montage)"""

    def montage_set_next_section(self, name: str, section: str, next_section: str) -> None:
        """manager.set_next_section(section, next_section, montage)"""

    def montage_set_play_rate(self, name: str, rate: float) -> None:
        """manager.set_play_rate(rate, montage)"""

    # ---- commands: controller / camera --------------------------------------------------------------------------------
    def set_control_rotation(self, rotation) -> None:
        """session.controller.control_rotation = Rot(pitch, yaw, roll)"""

    def camera_set_arm_length(self, value: float) -> None:
        """arm.p.target_arm_length = value"""

    def camera_set_socket_offset(self, offset) -> None:
        """arm.p.socket_offset = tuple(offset)"""

    def camera_set_fov(self, value: float) -> None:
        """camera.p.fov = value"""
