"""The ALS sources have the movement base (`als/als_lib.dal`, `AlsCharacter.dal`, `AlsAnimationInstance.dal`): the state struct, the shared refresh function, the
two classes that use it, the mantling's target in the space of a moving primitive and the hook slot it runs in.  The behaviour is in `test_als_character.py`,
`test_als_actions.py`, `test_als_truth.py` (against the Unreal takes) and, on the Blender side, `moon/tests/test_als_moving_base.py`."""

import unittest
from pathlib import Path

import _bootstrap  # noqa: F401


class AlsSourcesTests(unittest.TestCase):
    def test_the_state_struct_and_the_refresh_function(self):
        als = Path(__file__).resolve().parents[1] / "als"
        text = (als / "als_lib.dal").read_text(encoding="utf-8")
        self.assertIn("struct AlsMovementBaseState", text)
        self.assertIn("fn refresh_movement_base_state(", text)
        for name in ("AlsCharacter.dal", "AlsAnimationInstance.dal"):
            src = (als / name).read_text(encoding="utf-8")
            self.assertIn("refresh_movement_base_state(", src, name)
            self.assertIn("host.movement_base_relative_rotation", src, name)
        char = (als / "AlsCharacter.dal").read_text(encoding="utf-8")
        self.assertIn("update(dt) in prepare_root_motion", char)
        self.assertIn("host.set_movement_base(parameters.TargetPrimitive)", char)
        self.assertIn("host.primitive_uses_relative_location(", char)
        anim = (als / "AlsAnimationInstance.dal").read_text(encoding="utf-8")
        self.assertGreaterEqual(anim.count("if (MovementBase.bHasRelativeLocation) {"), 5)                  # the five foot lock branches (AI:1349, :1376, :1466, :1504, :1547)
        self.assertGreaterEqual(anim.count("if (MovementBase.bHasRelativeRotation) {"), 3)                  # the spine, the head, the previous yaw
        self.assertNotIn("dead without moving bases", anim)


if __name__ == "__main__":
    unittest.main()
