# g_record.py for a run without a renderer and without a GPU (UnrealEditor-Cmd.exe ... -nullrhi -benchmark -fps=60, at idle priority), ANIM-56.  The generated host ticks
# its mesh pose like a rendered one when DAL_HOST_ALWAYS_TICK_POSE=1 is in the environment (DalAlsHostCharacter.cpp); this wrapper does the same for the ORIGINAL ALS: AAlsCharacter::RefreshMeshProperties resets the mesh's tick option to Min(target, the
# Blueprint default) every tick, so the Blueprint's mesh default is set to AlwaysTickPoseAndRefreshBones (in memory only, nothing is saved) and the pose ticks like a rendered one.
import os
import traceback
import unreal

try:
    if os.environ.get("R1_ORIGINAL", "0") == "1":
        _cls = unreal.load_class(None, "/ALS/ALS/Character/B_Als_Character.B_Als_Character_C")
        _cdo = unreal.get_default_object(_cls)
        _mesh = _cdo.get_editor_property("mesh")
        _mesh.set_editor_property("visibility_based_anim_tick_option", unreal.VisibilityBasedAnimTickOption.ALWAYS_TICK_POSE_AND_REFRESH_BONES)
        unreal.log("g_record_nr: B_Als_Character mesh default tick option set to AlwaysTickPoseAndRefreshBones")
    _p = os.path.join(os.path.dirname(os.path.abspath(globals().get("__file__", "I:/DCC/DreamAnimLogic/Unreal/AlsHost/scripts/g_record_nr.py"))), "g_record.py")
    __file__ = _p.replace("\\", "/")
    exec(compile(open(_p, encoding="utf-8").read(), _p, "exec"), globals())
except Exception:
    unreal.log_error("g_record_nr failed: " + traceback.format_exc())
    unreal.SystemLibrary.execute_console_command(None, "QUIT_EDITOR")
