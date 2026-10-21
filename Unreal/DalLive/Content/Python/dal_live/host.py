"""``UeHost``: the Unreal implementation of ``dal.hostapi.HostV1`` for editor Python.

Reads and commands use the ``unreal`` module (CMC properties, ``Character``, ``AnimInstance`` montage calls, controller
and camera); what Python cannot reach goes through the C++ ``DalInterpHost`` component the host belongs to: the event
queue (delegates with payloads), Enhanced Input action values and events, traces and the target list, and the write of
the program's values into the AnimInstance's properties.  Each method names the C++ the generated component runs for
the same thing (``dal/hostapi.py`` has the protocol tables, ``DalHost.h`` / the generated ``.cpp`` the C++ side).
"""

from __future__ import annotations

import functools

import unreal

from dal import runtime as rt
from dal.hostapi import COMMANDS, HostV1
from dal.interp import coerce

_UNSET = object()
_REFRESH_TICKS = 120           # cached engine objects are looked up again at least this often


def _retrying(fn):
    """Run a host read / command; if it fails (a cached object may have died), forget the cache and try once more."""

    @functools.wraps(fn)
    def wrapper(self, *args):
        try:
            return fn(self, *args)
        except Exception:
            self._cache.clear()
            return fn(self, *args)

    return wrapper

# EMovementMode order, shared with dal.runtime.MovementMode
_MODE_NAMES = ("MOVE_NONE", "MOVE_WALKING", "MOVE_NAV_WALKING", "MOVE_FALLING", "MOVE_SWIMMING", "MOVE_FLYING", "MOVE_CUSTOM")

_PUBLISH_KIND = {"float": 0, "int": 0, "bool": 0, "vec2": 1, "vec3": 2, "rotator": 3, "quat": 4}


def _vec3(v) -> rt.Vec3:
    return rt.Vec3(v.x, v.y, v.z)


def _to_vector(v) -> "unreal.Vector":
    return unreal.Vector(x=v[0], y=v[1], z=v[2])


def _to_rotator(r) -> "unreal.Rotator":
    # dal rotators are (pitch, yaw, roll); unreal.Rotator(roll, pitch, yaw) takes roll first, so use the names
    return unreal.Rotator(roll=r[2], pitch=r[0], yaw=r[1])


