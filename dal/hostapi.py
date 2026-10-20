"""Host interface v0: the read-only values a ``.dal`` program may bind to inputs.

``HOST_ITEMS`` is the contract.  Each host implements every item once:

* Unreal: the C++ backend reads the owning pawn / ``UCharacterMovementComponent``
  on the game thread (see ``cppbackend.py``);
* Blender: DreamVEd's 3C replica supplies an object with the same attribute names
  (subclass ``HostV0``).
"""

from __future__ import annotations

from dataclasses import dataclass

from . import runtime as rt
from .types import BOOL, FLOAT, MOVEMENT_MODE, QUAT, ROTATOR, VEC2, VEC3, Type


@dataclass(frozen=True)
class HostItem:
    name: str
    ty: Type | None                 # fixed type, or None when the declaration chooses (action)
    arg: bool = False               # takes a string argument: host.action("Move")
    allowed: tuple[Type, ...] = ()  # accepted declared types when ty is None
    unit: str = ""
    doc: str = ""
    ue: str = ""                    # how the C++ backend reads it (documentation)


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
        HostItem("actor_rotation", QUAT, doc="world rotation of the owning actor",
                 ue="Pawn->GetActorQuat()"),
        HostItem("control_rotation", ROTATOR, unit="deg", doc="controller rotation (pitch, yaw, roll)",
                 ue="Pawn->GetControlRotation()"),
        HostItem("action", None, arg=True, allowed=(BOOL, FLOAT, VEC2, VEC3),
                 doc='Enhanced Input action value: host.action("Move")',
                 ue="UEnhancedPlayerInput::GetActionValue(Action)"),
        HostItem("notify", BOOL, arg=True,
                 doc='true for the one update after the animation notify fired: host.notify("Footstep")',
                 ue="UAnimInstance::AnimNotify_<Name>()"),
    )
}


class HostV0:
    """Base class for Python hosts.  Every item returns a neutral default; override what you have."""

    time = 0.0
    velocity = rt.VEC3_ZERO
    acceleration = rt.VEC3_ZERO
    max_acceleration = 0.0
    max_braking_deceleration = 0.0
    max_walk_speed = 0.0
    movement_mode = rt.MovementMode.None_
    is_grounded = False
    is_crouching = False
    actor_rotation = rt.QUAT_IDENTITY
    control_rotation = rt.ROTATOR_ZERO

    def action(self, name: str, ty: str):
        """Value of the input action ``name`` as DAL type ``ty`` ('bool', 'float', 'vec2', 'vec3')."""
        return {"bool": False, "float": 0.0, "vec2": rt.VEC2_ZERO, "vec3": rt.VEC3_ZERO}[ty]

    def notify(self, name: str) -> bool:
        """True if animation notify ``name`` fired since the previous update (consumed by the call)."""
        return False
