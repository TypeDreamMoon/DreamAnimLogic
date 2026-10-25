"""Compares two dumps of dump_settings.py (the original ALS, the generated one): python diff_dumps.py orig.json gen.json

A Vector2f is not introspectable from Python (its components show as absent on that side) and a collision response container is a list of channels
on the generated side: those are differences of representation, the rest are real."""
import json
import sys

a = json.load(open(sys.argv[1], encoding="utf-8"))
b = json.load(open(sys.argv[2], encoding="utf-8"))


def short(v):
    s = json.dumps(v)
    return s if len(s) < 160 else s[:157] + "..."


def walk(x, y, path, out):
    if isinstance(x, dict) and isinstance(y, dict):
        for k in sorted(set(x) | set(y)):
            if k == "__struct__":
                continue
            if k not in x:
                out.append((path + "." + k, "<absent>", short(y[k])))
            elif k not in y:
                out.append((path + "." + k, short(x[k]), "<absent>"))
            else:
                walk(x[k], y[k], path + "." + k, out)
    elif isinstance(x, list) and isinstance(y, list) and len(x) == len(y):
        for i, (p, q) in enumerate(zip(x, y)):
            walk(p, q, "%s[%d]" % (path, i), out)
    elif x != y:
        if isinstance(x, float) and isinstance(y, float) and abs(x - y) < 1e-4:
            return
        out.append((path, short(x), short(y)))


out = []
for p in sorted(set(a) | set(b)):
    walk(a.get(p), b.get(p), p.split("/")[-1], out)
print(len(out), "differences")
for p, x, y in out:
    print(p, "| orig:", x, "| gen:", y)