class UeHost(HostV1):
    """The host of one ``DalInterpHost`` component.  All object lookups are cached for the duration of one update."""

    def __init__(self, comp):
        self.comp = comp
        self._action_types: dict[str, str] = {}
        self._assets: dict[str, object] = {}
        self._publish: list[tuple[str, int]] | None = None     # (name, kind) in plan order; None = not planned
        self._publish_wanted = False
        self._module = None
        self._warned: set[str] = set()
        self._cache: dict = {}
        self._session = None
        self._read: list[tuple[str, int, object]] | None = None     # free inputs read from the AnimInstance: (name, kind, type)
        self._age = 0
        self._anim_epoch = -1
        self.pending_events = 1          # set by the manager from the tick delegate; conservative until the first tick
        self.pending_flags = 1

    # ---- bookkeeping -----------------------------------------------------------------------------------------------

    def begin_update(self) -> None:
        """Engine objects are cached across updates (a lookup is a crossing into C++); refresh them now and then, and when the AnimInstance moved."""
        self._age += 1
        epoch = self.comp.anim_epoch
        if epoch != self._anim_epoch:
            self._anim_epoch = epoch
            self._cache.pop("anim", None)
            self._publish = None if self._publish_wanted else self._publish
            self._read = None
        if self._age >= _REFRESH_TICKS:
            self._age = 0
            self._cache.clear()
        if self._publish_wanted:
            self._read_free_inputs()

    def _warn_once(self, key: str, text: str) -> None:
        if key not in self._warned:
            self._warned.add(key)
            unreal.log_warning(f"DalLive: {text}")

    def _cached(self, slot: str, fetch):
        obj = self._cache.get(slot, _UNSET)
        if obj is _UNSET:
            obj = self._cache[slot] = fetch()
        return obj

    def _get_owner(self):
        return self._cached("owner", self.comp.get_owner)

    def _get_cmc(self):
        return self._cached("cmc", self.comp.get_movement)

    def _get_char(self):
        return self._cached("char", self.comp.get_character)

    def _get_anim(self):
        return self._cached("anim", self.comp.get_anim_instance)

    def _get_arm(self):
        return self._cached("arm", self.comp.get_spring_arm)

    def _get_cam(self):
        return self._cached("cam", self.comp.get_camera)

    def _montage(self, name):
        """The montage asset of a declared montage (the component's Montages map) or of a ``montage`` value: its object path (settings data)."""
        if name is None:
            return None
        asset = self._assets.get(name)
        if asset is None:
            asset = self.comp.get_montage_asset(name)
            if asset is None and name.startswith("/"):
                asset = unreal.load_asset(name)
            if asset is None:
                self._warn_once("montage:" + name, f"{self.comp.get_name()}: no montage asset assigned for '{name}' (Montages map)")
            else:
                self._assets[name] = asset
        return asset

    def _sequence(self, name):
        """The animation sequence of a ``sequence`` value (its object path)."""
        if name is None:
            return None
        asset = self._assets.get("seq:" + name)
        if asset is None and name.startswith("/"):
            asset = unreal.load_asset(name)
            if asset is not None:
                self._assets["seq:" + name] = asset
        if asset is None:
            self._warn_once("sequence:" + name, f"{self.comp.get_name()}: no animation sequence '{name}' (an object path)")
        return asset

    # ---- program changes ---------------------------------------------------------------------------------------------

    def program_changed(self, module, session) -> None:
        """A program was loaded or swapped in: tell the component which events it has to queue, resolve assets, plan the publish."""
        self._module = module
        self._session = session
        self._read = None
        self._action_types = {a.name: a.ty.name for a in module.actions}
        self._assets = {}
        event_notifies, flag_notifies, inputs = [], [], []
        ended = blending = False
        for h in module.handlers:
            if h.event in ("notify", "notify_begin", "notify_end"):
                event_notifies.append(h.name)
            elif h.event == "montage_ended":
                ended = True
            elif h.event == "montage_blending_out":
                blending = True
            elif h.event == "input":
                inputs.append(f"{h.name}:{h.phase}")
        for i in module.inputs:
            if i.host is not None and i.host.item == "notify":
                flag_notifies.append(i.host.arg)
        self.comp.configure_watch(sorted(set(event_notifies)), sorted(set(flag_notifies)), ended, blending, sorted(set(inputs)))
        for m in module.montages:
            self._montage(m.name)
        self._publish = None
        self._publish_wanted = module.kind == "anim" or bool(self.comp.get_editor_property("publish_to_anim_instance"))
        if module.kind == "anim":
            self.comp.order_before_mesh()             # NativeUpdateAnimation of the generated parent runs before the graph evaluates

    def _plan_publish(self) -> None:
        """Ask the component to resolve the AnimInstance properties named like the program's params, inputs and states.

        Free inputs (no `<- host...` binding) are not published: they are the way game code talks to the logic, so the
        AnimInstance property is the source (see `_read_free_inputs`)."""
        module = self._module
        target = self.comp.get_anim_instance()
        if target is None:
            return                                    # the animation instance does not exist yet; try again next update
        entries = [(p.name, _PUBLISH_KIND.get(p.ty.name, 0)) for p in module.params]
        entries += [(i.name, _PUBLISH_KIND.get(i.ty.name, 0)) for i in module.inputs if i.host is not None]
        entries += [(s.name, _PUBLISH_KIND.get(s.ty.name, 0)) for s in module.states]
        accepted = set(str(n) for n in self.comp.set_publish_plan(target, [e[0] for e in entries], [e[1] for e in entries]))
        self._publish = [e for e in entries if e[0] in accepted]
        missing = [e[0] for e in entries if e[0] not in accepted]
        if missing:
            self._warn_once("publish-missing:" + ",".join(missing),
                            f"{target.get_class().get_name()} has no writable property for: {', '.join(missing)} (not published)")

    def _plan_read(self) -> None:
        module = self._module
        target = self.comp.get_anim_instance()
        if target is None:
            return
        free = [i for i in module.inputs if i.host is None]
        if not free:
            self._read = []
            return
        names = [i.name for i in free]
        kinds = [_PUBLISH_KIND.get(i.ty.name, 0) for i in free]
        accepted = set(str(n) for n in self.comp.set_read_plan(target, names, kinds))
        self._read = [(i.name, _PUBLISH_KIND.get(i.ty.name, 0), i.ty) for i in free if i.name in accepted]
        missing = [i.name for i in free if i.name not in accepted]
        if missing:
            self._warn_once("read-missing:" + ",".join(missing),
                            f"{target.get_class().get_name()} has no readable property for the free input(s): {', '.join(missing)}")

    def _read_free_inputs(self) -> None:
        """Anim logic: the free inputs come from the AnimInstance's properties of the same name (game code sets them there)."""
        if self._read is None:
            self._plan_read()
            if self._read is None:
                return
        if not self._read or self._session is None or self._session.interp is None:
            return
        data = self.comp.read_values()
        sizes = [1 if kind == 0 else {1: 2, 2: 3, 3: 3, 4: 4}[kind] for _n, kind, _t in self._read]
        if len(data) != sum(sizes):
            self._read = None                         # the plan went stale (the AnimInstance was replaced): plan again next update
            return
        values = self._session.interp.values
        pos = 0
        for (name, kind, ty), n in zip(self._read, sizes):
            values[name] = coerce(ty, data[pos] if kind == 0 else tuple(data[pos:pos + n]))
            pos += n

    def after_update(self, session) -> None:
        """Anim logic: write the program's values into the AnimInstance, by property name."""
        if not self._publish_wanted:
            return
        if self._publish is None:
            self._plan_publish()
            if self._publish is None:
                return
        values = session.interp.values
        data: list[float] = []
        for name, kind in self._publish:
            v = values[name]
            if kind == 0:
                data.append(float(v))
            else:
                data.extend(v)
        if self.comp.publish_values(data) < 0:
            self._publish = None                      # the AnimInstance was replaced: plan again on the next update

    # ---- reads ----------------------------------------------------------------------------------------------------------

    @property
    def time(self):
        """GetWorld()->GetTimeSeconds()"""
        return unreal.GameplayStatics.get_time_seconds(self.comp)

    @property
    def velocity(self):
        """CharacterMovement->Velocity"""
        cmc = self._get_cmc()
        return _vec3(cmc.velocity) if cmc is not None else rt.VEC3_ZERO

    @property
    def acceleration(self):
        """CharacterMovement->GetCurrentAcceleration()"""
        cmc = self._get_cmc()
        return _vec3(cmc.get_current_acceleration()) if cmc is not None else rt.VEC3_ZERO

    @property
    def max_acceleration(self):
        """CharacterMovement->GetMaxAcceleration()"""
        cmc = self._get_cmc()
        return float(cmc.get_max_acceleration()) if cmc is not None else 0.0

    @property
    def max_braking_deceleration(self):
        """CharacterMovement->GetMaxBrakingDeceleration()"""
        cmc = self._get_cmc()
        return float(cmc.get_max_braking_deceleration()) if cmc is not None else 0.0

    @property
    def max_walk_speed(self):
        """CharacterMovement->MaxWalkSpeed"""
        cmc = self._get_cmc()
        return float(cmc.max_walk_speed) if cmc is not None else 0.0

    @property
    def movement_mode(self):
        """CharacterMovement->MovementMode (EMovementMode order = dal MovementMode)"""
        cmc = self._get_cmc()
        if cmc is None:
            return rt.MovementMode.None_
        return rt.MovementMode(int(cmc.movement_mode.value))

    @property
    def is_grounded(self):
        """CharacterMovement->IsMovingOnGround()"""
        cmc = self._get_cmc()
        return bool(cmc.is_moving_on_ground()) if cmc is not None else False

    @property
    def is_crouching(self):
        """CharacterMovement->IsCrouching()"""
        cmc = self._get_cmc()
        return bool(cmc.is_crouching()) if cmc is not None else False

    @property
    def actor_location(self):
        """Pawn->GetActorLocation()"""
        owner = self._get_owner()
        return _vec3(owner.get_actor_location()) if owner is not None else rt.VEC3_ZERO

    @property
    def actor_rotation(self):
        """Pawn->GetActorQuat()  (the actor's rotator converted to a quaternion; the Python API has no GetActorQuat)"""
        owner = self._get_owner()
        if owner is None:
            return rt.QUAT_IDENTITY
        q = owner.get_actor_rotation().quaternion()
        return rt.Quat(q.x, q.y, q.z, q.w)

    @property
    def control_rotation(self):
        """Pawn->GetControlRotation()"""
        owner = self._get_owner()
        if owner is None or not hasattr(owner, "get_control_rotation"):
            return rt.ROTATOR_ZERO
        r = owner.get_control_rotation()
        return rt.Rotator(r.pitch, r.yaw, r.roll)

    def action(self, name, ty):
        """UEnhancedPlayerInput::GetActionValue(Action) of the owner's local player (through the component)"""
        v = self.comp.get_action_value(name)
        if ty == "bool":
            return v.x != 0.0 or v.y != 0.0 or v.z != 0.0
        if ty == "float":
            return v.x
        if ty == "vec2":
            return rt.Vec2(v.x, v.y)
        return rt.Vec3(v.x, v.y, v.z)

    def notify(self, name):
        """the flag a point notify (Play Montage Notify) set since the last read"""
        return bool(self.comp.consume_notify_flag(name)) if self.pending_flags else False

    # ---- montage state ----------------------------------------------------------------------------------------------------

    def montage_playing(self, name):
        """AnimInstance->Montage_IsPlaying(Montage)"""
        anim, asset = self._get_anim(), self._montage(name)
        return bool(anim.montage_is_playing(asset)) if anim is not None and asset is not None else False

    def montage_active(self, name):
        """AnimInstance->Montage_IsActive(Montage)"""
        anim, asset = self._get_anim(), self._montage(name)
        return bool(anim.montage_is_active(asset)) if anim is not None and asset is not None else False

    def montage_position(self, name):
        """AnimInstance->Montage_GetPosition(Montage)"""
        anim, asset = self._get_anim(), self._montage(name)
        return float(anim.montage_get_position(asset)) if anim is not None and asset is not None else 0.0

    def montage_section(self, name):
        """AnimInstance->Montage_GetCurrentSection(Montage), only while the montage is playing"""
        anim, asset = self._get_anim(), self._montage(name)
        if anim is None or asset is None or not anim.montage_is_playing(asset):
            return None
        return str(anim.montage_get_current_section(asset))

    # ---- queries ----------------------------------------------------------------------------------------------------------------

    def trace_line(self, start, end):
        """World->LineTraceSingleByChannel(TraceChannel, ignoring the owner) -- in the component"""
        h = self.comp.trace_line(_to_vector(start), _to_vector(end))
        return rt.Hit(h.blocking, _vec3(h.point), _vec3(h.normal), h.distance, h.time)

    def trace_sphere(self, start, end, radius):
        """World->SweepSingleByChannel(sphere) -- in the component"""
        h = self.comp.trace_sphere(_to_vector(start), _to_vector(end), float(radius))
        return rt.Hit(h.blocking, _vec3(h.point), _vec3(h.normal), h.distance, h.time)

    def target_count(self):
        return int(self.comp.target_count())

    def target_id(self, index):
        return int(self.comp.target_id(int(index)))

    def target_location(self, index):
        return _vec3(self.comp.target_location(int(index)))

    def target_index(self, target_id):
        return int(self.comp.target_index(int(target_id)))

    # ---- events ----------------------------------------------------------------------------------------------------------------------

    def poll_events(self):
        """The events the component's delegates and input bindings queued (oldest first), as HostV1 tuples"""
        if not self.pending_events:
            return []
        raw = self.comp.take_events()
        if not raw:
            return []
        out = []
        for e in raw:
            kind = str(e.kind)
            name = str(e.name)
            if kind == "input":
                v = e.value
                ty = self._action_types.get(name, "bool")
                if ty == "bool":
                    value = v.x != 0.0 or v.y != 0.0 or v.z != 0.0
                elif ty == "float":
                    value = v.x
                elif ty == "vec2":
                    value = (v.x, v.y)
                else:
                    value = (v.x, v.y, v.z)
                out.append(("input", name, str(e.phase), value))
            elif kind in ("montage_ended", "montage_blending_out"):
                out.append((kind, name, bool(e.flag)))
            else:
                out.append((kind, name))
        return out

    # ---- commands: character movement ---------------------------------------------------------------------------------------------

    def _set_cmc(self, prop: str, value) -> None:
        cmc = self._get_cmc()
        if cmc is not None:
            cmc.set_editor_property(prop, value)

    def set_max_walk_speed(self, value):
        """CharacterMovement->MaxWalkSpeed = v"""
        self._set_cmc("max_walk_speed", float(value))

    def set_max_acceleration(self, value):
        """CharacterMovement->MaxAcceleration = v"""
        self._set_cmc("max_acceleration", float(value))

    def set_braking_deceleration_walking(self, value):
        """CharacterMovement->BrakingDecelerationWalking = v"""
        self._set_cmc("braking_deceleration_walking", float(value))

    def set_gravity_scale(self, value):
        """CharacterMovement->GravityScale = v"""
        self._set_cmc("gravity_scale", float(value))

    def set_jump_z_velocity(self, value):
        """CharacterMovement->JumpZVelocity = v"""
        self._set_cmc("jump_z_velocity", float(value))

    def set_air_control(self, value):
        """CharacterMovement->AirControl = v"""
        self._set_cmc("air_control", float(value))

    def set_rotation_rate(self, rotation):
        """CharacterMovement->RotationRate = r"""
        self._set_cmc("rotation_rate", _to_rotator(rotation))

    def set_max_walk_speed_crouched(self, value):
        """CharacterMovement->MaxWalkSpeedCrouched = v"""
        self._set_cmc("max_walk_speed_crouched", float(value))

    def set_ground_friction(self, value):
        """CharacterMovement->GroundFriction = v"""
        self._set_cmc("ground_friction", float(value))

    def set_braking_friction_factor(self, value):
        """CharacterMovement->BrakingFrictionFactor = v"""
        self._set_cmc("braking_friction_factor", float(value))

    def _set_als_cmc(self, prop: str, value) -> None:
        """A property of the movement component of DalRuntime (UDalCharacterMovementComponent); a character with another one ignores it."""
        cmc = self._get_cmc()
        if cmc is None:
            return
        try:
            cmc.set_editor_property(prop, value)
        except Exception:                      # the property is not there: not the DalRuntime movement component
            self._warn_once("als_cmc:" + prop, f"{self.comp.get_name()}: the movement component has no '{prop}' (UDalCharacterMovementComponent of DalRuntime)")

    def set_max_acceleration_walking(self, value):
        """UDalCharacterMovementComponent::MaxAccelerationWalking = v (DalRuntime)"""
        self._set_als_cmc("max_acceleration_walking", float(value))

    def set_input_blocked(self, value):
        """UDalCharacterMovementComponent::bInputBlocked = b (DalRuntime)"""
        self._set_als_cmc("input_blocked", bool(value))

    def set_actor_rotation(self, rotation, teleport):
        """Pawn->SetActorRotation(Rotation, bTeleport ? ETeleportType::TeleportPhysics : ETeleportType::None)"""
        owner = self._get_owner()
        if owner is not None:
            owner.set_actor_rotation(_to_rotator(rotation), bool(teleport))

    def set_actor_location(self, location, sweep=False, teleport=True):
        """Pawn->SetActorLocation(Location, bSweep, nullptr, bTeleport ? ETeleportType::TeleportPhysics : ETeleportType::None)"""
        owner = self._get_owner()
        if owner is not None:
            owner.set_actor_location(_to_vector(location), bool(sweep), bool(teleport))

    def set_velocity(self, velocity):
        """CharacterMovement->Velocity = v"""
        self._set_cmc("velocity", _to_vector(velocity))

    def set_anim_root_motion_translation_scale(self, scale):
        """Character->SetAnimRootMotionTranslationScale(Scale)"""
        ch = self._get_char()
        if ch is not None:
            ch.set_anim_root_motion_translation_scale(float(scale))

    def add_controller_yaw_input(self, value):
        """Pawn->AddControllerYawInput(v)"""
        owner = self._get_owner()
        if owner is not None:
            owner.add_controller_yaw_input(float(value))

    def add_controller_pitch_input(self, value):
        """Pawn->AddControllerPitchInput(v)"""
        owner = self._get_owner()
        if owner is not None:
            owner.add_controller_pitch_input(float(value))

    def set_orient_rotation_to_movement(self, value):
        """CharacterMovement->bOrientRotationToMovement = b"""
        self._set_cmc("orient_rotation_to_movement", bool(value))

    def set_use_controller_desired_rotation(self, value):
        """CharacterMovement->bUseControllerDesiredRotation = b"""
        self._set_cmc("use_controller_desired_rotation", bool(value))

    def set_movement_mode(self, mode, sub=0):
        """CharacterMovement->SetMovementMode(Mode, SubMode)"""
        cmc = self._get_cmc()
        if cmc is not None:
            cmc.set_movement_mode(getattr(unreal.MovementMode, _MODE_NAMES[int(mode)]), int(sub))

    def jump(self):
        """Character->Jump()"""
        ch = self._get_char()
        if ch is not None:
            ch.jump()

    def stop_jumping(self):
        """Character->StopJumping()"""
        ch = self._get_char()
        if ch is not None:
            ch.stop_jumping()

    def crouch(self):
        """Character->Crouch()"""
        ch = self._get_char()
        if ch is not None:
            ch.crouch(False)

    def uncrouch(self):
        """Character->UnCrouch()"""
        ch = self._get_char()
        if ch is not None:
            ch.un_crouch(False)

    def add_movement_input(self, direction, scale):
        """Pawn->AddMovementInput(Direction, Scale)"""
        owner = self._get_owner()
        if owner is not None:
            owner.add_movement_input(_to_vector(direction), float(scale), False)

    def launch(self, velocity, xy_override, z_override):
        """Character->LaunchCharacter(Velocity, bXYOverride, bZOverride)"""
        ch = self._get_char()
        if ch is not None:
            ch.launch_character(_to_vector(velocity), bool(xy_override), bool(z_override))

    # ---- commands: montages ------------------------------------------------------------------------------------------------------------

    def montage_play(self, name, rate, section):
        """AnimInstance->Montage_Play(Montage, Rate); Montage_JumpToSection(Section, Montage) when a section is given"""
        anim, asset = self._get_anim(), self._montage(name)
        if anim is not None and asset is not None:
            anim.montage_play(asset, float(rate))
            if section is not None:
                anim.montage_jump_to_section(section, asset)

    def montage_stop(self, name, blend_out):
        """AnimInstance->Montage_Stop(BlendOut or the montage's default, Montage)"""
        anim, asset = self._get_anim(), self._montage(name)
        if anim is not None and asset is not None:
            anim.montage_stop(float(blend_out) if blend_out >= 0.0 else float(asset.get_default_blend_out_time()), asset)

    def montage_stop_all(self, blend_out):
        """AnimInstance->Montage_Stop(BlendOut, or the blend out of the active montage)"""
        anim = self._get_anim()
        if anim is not None:
            if blend_out >= 0.0:
                t = float(blend_out)
            else:
                active = anim.get_current_active_montage()
                t = float(active.get_default_blend_out_time()) if active is not None else 0.0
            anim.montage_stop(t)

    def montage_jump_to_section(self, name, section):
        """AnimInstance->Montage_JumpToSection(Section, Montage)"""
        anim, asset = self._get_anim(), self._montage(name)
        if anim is not None and asset is not None:
            anim.montage_jump_to_section(section, asset)

    def montage_set_next_section(self, name, section, next_section):
        """AnimInstance->Montage_SetNextSection(From, To, Montage)"""
        anim, asset = self._get_anim(), self._montage(name)
        if anim is not None and asset is not None:
            anim.montage_set_next_section(section, next_section, asset)

    def montage_set_play_rate(self, name, rate):
        """AnimInstance->Montage_SetPlayRate(Montage, Rate)"""
        anim, asset = self._get_anim(), self._montage(name)
        if anim is not None and asset is not None:
            anim.montage_set_play_rate(asset, float(rate))

    def montage_set_position(self, name, position):
        """AnimInstance->Montage_SetPosition(Montage, Position)"""
        anim, asset = self._get_anim(), self._montage(name)
        if anim is not None and asset is not None:
            anim.montage_set_position(asset, float(position))

    def play_slot_animation(self, sequence, slot, blend_in, blend_out, rate, loops, blend_out_trigger, start_time, blend_out_mode):
        """AnimInstance->PlaySlotAnimationAsDynamicMontage(Sequence, Slot, BlendIn, BlendOut, Rate, Loops, BlendOutTriggerTime, StartTime).
        An inertialization blend out (blend_out_mode 1) needs the ..._WithBlendSettings variant, which Python cannot call: the standard blend plays."""
        anim, asset = self._get_anim(), self._sequence(sequence)
        if anim is not None and asset is not None:
            anim.play_slot_animation_as_dynamic_montage(asset, slot, float(blend_in), float(blend_out), float(rate), int(loops),
                                                        float(blend_out_trigger), float(start_time))

    # Not in the editor session (no Python API reaches them; the commands do nothing here, the generated C++ and the Blender host have them):
    # montage_stop_source (the montage instance of a notify), stop_montages_with_slot (FAnimMontageInstance tracks), snapshot_pose (FPoseSnapshot),
    # root_motion_override / root_motion_clear (the root motion source of DalRuntime's movement component), set_movement_mode_locked and the ragdoll_*
    # commands (DalRuntime's movement component and ragdoll service), the montage asset queries
    # (montage_length, montage_rate_scale, montage_sampling_rate, montage_blend_in_time, montage_blend_in_option, montage_root_transform,
    # montage_last_root_transform: they answer their neutral defaults).

    # ---- commands: controller / camera ---------------------------------------------------------------------------------------------------

    def set_control_rotation(self, rotation):
        """Controller->SetControlRotation(R)"""
        owner = self._get_owner()
        controller = owner.get_controller() if owner is not None and hasattr(owner, "get_controller") else None
        if controller is not None:
            controller.set_control_rotation(_to_rotator(rotation))

    def camera_set_arm_length(self, value):
        """SpringArm->TargetArmLength = v"""
        arm = self._get_arm()
        if arm is not None:
            arm.set_editor_property("target_arm_length", float(value))

    def camera_set_socket_offset(self, offset):
        """SpringArm->SocketOffset = v"""
        arm = self._get_arm()
        if arm is not None:
            arm.set_editor_property("socket_offset", _to_vector(offset))

    def camera_set_fov(self, value):
        """Camera->SetFieldOfView(v)"""
        cam = self._get_cam()
        if cam is not None:
            cam.set_field_of_view(float(value))


# Every read and command may find a cached object dead (the pawn was replaced, the mesh re-initialised): look it up again once.
for _name in ("time", "velocity", "acceleration", "max_acceleration", "max_braking_deceleration", "max_walk_speed", "movement_mode",
              "is_grounded", "is_crouching", "actor_location", "actor_rotation", "control_rotation"):
    setattr(UeHost, _name, property(_retrying(UeHost.__dict__[_name].fget)))
for _name in (*COMMANDS, "action", "notify", "montage_playing", "montage_active", "montage_position", "montage_section",
              "trace_line", "trace_sphere", "target_count", "target_id", "target_location", "target_index"):
    if _name in UeHost.__dict__:                      # a command this host does not implement stays the do-nothing method of HostV1
        setattr(UeHost, _name, _retrying(UeHost.__dict__[_name]))
del _name
