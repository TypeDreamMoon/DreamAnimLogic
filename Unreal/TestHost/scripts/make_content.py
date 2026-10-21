"""Editor script: create the test assets of the W4 harness in /Game/DalTest.

  * IA_Attack, IA_LockOn (bool input actions)
  * AM_Light1..3: montages built from the template's MM_Attack_01..03 with sections, a Hit point notify and a
    CancelWindow notify window (the Play Montage Notify classes the DAL actor logic listens to)
  * L_DalTest: a flat 200 m square floor and a PlayerStart

Run once:  UnrealEditor-Cmd.exe DalLiveHost.uproject /Engine/Maps/Entry -nullrhi -unattended -ExecutePythonScript=make_content.py
"""

import unreal

PATH = "/Game/DalTest"
ATTACK_DIR = "/Game/Characters/Mannequins/Anims/Unarmed/Attack"
SKELETON = "/Game/Characters/Mannequins/Meshes/SK_Mannequin"

lib = unreal.EditorAssetLibrary
tools = unreal.AssetToolsHelpers.get_asset_tools()


def ensure(name, asset_class, factory, setup=None):
    full = f"{PATH}/{name}"
    if lib.does_asset_exist(full):
        unreal.log(f"[content] {full} exists")
        return lib.load_asset(full)
    asset = tools.create_asset(name, PATH, asset_class, factory)
    if setup:
        setup(asset)
    lib.save_loaded_asset(asset)
    unreal.log(f"[content] created {full}")
    return asset


def make_action(name):
    def setup(a):
        a.set_editor_property("value_type", unreal.InputActionValueType.BOOLEAN)

    return ensure(name, unreal.InputAction, unreal.InputAction_Factory(), setup)


def make_montage(index):
    name = f"AM_Light{index}"
    full = f"{PATH}/{name}"
    if lib.does_asset_exist(full):
        unreal.log(f"[content] {full} exists")
        return lib.load_asset(full)
    seq = lib.load_asset(f"{ATTACK_DIR}/MM_Attack_0{index}")
    factory = unreal.AnimMontageFactory()
    factory.set_editor_property("target_skeleton", lib.load_asset(SKELETON))
    factory.set_editor_property("source_animation", seq)
    montage = tools.create_asset(name, PATH, unreal.AnimMontage, factory)
    length = montage.get_play_length()
    # sections: Default (0) is made by the factory; the DAL declares Windup / Strike / Recover for the same montage
    unreal.DalTestLibrary.add_montage_section(montage, "Strike", length * 0.35)
    unreal.DalTestLibrary.add_montage_section(montage, "Recover", length * 0.65)
    track = "DalNotifies"
    unreal.AnimationLibrary.add_animation_notify_track(montage, track, unreal.LinearColor(1, 0.5, 0, 1))
    hit = unreal.AnimationLibrary.add_animation_notify_event(montage, track, length * 0.40, unreal.AnimNotify_PlayMontageNotify)
    hit.set_editor_property("notify_name", "Hit")
    window = unreal.AnimationLibrary.add_animation_notify_state_event(
        montage, track, length * 0.50, length * 0.30, unreal.AnimNotify_PlayMontageNotifyWindow)
    window.set_editor_property("notify_name", "CancelWindow")
    lib.save_loaded_asset(montage)
    unreal.log(f"[content] created {full} (length {length:.3f} s)")
    return montage


def make_level():
    full = f"{PATH}/L_DalTest"
    if lib.does_asset_exist(full):
        unreal.log(f"[content] {full} exists")
        return
    unreal.EditorLevelLibrary.new_level(full)
    cube = lib.load_asset("/Engine/BasicShapes/Cube")
    floor = unreal.EditorLevelLibrary.spawn_actor_from_class(unreal.StaticMeshActor, unreal.Vector(0, 0, -50))
    floor.set_actor_label("Floor")
    floor.static_mesh_component.set_static_mesh(cube)
    floor.set_actor_scale3d(unreal.Vector(200, 200, 1))
    unreal.EditorLevelLibrary.spawn_actor_from_class(unreal.PlayerStart, unreal.Vector(0, 0, 100))
    unreal.EditorLevelLibrary.save_current_level()
    unreal.log(f"[content] created level {full}")


lib.make_directory(PATH)
make_action("IA_Attack")
make_action("IA_LockOn")
make_action("IA_Sprint")
for i in (1, 2, 3):
    make_montage(i)
make_level()
unreal.log("[content] DONE")
