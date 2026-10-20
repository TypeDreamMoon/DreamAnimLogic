"""Python stand-ins for the extern functions of als_lean.dal.

The compiler checks that every `extern fn` has a function of the same name and arity here, and
bundles this file into the generated Python module.  The C++ counterpart is AlsLeanLibrary.h.
Keep the two in step: the curve below is an illustrative stand-in for ALS's LeanAmountCurve asset.
"""

# (vertical velocity cm/s, lean multiplier): rising -> lean back, falling -> lean forward.
_LEAN_CURVE = (
    (-600.0, 1.0),
    (-200.0, 0.6),
    (0.0, 0.0),
    (200.0, -0.6),
    (600.0, -1.0),
)


def InAirLeanCurve(VerticalVelocity):
    """Piecewise-linear curve, flat outside the first and last key (like a UCurveFloat)."""
    x = float(VerticalVelocity)
    keys = _LEAN_CURVE
    if x <= keys[0][0]:
        return keys[0][1]
    if x >= keys[-1][0]:
        return keys[-1][1]
    for (x0, y0), (x1, y1) in zip(keys, keys[1:]):
        if x <= x1:
            return y0 + (y1 - y0) * (x - x0) / (x1 - x0)
    return keys[-1][1]
