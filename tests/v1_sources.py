"""Small v1 / v2 programs shared by the C++ tests: every host command, traces and targets, an anim instance with events."""

ALL_COMMANDS = """
logic AllCommands : actor;
montage Swing { Windup, Strike }
input F: float;
input V: vec3;
input R: rotator;
input B: bool;
input S: int;
input Mode: MovementMode;
state N: int;
update(dt) {
    host.set_max_walk_speed(F);
    host.set_max_acceleration(F * 2.0);
    host.set_braking_deceleration_walking(F + 1.0);
    host.set_gravity_scale(F);
    host.set_jump_z_velocity(F);
    host.set_air_control(F);
    host.set_max_walk_speed_crouched(F);
    host.set_max_acceleration_walking(F);
    host.set_ground_friction(F);
    host.set_braking_friction_factor(F);
    host.set_input_blocked(B);
    host.set_actor_rotation(R);
    host.set_actor_rotation(R, B);
    host.add_controller_yaw_input(F);
    host.add_controller_pitch_input(-F);
    host.set_movement_mode_locked(B);
    host.set_actor_location(V);
    host.set_actor_location(V, B, !B);
    host.set_velocity(V * 2.0);
    host.set_anim_root_motion_translation_scale(F);
    host.ragdoll_begin(B, F);
    host.ragdoll_end();
    host.ragdoll_limit_speed(F);
    host.ragdoll_set_motor_strength(F);
    host.ragdoll_add_force(V);
    host.ragdoll_add_force(V, B);
    host.set_rotation_rate(R);
    host.set_orient_rotation_to_movement(B);
    host.set_use_controller_desired_rotation(!B);
    host.set_movement_mode(Mode);
    host.set_movement_mode(Mode, 2);
    host.root_motion_override(V, quat(0.0, 0.0, 0.0, 1.0));
    host.root_motion_clear();
    if (B) { host.jump(); } else { host.stop_jumping(); }
    if (S > 0) { host.crouch(); } else { host.uncrouch(); }
    host.add_movement_input(V);
    host.add_movement_input(V, F);
    host.launch(V);
    host.launch(V, B, !B);
    host.montage_play(Swing);
    host.montage_play(Swing, F, Swing.Strike);
    host.montage_stop(Swing);
    host.montage_stop(Swing, 0.1);
    host.montage_stop_all();
    host.montage_stop_all(0.3);
    host.montage_jump_to_section(Swing, Swing.Windup);
    host.montage_set_next_section(Swing, Swing.Windup, Swing.Strike);
    host.montage_set_play_rate(Swing, F);
    host.montage_set_position(Swing, F);
    host.stop_montages_with_slot("Slot");
    host.stop_montages_with_slot("Slot", 0.1);
    host.set_control_rotation(R);
    host.camera_set_arm_length(F);
    host.camera_set_socket_offset(V);
    host.camera_set_fov(F);
    N += 1;
}
"""

# the commands an actor logic cannot issue (they belong to an animation instance), with the asset values they take
ANIM_V4_COMMANDS = """
settings Moves { Attack: montage; Turn: sequence; }
notify_state EarlyOut branching { Duration: float = 0.25; }
logic AnimCommands : anim post_flush;
param Cfg: Moves;
input Go: float;
state Pose: snapshot;
on notify_begin EarlyOut { host.montage_stop_source(payload.Duration); }
update(dt) {
    host.montage_play(Cfg.Attack, Go);
    host.play_slot_animation(Cfg.Turn, "Transition");
    host.play_slot_animation(Cfg.Turn, "Plain", 0.1, 0.2, Go, 2, -1.0, 0.0, MontageBlendMode.Inertialization);
    host.snapshot_pose(Pose);
}
"""

PROBE = """
logic Probe : actor;
input A: vec3;
input B: vec3;
input Radius: float;
state Blocked: bool;
state P: vec3;
state N: vec3;
state D: float;
state T: float;
state SphereBlocked: bool;
state SphereP: vec3;
state SphereD: float;
state Count: int;
state FirstId: int;
state FirstLoc: vec3;
state Back: int;
state Gone: vec3;
update(dt) {
    let h = host.trace_line(A, B);
    Blocked = h.blocking;
    P = h.point;
    N = h.normal;
    D = h.distance;
    T = h.time;
    let s = host.trace_sphere(A, B, abs(Radius));
    SphereBlocked = s.blocking;
    SphereP = s.point;
    SphereD = s.distance;
    Count = host.target_count();
    FirstId = host.target_id(0);
    FirstLoc = host.target_location(0);
    Back = host.target_index(FirstId);
    Gone = host.target_location(7);
}
"""

ANIM_V1 = """
logic AnimV1;
montage Slash { Wind, Hit, Rec }
action Fire: float;
input Fired: float <- host.action(Fire);
state Count: int;
state Open: bool;
state Ended: int;
state Blend: int;
state Phase: SlashSection;
state Pos: float;
state WasPlaying: bool;
on notify HitNotify {
    Count += 1;
    host.set_gravity_scale(2.0);
}
on notify_begin Window { Open = true; }
on notify_end Window { Open = false; }
on montage_ended Slash {
    Ended += 1;
    if (interrupted) { host.jump(); }
}
on montage_blending_out Slash { Blend += 1; }
update(dt) {
    if (Fired > 0.5 && !Slash.playing) {
        host.montage_play(Slash, 1.0, Slash.Hit);
    }
    Phase = Slash.section;
    Pos = Slash.position;
    WasPlaying = Slash.playing;
    if (Slash.active && Pos > 1.5) {
        host.montage_set_next_section(Slash, Slash.Hit, Slash.Rec);
    }
}
entry Kick(dt) {
    host.montage_stop(Slash);
}
"""
