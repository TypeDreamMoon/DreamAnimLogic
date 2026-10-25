"""The animation phase of reference takes: python phase_ref.py <folder with als_<take>.dvrec> <out.json>

The pose of the graph's loops (idle, locomotion: 160 frames at 60 fps) depends on how many animation updates the instance had when the recording
started.  This writes {take: world time of the first recorded frame, in frames at 60 fps} for g_record.py (R1_PHASE=<out.json>), which then
waits before it starts the recorder until the world time of the run it records is the same modulo 160 frames (at a fixed 60 fps the update counter of
the instance is that time in frames minus one or two).  Needs the dreamved reader (Blender repo, scripts/modules)."""
import glob
import json
import os
import sys

sys.path.insert(0, r"I:/DCC/blender-moon-engine/scripts/modules")
import numpy as np  # noqa: E402
from dreamved.anim.recording import Recording  # noqa: E402

folder, out = sys.argv[1], sys.argv[2]
result = {}
for path in sorted(glob.glob(os.path.join(folder, "als_*.dvrec"))):
    take = os.path.basename(path)[len("als_"):-len(".dvrec")]
    rec = Recording.load(path)
    result[take] = int(round(float(np.asarray(rec.truth.series("final", "Time.Seconds"), float)[0]) * 60.0))
json.dump(result, open(out, "w", encoding="utf-8"), indent=1, sort_keys=True)
print(out, result)
