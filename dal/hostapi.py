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
from .types import BLEND_OPTION, BOOL, CHANNEL, CHANNEL_SET, FLOAT, HIT, INT, MONTAGE_ASSET, MONTAGE_BLEND_MODE, MOVEMENT_MODE, PRIMITIVE, QUAT, ROOT_MOTION_MODE, ROTATOR, SEQUENCE, TRANSFORM, VEC2, VEC3, Type


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
    nargs: int = 1                  # how many names an item with an argument takes (two for ref_path_child_translation)
    free_name: bool = False         # the argument is a bone / curve name the engine looks up as an FName: words may be separated by single spaces ("VB foot_l")
    consume: str = ""               # consume-on-read: the host method that hands the whole group out once (and clears it)
    part: int = 0                   # which element of the consume method's result this item is


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
        # v2: reads the ALS rewrite needs
        HostItem("capsule_radius", FLOAT, unit="cm", doc="scaled radius of the character's capsule",
                 ue="Character->GetCapsuleComponent()->GetScaledCapsuleRadius()"),
        HostItem("capsule_half_height", FLOAT, unit="cm", doc="scaled half height of the character's capsule",
                 ue="Character->GetCapsuleComponent()->GetScaledCapsuleHalfHeight()"),
        HostItem("actor_scale", FLOAT, doc="Z scale of the character's capsule component",
                 ue="Character->GetCapsuleComponent()->GetComponentScale().Z"),
        # v4: scale of the mesh and time dilation (ALS reads the mesh's scale and divides the delta time by the dilation)
        HostItem("mesh_scale", FLOAT, doc="Z scale of the character's skeletal mesh component (actor_scale is the capsule's)",
                 ue="Mesh->GetComponentScale().Z"),
        HostItem("time_dilation", FLOAT, doc="global time dilation of the world (1 = normal speed)",
                 ue="World->GetWorldSettings()->GetEffectiveTimeDilation()"),
        HostItem("actor_time_dilation", FLOAT, doc="custom time dilation of the character's actor (1 = normal speed)",
                 ue="Pawn->CustomTimeDilation"),
        HostItem("real_delta_time", FLOAT, unit="s", doc="the real (undilated, unclamped by the game) duration of the last frame",
                 ue="World->DeltaRealTimeSeconds"),
        HostItem("walkable_floor_z", FLOAT, doc="smallest Z of a floor normal that can be walked on",
                 ue="CharacterMovement->GetWalkableFloorZ()"),
        HostItem("actor_ready", BOOL, doc="the actor has registered all its components (true for hosts that cannot tell)",
                 ue="Pawn->HasActorRegisteredAllComponents()"),
        HostItem("has_root_motion", BOOL, doc="root motion (from animation or a root motion source) is driving the character",
                 ue="Character->HasAnyRootMotion()"),
        HostItem("movement_mode_custom", INT, doc="CMC custom movement mode (the sub mode of MOVE_Custom)",
                 ue="CharacterMovement->CustomMovementMode"),
        HostItem("anim_curve", FLOAT, arg=True, arg_kind="string", free_name=True,
                 doc='value of an animation curve of the last evaluated pose: host.anim_curve("LayerHead") (0 when the curve is not there)',
                 ue="AnimInstance->GetCurveValue(FName(Name))"),
        HostItem("root_motion_mode", ROOT_MOTION_MODE, doc="how the animation instance extracts root motion (ERootMotionMode)",
                 ue="AnimInstance->RootMotionMode"),
        HostItem("ragdoll_supported", BOOL, doc="the mesh has a physics asset with the pelvis and spine_03 bodies (ragdolling can start)",
                 ue="Mesh->GetPhysicsAsset() has bodies pelvis and spine_03"),
        # v4: what the ragdoll service knows while the character ragdolls (ALS reads the pelvis body, not a bone: the bones lag behind the simulation)
        HostItem("ragdoll_pelvis_location", VEC3, unit="cm", doc="world location of the pelvis body of the ragdoll (zero without a ragdoll service or a pelvis body)",
                 ue="UDalRagdollService::GetPelvisLocation() (DalRuntime): FBodyInstance::GetUnrealWorldTransform() of the pelvis body"),
        HostItem("ragdoll_velocity", VEC3, unit="cm/s", doc="linear velocity of the pelvis body of the ragdoll (zero without a ragdoll service or a pelvis body)",
                 ue="UDalRagdollService::GetPelvisVelocity() (DalRuntime): FBodyInstance::GetUnrealWorldVelocity() of the pelvis body"),
        HostItem("ragdoll_facing_up", BOOL, doc="the ragdoll lies face up: the roll of the pelvis unwound to -180..180 degrees is <= 0 (how ALS decides the get up montage); false without a ragdoll service",
                 ue="UDalRagdollService::IsFacingUp() (DalRuntime): FMath::UnwindDegrees(Mesh->GetSocketTransform(pelvis).Rotator().Roll) <= 0"),
        HostItem("mesh_world_transform", TRANSFORM, doc="world transform of the character mesh", ue="Mesh->GetComponentTransform()"),
        HostItem("bone_component_transform", TRANSFORM, arg=True, arg_kind="string", free_name=True,
                 doc='transform of a bone in component space, from the pose evaluated last: host.bone_component_transform("pelvis")',
                 ue="Mesh->GetSocketTransform(Bone, RTS_Component)"),
        HostItem("bone_world_transform", TRANSFORM, arg=True, arg_kind="string", free_name=True,
                 doc='transform of a bone in world space, from the pose evaluated last: host.bone_world_transform("ik_foot_l")',
                 ue="Mesh->GetSocketTransform(Bone, RTS_World)"),
        HostItem("ref_path_child_translation", VEC3, arg=True, arg_kind="string", nargs=2, unit="cm", free_name=True,
                 doc='host.ref_path_child_translation("calf_l", "thigh_l"): the local reference-pose translation of the bone of the path from the first '
                     "bone up to the second that is a direct child of the second (the thigh axis)",
                 ue="Mesh->GetSkeletalMeshAsset()->GetRefSkeleton(): the reference pose translation of the child of Ancestor on the path from Descendant"),
        HostItem("pre_penetration_velocity", VEC3, unit="cm/s", consume="consume_pre_penetration_velocity", part=1,
                 doc="velocity the movement had before depenetration moved the character; consumed by the read",
                 ue="UDalCharacterMovementComponent::TryConsumePrePenetrationAdjustmentVelocity (DalRuntime)"),
        HostItem("pre_penetration_velocity_valid", BOOL, consume="consume_pre_penetration_velocity", part=0,
                 doc="a pre-penetration velocity was recorded since the last read; consumed by the read",
                 ue="UDalCharacterMovementComponent::TryConsumePrePenetrationAdjustmentVelocity (DalRuntime)"),
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

    ``params`` are types, or the markers ``"montage"`` (a declared montage, or -- v4 -- a ``montage`` asset value), ``"section"``
    (a section of the declared montage in the first argument) and ``"name"`` (a string literal: a slot name) and ``"place"`` (a snapshot state). The first
    ``required`` parameters must be written; the rest take ``defaults`` (``None`` for a section means no section; an enum
    parameter's default is a member name).  ``implicit`` are values the generated code puts in front of the written ones when it
    calls the host (``"source"``: the montage instance that raised the notify the handler is running for), so the host method
    has ``len(implicit) + len(params)`` parameters.  A ``sparse`` command does not send the optional parameters the program
    left out (the host method gives them defaults): how a parameter was added to a command without changing programs that
    do not use it.
    """

    name: str
    params: tuple
    required: int | None = None
    defaults: tuple = ()
    doc: str = ""
    ue: str = ""
    implicit: tuple = ()
    sparse: bool = False

    @property
    def min_args(self) -> int:
        return len(self.params) if self.required is None else self.required


def _c(name, params=(), required=None, defaults=(), doc="", ue="", implicit=(), sparse=False):
    return Command(name, tuple(params), required, tuple(defaults), doc, ue, tuple(implicit), sparse)


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
        # v4: what the ALS character writes (UDalCharacterMovementComponent is the movement component of the DalRuntime module)
        _c("set_max_walk_speed_crouched", [FLOAT], ue="CharacterMovement->MaxWalkSpeedCrouched = v"),
        _c("set_max_acceleration_walking", [FLOAT], doc="the acceleration on the ground (ALS's movement component; get_max_acceleration returns it there)",
           ue="UDalCharacterMovementComponent::MaxAccelerationWalking = v (DalRuntime)"),
        _c("set_ground_friction", [FLOAT], ue="CharacterMovement->GroundFriction = v"),
        _c("set_braking_friction_factor", [FLOAT], ue="CharacterMovement->BrakingFrictionFactor = v"),
        _c("set_input_blocked", [BOOL], doc="the movement component drops the movement input while this is set (ALS's movement component)",
           ue="UDalCharacterMovementComponent::bInputBlocked = b (DalRuntime)"),
        _c("set_actor_rotation", [ROTATOR, BOOL], 1, (False,), doc="rotate the actor without sweeping; teleport = true is a teleport (it resets what is attached to the transform, foot locking)",
           ue="Pawn->SetActorRotation(Rotation, bTeleport ? ETeleportType::TeleportPhysics : ETeleportType::None)"),
        _c("add_controller_yaw_input", [FLOAT], doc="turn the controller (APawn::AddControllerYawInput)", ue="Pawn->AddControllerYawInput(v)"),
        _c("add_controller_pitch_input", [FLOAT], doc="pitch the controller (APawn::AddControllerPitchInput)", ue="Pawn->AddControllerPitchInput(v)"),
        _c("set_rotation_rate", [ROTATOR], ue="CharacterMovement->RotationRate = r"),
        _c("set_orient_rotation_to_movement", [BOOL], ue="CharacterMovement->bOrientRotationToMovement = b"),
        _c("set_use_controller_desired_rotation", [BOOL], ue="CharacterMovement->bUseControllerDesiredRotation = b"),
        _c("set_movement_mode", [MOVEMENT_MODE, INT], 1, (0,), sparse=True,
           doc="the second argument is the custom sub mode (MOVE_Custom's CustomMovementMode); a program that leaves it out sends the mode alone",
           ue="CharacterMovement->SetMovementMode(Mode, SubMode)"),
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
        _c("montage_stop_all", [FLOAT], 0, (-1.0,),
           doc="blend out every playing montage; a negative time uses the blend out of the montage that is playing",
           ue="AnimInstance->Montage_Stop(BlendOut)"),
        _c("montage_jump_to_section", ["montage", "section"], ue="AnimInstance->Montage_JumpToSection(Section, Montage)"),
        _c("montage_set_next_section", ["montage", "section", "section"],
           ue="AnimInstance->Montage_SetNextSection(From, To, Montage)"),
        _c("montage_set_play_rate", ["montage", FLOAT], ue="AnimInstance->Montage_SetPlayRate(Montage, Rate)"),
        # v4: dynamic montages, slots, notify sources, pose snapshots.  The montage of these may also be a `montage` asset value.
        _c("montage_set_position", ["montage", FLOAT], doc="move a playing montage to a position (seconds)", ue="AnimInstance->Montage_SetPosition(Montage, Position)"),
        _c("montage_stop_source", [FLOAT], 0, (-1.0,), implicit=("source",),
           doc="in an `on notify...` handler of a declared notify: blend out the montage instance that raised the notify; a negative time uses the montage's own blend out",
           ue="AnimInstance->GetMontageInstanceForID(Source)->Stop(FMontageBlendSettings(Montage->BlendOut, BlendTime))"),
        _c("play_slot_animation", [SEQUENCE, "name", FLOAT, FLOAT, FLOAT, INT, FLOAT, FLOAT, MONTAGE_BLEND_MODE], 2,
           (0.25, 0.25, 1.0, 1, -1.0, 0.0, "Standard"),
           doc="play an animation sequence as a dynamic montage in a slot: host.play_slot_animation(Sequence, \"Transition\", blend_in, blend_out, rate, loops, "
               "blend_out_trigger_time, start_time, blend_out_mode)",
           ue="AnimInstance->PlaySlotAnimationAsDynamicMontage(Sequence, Slot, BlendIn, BlendOut, Rate, Loops, BlendOutTriggerTime, StartTime) "
              "(with an inertialization blend out: ..._WithBlendSettings)"),
        _c("stop_montages_with_slot", ["name", FLOAT], 1, (-1.0,),
           doc="blend out every active montage that has a track in the slot; a negative time uses each montage's own blend out",
           ue="every FAnimMontageInstance of AnimInstance->MontageInstances whose montage has a slot track named Slot: Stop(blend settings)"),
        _c("snapshot_pose", ["place"], doc="save the current pose into a snapshot state (a state of type snapshot, or a snapshot member of a struct state); "
                                           "the host gets the dotted path of the place; valid(State) is true from the next update (animation logic)",
           ue="AnimInstance->SnapshotPose(<the FPoseSnapshot state or member>)"),
        # v4: root motion override (the character is driven by the logic: climbing; MOVE_Custom with a sub mode is its movement mode)
        _c("root_motion_override", [VEC3, QUAT],
           doc="root motion for the next movement update: velocity (cm/s, world space) and the rotation to apply (a delta of the actor's rotation); "
               "it stays in effect until root_motion_clear()",
           ue="UDalCharacterMovementComponent::SetRootMotionOverride(Velocity, RotationDelta): an override root motion source (DalRuntime)"),
        _c("root_motion_clear", [], doc="end the root motion override", ue="UDalCharacterMovementComponent::ClearRootMotionOverride() (DalRuntime)"),
        # v4: the rest of the character commands of the ALS rewrite (climbing, ragdolling)
        _c("set_movement_mode_locked", [BOOL], doc="while it is set the movement component ignores every SetMovementMode, its own calls included (ALS's movement component)",
           ue="UDalCharacterMovementComponent::bMovementModeLocked = b (DalRuntime)"),
        _c("set_actor_location", [VEC3, BOOL, BOOL], 1, (False, True), doc="move the actor; sweep: stop at what blocks, teleport: reset the physics state (a teleport)",
           ue="Pawn->SetActorLocation(Location, bSweep, nullptr, bTeleport ? ETeleportType::TeleportPhysics : ETeleportType::None)"),
        _c("set_velocity", [VEC3], ue="CharacterMovement->Velocity = v"),
        _c("set_anim_root_motion_translation_scale", [FLOAT], ue="Character->SetAnimRootMotionTranslationScale(Scale)"),
        _c("ragdoll_begin", [BOOL, FLOAT], doc="start ragdolling (the native ragdoll service of the actor); speed_limit caps the speed of the bodies while limit_speed is set",
           ue="UDalRagdollService::Begin(bLimitSpeed, SpeedLimit) (DalRuntime)"),
        _c("ragdoll_end", [], doc="stop ragdolling", ue="UDalRagdollService::End() (DalRuntime)"),
        _c("ragdoll_limit_speed", [FLOAT], doc="the speed the ragdoll's bodies are capped to", ue="UDalRagdollService::LimitSpeed(Speed) (DalRuntime)"),
        _c("ragdoll_set_motor_strength", [FLOAT], doc="strength of the joint motors that make the ragdoll follow its animation pose",
           ue="UDalRagdollService::SetMotorStrength(Strength) (DalRuntime)"),
        _c("ragdoll_add_force", [VEC3, BOOL], 1, (False,), doc="push the ragdoll; spine: apply it to the spine body only",
           ue="UDalRagdollService::AddForce(Force, bSpine) (DalRuntime)"),
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
    thread_safe: bool = False       # an animation instance may ask it on its worker thread (scene queries, component reads)


QUERIES: dict[str, Query] = {
    q.name: q
    for q in (
        Query("trace_line", (VEC3, VEC3), HIT, "line trace from start to end against the world's blocking geometry",
              "World->LineTraceSingleByChannel(Hit, Start, End, DalTraceChannel (ECC_Visibility), IgnoreOwner)"),
        Query("trace_sphere", (VEC3, VEC3, FLOAT), HIT, "sphere sweep from start to end",
              "World->SweepSingleByChannel(Hit, Start, End, FQuat::Identity, DalTraceChannel (ECC_Visibility), Sphere(Radius), IgnoreOwner)"),
        # v3: queries with a channel and the channels that block; an animation instance may use these (the scene is read-locked in UE)
        Query("sweep_capsule", (VEC3, VEC3, FLOAT, FLOAT, CHANNEL, CHANNEL_SET), HIT,
              "sweep a vertical capsule (radius, half height) from start to end; only the listed channels block",
              "World->SweepSingleByChannel(Hit, Start, End, FQuat::Identity, Channel, FCollisionShape::MakeCapsule(Radius, HalfHeight), IgnoreOwner, Responses)",
              True),
        Query("sweep_sphere", (VEC3, VEC3, FLOAT, CHANNEL, CHANNEL_SET), HIT, "sweep a sphere from start to end; only the listed channels block",
              "World->SweepSingleByChannel(Hit, Start, End, FQuat::Identity, Channel, FCollisionShape::MakeSphere(Radius), IgnoreOwner, Responses)", True),
        Query("line_trace", (VEC3, VEC3, CHANNEL), HIT, "line trace on a channel",
              "World->LineTraceSingleByChannel(Hit, Start, End, Channel, IgnoreOwner)", True),
        Query("overlap_capsule", (VEC3, FLOAT, FLOAT, CHANNEL, CHANNEL_SET), BOOL,
              "does a vertical capsule at this center overlap anything that blocks (center, radius, half height)",
              "World->OverlapBlockingTestByChannel(Center, FQuat::Identity, Channel, FCollisionShape::MakeCapsule(Radius, HalfHeight), IgnoreOwner, Responses)", True),
        Query("is_walkable", (HIT,), BOOL, "can the character stand on what this hit touched (a blocking hit whose normal is flat enough)",
              "CharacterMovement->IsWalkable(Hit)", True),
        Query("primitive_valid", (PRIMITIVE,), BOOL, "is the component handle still valid", "IsValid(Component)", True),
        Query("primitive_speed", (PRIMITIVE,), FLOAT, "speed of the component", "Component->GetComponentVelocity().Size()", True),
        Query("primitive_can_step_up", (PRIMITIVE,), BOOL, "can the character step onto the component", "Component->CanCharacterStepUp(Pawn)", True),
        Query("primitive_transform", (PRIMITIVE,), TRANSFORM, "world transform of the component", "Component->GetComponentTransform()", True),
        Query("montage_is_playing", (MONTAGE_ASSET,), BOOL, "is the montage (an asset value) playing", "AnimInstance->Montage_IsPlaying(Montage)", True),
        # v4: what an animation montage asset says about itself (pure, any thread)
        Query("montage_length", (MONTAGE_ASSET,), FLOAT, "play length of the montage in seconds (0 for none)", "Montage->GetPlayLength()", True),
        Query("montage_rate_scale", (MONTAGE_ASSET,), FLOAT, "rate scale of the montage (1 for none)", "Montage->RateScale", True),
        Query("montage_sampling_rate", (MONTAGE_ASSET,), FLOAT, "sampling frame rate of the montage, frames per second (0 for none)",
              "Montage->GetSamplingFrameRate().AsDecimal()", True),
        Query("montage_blend_in_time", (MONTAGE_ASSET,), FLOAT, "blend in time of the montage in seconds", "Montage->BlendIn.GetBlendTime()", True),
        Query("montage_blend_in_option", (MONTAGE_ASSET,), BLEND_OPTION, "blend in easing of the montage", "Montage->BlendIn.GetBlendOption()", True),
        Query("montage_root_transform", (MONTAGE_ASSET, FLOAT), TRANSFORM,
              "absolute root bone transform of the montage at a time (component space), taken from its first slot track "
              "(UAlsMontageUtility::ExtractRootTransformFromMontage)",
              "Segment->GetAnimReference()->ExtractRootTrackTransform(FAnimExtractContext(Segment->ConvertTrackPosToAnimPos(Time)), nullptr)", True),
        Query("montage_last_root_transform", (MONTAGE_ASSET,), TRANSFORM, "absolute root bone transform at the end of the montage's last segment",
              "AnimTrack.AnimSegments.Last(): ExtractRootTrackTransform(FAnimExtractContext(Segment.GetEndPos()), nullptr)", True),
        Query("target_count", (), INT, "number of targetable actors", "actors with the tag DalTargetTag (DalTarget), collected once per tick"),
        Query("target_id", (INT,), INT, "stable id of the i-th target", "AActor::GetUniqueID()"),
        Query("target_location", (INT,), VEC3, "location of the i-th target", "Actor->GetActorLocation()"),
        Query("target_index", (INT,), INT, "index of the target with this id, or -1 if it is gone", "lookup by id"),
    )
}


# ---- events ---------------------------------------------------------------------------------------------------------------------

EVENTS: dict[str, str] = {
    "notify": "an animation notify named N fired: ('notify', N); a declared `notify` class adds its properties: ('notify', N, payload, source)",
    "notify_begin": "a notify state / window named N began: ('notify_begin', N); a declared `notify_state` adds ('notify_begin', N, payload, source)",
    "notify_end": "a notify state / window named N ended: ('notify_end', N) or ('notify_end', N, payload, source)",
    "notify_tick": "a declared notify state N is active, once per update of the montage that plays it: ('notify_tick', N, payload, source)",
    "montage_ended": "a montage ended: ('montage_ended', M, interrupted)",
    "montage_blending_out": "a montage started blending out: ('montage_blending_out', M, interrupted)",
    "input": "an input action changed phase: ('input', A, phase, value) with phase started / triggered / ongoing / completed / canceled",
    # v4: hooks of the character (the movement component and the character class tell the logic)
    "movement_mode_changed": "the movement mode changed: ('movement_mode_changed', previous, new) with the MovementMode values",
    "jumped": "the character started a jump: ('jumped',)",
    "landed": "the character landed: ('landed', hit)",
    "crouch_started": "the character crouched: ('crouch_started',)",
    "crouch_ended": "the character stood up from a crouch: ('crouch_ended',)",
    "teleported": "the character was moved without sweeping (a teleport): ('teleported',)",
    "physics_rotation": "the movement component finished its rotation update: ('physics_rotation', delta time)",
}

# the events without a name (`on jumped { ... }`): their handlers take these parameters, whose names the program chooses
HOOK_EVENTS: dict[str, tuple[Type, ...]] = {
    "movement_mode_changed": (MOVEMENT_MODE, MOVEMENT_MODE),
    "jumped": (),
    "landed": (HIT,),
    "crouch_started": (),
    "crouch_ended": (),
    "teleported": (),
    "physics_rotation": (FLOAT,),
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
    capsule_radius = 0.0
    capsule_half_height = 0.0
    actor_scale = 1.0
    mesh_scale = 1.0
    time_dilation = 1.0
    actor_time_dilation = 1.0
    real_delta_time = 0.0
    walkable_floor_z = 0.71
    actor_ready = True
    has_root_motion = False
    movement_mode_custom = 0
    pre_penetration_velocity = rt.VEC3_ZERO
    pre_penetration_velocity_valid = False
    root_motion_mode = rt.RootMotionMode.RootMotionFromMontagesOnly
    ragdoll_supported = False
    ragdoll_pelvis_location = rt.VEC3_ZERO
    ragdoll_velocity = rt.VEC3_ZERO
    ragdoll_facing_up = False
    mesh_world_transform = rt.Transform()

    def action(self, name: str, ty: str):
        """Value of the input action ``name`` as DAL type ``ty`` ('bool', 'float', 'vec2', 'vec3').

        Blender adapter: ``pi.action_value(name)`` (``c3.input.EnhancedInput``) -> ``get_bool()`` / ``get_float()`` / ``get_vec2()``.
        """
        return {"bool": False, "float": 0.0, "vec2": rt.VEC2_ZERO, "vec3": rt.VEC3_ZERO}[ty]

    def notify(self, name: str) -> bool:
        """True if animation notify ``name`` fired since the previous update (consumed by the call)."""
        return False

    def anim_curve(self, name: str) -> float:
        """Value of the animation curve ``name`` in the pose evaluated last (0 when the curve is not there).

        Blender adapter: ``AnimInstance.get_curve_value(name)`` (``anim/runtime/instance.py``)."""
        return 0.0

    def bone_component_transform(self, name: str):
        """Mesh->GetSocketTransform(name, RTS_Component): the bone's transform in the pose evaluated last (identity when the bone is not known).

        Blender adapter: the component-space pose of the last evaluation (``AnimResult.comp``)."""
        return rt.Transform()

    def bone_world_transform(self, name: str):
        """Mesh->GetSocketTransform(name, RTS_World): ``inst.component_transform`` composed with the component-space pose (identity when unknown)."""
        return rt.Transform()

    def ref_path_child_translation(self, descendant: str, ancestor: str):
        """The local reference-pose translation of the bone that is a direct child of ``ancestor`` on the path up from ``descendant``
        (zero when there is no such path).  Blender adapter: ``Skeleton.parents`` and the reference pose."""
        return rt.VEC3_ZERO

    def consume_pre_penetration_velocity(self):
        """``(valid, velocity)`` of the pre-penetration velocity recorded since the last call (zero when there is none); the call clears it.

        Blender adapter: the value ``c3 CharacterMovement._phys_walking`` stores as the move velocity before depenetration.
        Unreal: ``UDalCharacterMovementComponent::TryConsumePrePenetrationAdjustmentVelocity`` (module DalRuntime)."""
        valid, velocity = bool(self.pre_penetration_velocity_valid), self.pre_penetration_velocity
        self.pre_penetration_velocity_valid, self.pre_penetration_velocity = False, rt.VEC3_ZERO
        return valid, (velocity if valid else rt.VEC3_ZERO)           # no recorded value reads as zero


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
        """c3.collision ``world.line_trace(start, end, ignore=owner)`` -> ``rt.Hit(h.blocking_hit, h.impact_point, h.impact_normal, h.distance, h.time)``"""
        return rt.Hit()

    def trace_sphere(self, start, end, radius: float):
        """c3.collision ``world.sweep_sphere(start, end, radius, ignore=owner)`` -> ``rt.Hit`` as for ``trace_line``"""
        return rt.Hit()

    # v3 queries: the channel and the blocking channels are accepted; a host without collision channels (Blender) ignores them
    def sweep_capsule(self, start, end, radius: float, half_height: float, channel, responses):
        """c3.collision ``world.sweep_capsule(start, end, radius, half_height, ignore=owner)`` -> ``rt.Hit`` (location, start_penetrating,
        primitive = the shape object, face_normal)"""
        return rt.Hit()

    def sweep_sphere(self, start, end, radius: float, channel, responses):
        """c3.collision ``world.sweep_sphere(start, end, radius, ignore=owner)`` -> ``rt.Hit`` as for ``sweep_capsule``"""
        return rt.Hit()

    def line_trace(self, start, end, channel):
        """c3.collision ``world.line_trace(start, end, ignore=owner)`` -> ``rt.Hit`` as for ``sweep_capsule``"""
        return rt.Hit()

    def overlap_capsule(self, center, radius: float, half_height: float, channel, responses) -> bool:
        """c3.collision ``world.overlaps_capsule(center, radius, half_height, ignore=owner)``"""
        return False

    def is_walkable(self, hit) -> bool:
        """c3.movement ``cm._is_walkable(hit)``: a blocking hit that does not start inside the shape and whose normal is flat enough
        (``normal.z >= max(KINDA_SMALL_NUMBER, walkable_floor_z)``)."""
        return bool(hit.blocking and not hit.start_penetrating and hit.normal.z >= max(rt.KINDA_SMALL_NUMBER, self.walkable_floor_z))

    def primitive_valid(self, primitive) -> bool:
        """the handle names a shape of the scene"""
        return primitive is not None

    def primitive_speed(self, primitive) -> float:
        """speed of the shape (0: the shapes of the scene are static)"""
        return 0.0

    def primitive_can_step_up(self, primitive) -> bool:
        """``Shape.can_step_up`` (True for a shape that has no flag, False for no handle)"""
        return primitive is not None

    def primitive_transform(self, primitive):
        """the shape's world transform (identity when it is not known)"""
        return rt.Transform()

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
        """Events since the last call, oldest first, as tuples (see EVENTS).  Called once per update.

        Blender adapter: drain ``manager.events`` -- ``{"type": "ended", "montage": M, "interrupted": b}`` becomes
        ``("montage_ended", M, b)`` and ``"blend_out_started"`` becomes ``("montage_blending_out", M, b)`` -- and turn the
        notifies of the montage's notify queue (``manager.pending_notifies``) into ``("notify", N)`` for point notifies and
        ``("notify_begin", N)`` / ``("notify_end", N)`` for notify states.  Input actions: bind the declared actions with
        ``pi.bind(A, Event.STARTED / TRIGGERED / ONGOING / COMPLETED / CANCELED, callback)`` (``c3.input``) and queue
        ``("input", A, "started" / "triggered" / ..., value)`` from the callbacks (``value`` = the action value).

        The hooks of the character (``HOOK_EVENTS``, v4) come from the movement component: ``("movement_mode_changed", previous, new)``
        (the ``MovementMode`` values) for ``c3`` ``mode_changed``, ``("jumped",)`` for ``jumped``, ``("landed", hit)`` for ``landed``
        (``rt.Hit(h.blocking_hit, h.impact_point, h.impact_normal, h.distance, h.time, ...)``), ``("crouch_started",)`` /
        ``("crouch_ended",)`` and ``("teleported",)``.  A handler that names parameters gets those values; a host may leave trailing
        payload values out (they read as zero).

        The notifies of the classes a program declares (``notify`` / ``notify_state``) are events with the properties of the notify
        and the montage instance that raised it: ``("notify", Name, {"Member": value, ...}, instance)``, likewise ``notify_begin``,
        ``notify_end`` and, once per update of the montage while the state is active, ``notify_tick``.  The properties are plain data
        (floats, bools, ints, tuples; enum members by name or index, tags by full name, assets by name) and missing members keep the
        declared defaults; ``instance`` (any value, ``None`` when no montage raised it) is what ``host.montage_stop_source`` hands back
        to the host.  Blender: the notify queue of ``runtime/montage.py`` fills the payload from the ``properties`` of the exported notify.
        """
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

    def set_max_walk_speed_crouched(self, value: float) -> None:
        """cm.p.max_walk_speed_crouched = value"""

    def set_max_acceleration_walking(self, value: float) -> None:
        """cm.p.max_acceleration_walking = value (c3 H2.1: ``get_max_acceleration()`` returns it on the ground)"""

    def set_ground_friction(self, value: float) -> None:
        """cm.p.ground_friction = value"""

    def set_braking_friction_factor(self, value: float) -> None:
        """cm.p.braking_friction_factor = value"""

    def set_input_blocked(self, value: bool) -> None:
        """cm.input_blocked = value (c3 H2.2)"""

    def set_actor_rotation(self, rotation, teleport: bool) -> None:
        """cm.rotation = Rot(pitch, yaw, roll); ``teleport`` also sets ``just_teleported`` (c3: ``set_actor_rotation(rot, teleport)``)"""

    def add_controller_yaw_input(self, value: float) -> None:
        """session.controller.add_yaw_input(value)"""

    def add_controller_pitch_input(self, value: float) -> None:
        """session.controller.add_pitch_input(value)"""

    def set_rotation_rate(self, rotation) -> None:
        """cm.p.rotation_rate = (pitch, yaw, roll)"""

    def set_orient_rotation_to_movement(self, value: bool) -> None:
        """cm.p.orient_rotation_to_movement = value"""

    def set_use_controller_desired_rotation(self, value: bool) -> None:
        """cm.p.use_controller_desired_rotation = value"""

    def set_movement_mode(self, mode, sub=0) -> None:
        """cm.set_movement_mode(int(mode)) -- EMovementMode numbering (None 0, Walking 1, Falling 3 are modelled).  ``sub`` is the custom sub mode:
        a program that does not write it does not send it (``sparse``), so a host written for the one-argument command keeps working."""

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

    def montage_set_position(self, name: str, position: float) -> None:
        """MontageManager: set the position of the newest instance of the montage (``MontageInstance.position = position``).
        ``name`` is a declared montage's name or, for a ``montage`` asset value, the asset's name (None: nothing to do)."""

    def montage_stop_source(self, source, blend_out: float) -> None:
        """MontageManager: blend out the montage instance ``source`` (the value the notify event carried as its 4th element) with
        ``blend_out`` seconds (negative: the montage's own blend out); an unknown or finished instance is ignored."""

    def play_slot_animation(self, sequence, slot: str, blend_in: float, blend_out: float, rate: float, loops: int,
                            blend_out_trigger: float, start_time: float, blend_out_mode) -> None:
        """A dynamic montage of the animation sequence ``sequence`` (an asset name, None: nothing to do) in the slot: ``Montage.from_sequence``
        + ``manager.play(..., play_rate=rate, blend_in=blend_in)``; ``blend_out_mode`` is a ``MontageBlendMode``."""

    def stop_montages_with_slot(self, slot: str, blend_out: float) -> None:
        """MontageManager.stop_with_slot(slot, blend_out): every active montage with a track in the slot blends out."""

    def snapshot_pose(self, name: str) -> None:
        """SavePoseSnapshot(name): the pose the animation instance evaluated last is kept under the name ``name``, the dotted path of the
        snapshot state (``"RagdollingState.FinalRagdollPose"``) for the graph's PoseSnapshot node; the generated code then holds the name in
        the state (valid() reads true from the next update on)."""

    def montage_is_playing(self, name) -> bool:
        """manager.is_playing(montage) for a montage asset value (the asset's name); the same as ``montage_playing`` for a declared montage"""
        return bool(self.montage_playing(name)) if name is not None else False

    def set_movement_mode_locked(self, value: bool) -> None:
        """cm.set_movement_mode_locked(value): the movement mode cannot change, the movement component's own changes included"""

    def set_actor_location(self, location, sweep: bool, teleport: bool) -> None:
        """cm.set_actor_location(V3(location), sweep, teleport)"""

    def set_velocity(self, velocity) -> None:
        """cm.set_velocity(V3(velocity))"""

    def set_anim_root_motion_translation_scale(self, scale: float) -> None:
        """cm.set_anim_root_motion_translation_scale(scale)"""

    def ragdoll_begin(self, limit_speed: bool, speed_limit: float) -> None:
        """the ragdoll service of the character: ``ragdoll_begin(limit_speed, speed_limit)`` (a host without one does nothing)"""

    def ragdoll_end(self) -> None:
        """the ragdoll service: ``ragdoll_end()``"""

    def ragdoll_limit_speed(self, speed: float) -> None:
        """the ragdoll service: ``ragdoll_limit_speed(speed)``"""

    def ragdoll_set_motor_strength(self, strength: float) -> None:
        """the ragdoll service: ``ragdoll_set_motor_strength(strength)``"""

    def ragdoll_add_force(self, force, spine: bool) -> None:
        """the ragdoll service: ``ragdoll_add_force(V3(force), spine)``"""

    def root_motion_override(self, velocity, rotation_delta) -> None:
        """character movement: while the override is set, root motion = ``Transform(rotation_delta, velocity * dt)`` replaces the movement's own
        velocity update (c3: ``cm.root_motion_override = (velocity, rotation_delta)``, applied in the custom movement mode's ``_phys_custom``)"""

    def root_motion_clear(self) -> None:
        """character movement: end the root motion override (``cm.root_motion_override = None``)"""

    # ---- queries: what a montage asset says about itself --------------------------------------------------------------
    def montage_length(self, name) -> float:
        """``Montage.length`` of the asset named ``name`` (0.0 for none / unknown)"""
        return 0.0

    def montage_rate_scale(self, name) -> float:
        """``Montage.rate_scale`` (1.0 for none / unknown)"""
        return 1.0

    def montage_sampling_rate(self, name) -> float:
        """frames per second the montage's animation is sampled at (c3: ``sequence.sampling_rate``; 0.0 for none / unknown)"""
        return 0.0

    def montage_blend_in_time(self, name) -> float:
        """``Montage.blend_in.time`` (0.25 for none / unknown)"""
        return 0.25

    def montage_blend_in_option(self, name):
        """``Montage.blend_in.option`` as a ``BlendOption`` (Linear for none / unknown)"""
        return rt.BlendOption.Linear

    def montage_root_transform(self, name, time):
        """the root bone's absolute transform in component space at ``time`` (c3: the root track of the montage's first slot track's segment at that time;
        identity for none / unknown)"""
        return rt.Transform()

    def montage_last_root_transform(self, name):
        """the root bone's absolute transform at the end of the montage's last segment (identity for none / unknown)"""
        return rt.Transform()

    # ---- commands: controller / camera --------------------------------------------------------------------------------
    def set_control_rotation(self, rotation) -> None:
        """session.controller.control_rotation = Rot(pitch, yaw, roll)"""

    def camera_set_arm_length(self, value: float) -> None:
        """arm.p.target_arm_length = value"""

    def camera_set_socket_offset(self, offset) -> None:
        """arm.p.socket_offset = tuple(offset)"""

    def camera_set_fov(self, value: float) -> None:
        """camera.p.fov = value"""


# ---- documentation generated from the tables --------------------------------------------------------------------------------

def _param_text(p) -> str:
    return p if isinstance(p, str) else p.name


def protocol_markdown() -> str:
    """The host protocol as Markdown tables (``python -m dal host``): what a host has to provide, from the tables above."""
    out = ["## Reads (`input X: type <- host.item`)", "", "| item | type | Unreal | meaning |", "|---|---|---|---|"]
    for it in HOST_ITEMS.values():
        ty = it.ty.name if it.ty is not None else ("/".join(t.name for t in it.allowed) if it.allowed else "section enum")
        name = f"{it.name}({it.arg_kind})" if it.arg else it.name
        unit = f" ({it.unit})" if it.unit else ""
        out.append(f"| `{name}` | {ty}{unit} | `{it.ue}` | {it.doc} |")
    out += ["", "## Commands (`host.name(args);`, executed after the update)", "", "| command | arguments | Unreal |", "|---|---|---|"]
    for c in COMMANDS.values():
        params = ", ".join(_param_text(p) + ("?" if i >= c.min_args else "") for i, p in enumerate(c.params))
        if c.implicit:
            params = ", ".join(f"[{x}]" for x in c.implicit) + (", " + params if params else "")       # [source]: given to the host, not written
        out.append(f"| `{c.name}` | {params or '-'} | `{c.ue}` |")
    out += ["", "## Queries (`host.name(args)` expressions; actor logic, an animation instance only the thread-safe ones marked †)", "",
            "| query | arguments | result | Unreal |", "|---|---|---|---|"]
    for q in QUERIES.values():
        params = ", ".join(t.name for t in q.params)
        out.append(f"| `{q.name}`{' †' if q.thread_safe else ''} | {params or '-'} | {q.ret.name} | `{q.ue}` |")
    out += ["", "## Events (`on ...` handlers; `HostV1.poll_events`)", "", "| event | tuple |", "|---|---|"]
    for name, doc in EVENTS.items():
        out.append(f"| `{name}` | {doc} |")
    return "\n".join(out) + "\n"
