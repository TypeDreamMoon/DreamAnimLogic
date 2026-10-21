"""Helpers for the tests that generate one program per built-in signature.

A built-in that returns a record value (`spring`, `spring3`) cannot be stored: the generated programs store its members.
A built-in that takes a curve reads it from a settings param (a curve belongs in settings): the programs declare `BuiltinCfg`."""

from dal.types import CURVE, CURVE3, SPRING_MEMBERS

PRELUDE = "settings BuiltinCfg { Curve: curve; Curve3: curve3; }" + chr(10)
CURVE_ARGS = {CURVE: "Cfg.Curve", CURVE3: "Cfg.Curve3"}


def outputs(ret):
    """[(state name suffix, state type name, member access text)] for the result of a built-in call."""
    members = SPRING_MEMBERS.get(ret)
    if not members:
        return [("", ret.name, "")]
    return [(f"_{m}", t, f".{m}") for m, t in members.items()]


def uses_curve(sig):
    return any(p in CURVE_ARGS for p in sig.params)


def arg(sig, i, name):
    """The text of argument i: an input called `name`, or a curve of the settings param."""
    return CURVE_ARGS.get(sig.params[i], name)


def input_decl(sig, i, name):
    """The declaration of the input behind argument i (nothing for a curve)."""
    return "" if sig.params[i] in CURVE_ARGS else f"input {name}: {sig.params[i].name};"
