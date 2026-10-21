"""The property bag (`CppOptions.property_bag`, `python -m dal compile --bag`): U<Logic>Bag is a UAnimInstance with the
same UPROPERTYs as U<Logic> and none of the logic -- the parent class of an Animation Blueprint while DalLive interprets
the program (Unreal/DalLive).  Off by default: the generated files of every other program stay byte-identical.
"""

import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest

import _bootstrap  # noqa: F401
from _bootstrap import EXAMPLES, ROOT

import dal
from dal.cppbackend import CppOptions, generate_cpp


def example(name):
    return dal.check_file(os.path.join(EXAMPLES, name)).module


def class_body(header: str, cls: str) -> str:
    m = re.search(r"class (?:\w+_API )?" + cls + r" : public \w+\n\{\n(.*?)\n\};", header, re.S)
    assert m, cls
    return m.group(1)


def property_lines(body: str) -> list[str]:
    """Every UPROPERTY with the declaration that follows it, and the host-source comment above an input."""
    lines = body.split("\n")
    out = []
    for i, ln in enumerate(lines):
        if ln.strip().startswith("UPROPERTY") and not any(k in lines[i + 1] for k in ("DalAction_", "DalMontage_", "DalTargetTag", "DalTraceChannel")):
            out.append(ln.strip() + " | " + lines[i + 1].strip())
    return out


class PropertyBagTests(unittest.TestCase):
    def test_off_by_default_and_the_main_class_is_unchanged(self):
        for name in ("als_lean.dal", "hero_gait.dal", "melee_combo.dal", "lockon_camera.dal"):
            module = example(name)
            plain = generate_cpp(module, CppOptions(module_api="X_API"))
            bag = generate_cpp(module, CppOptions(module_api="X_API", property_bag=True))
            self.assertNotIn("Bag", plain[f"{module.name}.h"], name)
            self.assertEqual(plain[f"{module.name}.cpp"], bag[f"{module.name}.cpp"], name)       # the source never changes
            if module.kind == "actor":
                self.assertEqual(plain, bag, name)                                              # no bag for component logic
            else:
                self.assertTrue(bag[f"{module.name}.h"].startswith(plain[f"{module.name}.h"].rstrip("\n")), name)

    def test_bag_has_the_same_properties_and_no_logic(self):
        module = example("hero_gait.dal")
        header = generate_cpp(module, CppOptions(module_api="X_API", property_bag=True))["HeroGait.h"]
        main = class_body(header, "UHeroGait")
        bag = class_body(header, "UHeroGaitBag")
        self.assertIn("class X_API UHeroGaitBag : public UAnimInstance", header)
        self.assertEqual(property_lines(main), property_lines(bag))
        self.assertGreater(len(property_lines(bag)), 10)
        for logic in ("Native", "DalUpdate", "DalSampleInputs", "UFUNCTION", "AnimNotify_", "ResetSteps", "private:", "protected:"):
            self.assertNotIn(logic, bag)
        self.assertEqual(header.count("GENERATED_BODY()"), 2)
        self.assertEqual(header.count('#include "HeroGait.generated.h"'), 1)

    def test_bag_follows_the_parent_class_option(self):
        module = example("hero_gait.dal")
        header = generate_cpp(module, CppOptions(parent_class="UMyBase", parent_include="MyBase.h", property_bag=True))["HeroGait.h"]
        self.assertIn("class UHeroGaitBag : public UMyBase", header)

    def test_command_line_flag(self):
        tmp = tempfile.mkdtemp(prefix="dal_bag_")
        self.addCleanup(shutil.rmtree, tmp, True)
        env = dict(os.environ, PYTHONDONTWRITEBYTECODE="1", PYTHONIOENCODING="utf-8")
        run = lambda *a: subprocess.run([sys.executable, "-m", "dal", *a], cwd=ROOT, env=env, capture_output=True, text=True, encoding="utf-8")
        p = run("compile", os.path.join(EXAMPLES, "hero_gait.dal"), "--cpp", tmp, "--bag")
        self.assertEqual(p.returncode, 0, p.stderr)
        with open(os.path.join(tmp, "HeroGait.h"), encoding="utf-8") as f:
            self.assertIn("UHeroGaitBag", f.read())
        p = run("compile", os.path.join(EXAMPLES, "hero_gait.dal"), "--cpp", os.path.join(tmp, "plain"))
        self.assertEqual(p.returncode, 0, p.stderr)
        with open(os.path.join(tmp, "plain", "HeroGait.h"), encoding="utf-8") as f:
            self.assertNotIn("Bag", f.read())


if __name__ == "__main__":
    unittest.main()
