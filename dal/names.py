"""Names a ``.dal`` program may not declare, because a backend (or Unreal) uses them."""

from __future__ import annotations

import keyword

PY_RESERVED = frozenset(keyword.kwlist) | frozenset(getattr(keyword, "softkwlist", [])) | frozenset(
    {"self", "cls", "True", "False", "None"}
)

CPP_KEYWORDS = frozenset(
    """alignas alignof and and_eq asm auto bitand bitor bool break case catch char char8_t char16_t char32_t
    class compl concept const consteval constexpr constinit const_cast continue co_await co_return co_yield
    decltype default delete do double dynamic_cast else enum explicit export extern false float for friend goto
    if inline int long mutable namespace new noexcept not not_eq nullptr operator or or_eq private protected
    public register reinterpret_cast requires return short signed sizeof static static_assert static_cast
    struct switch template this thread_local throw true try typedef typeid typename union unsigned using
    virtual void volatile wchar_t while xor xor_eq final override""".split()
)

# Macros and symbols that break (or silently change) generated Unreal C++.
UE_RESERVED = frozenset(
    """TRUE FALSE NULL INDEX_NONE check checkf verify ensure ensureMsgf TEXT UE_LOG UPROPERTY UFUNCTION UCLASS USTRUCT
    UENUM GENERATED_BODY interface small near far IN OUT OPTIONAL ERROR DELETE CONST PI MAX_int32 MIN_int32 MAX_FLT
    WITH_EDITOR DalMath""".split()
)

# Members of UAnimInstance that a state/input/param could shadow.
ANIM_INSTANCE_MEMBERS = frozenset(
    """ActiveAnimNotifyEventReference ActiveAnimNotifyState Bones ComponentSpaceFlags CurrentSkeleton NotifyQueue
    OnAllMontageInstancesEnded OnMontageBlendedIn OnMontageBlendingOut OnMontageEnded OnMontageSectionChanged
    OnMontageStarted PostCompileValidationClassName SlotGroupInertializationRequestDataMap
    bPropagateNotifiesToLinkedInstances bQueueMontageEvents bReceiveNotifiesFromLinkedInstances
    bUseMainInstanceMontageEvaluationData bUseMultiThreadedAnimationUpdate bUsingCopyPoseFromMesh RootMotionMode
    Montage_Play Montage_Stop Montage_Pause Montage_Resume""".split()
)

# Attributes of the generated Python class (and its LogicBase) that a DAL name must not hide.
PY_CLASS_API = frozenset(
    "reset snapshot overrides set_input sample_inputs call_entry values host update".split()
)

RESERVED_PREFIXES = ("Dal_", "DAL_", "DalHost_", "DalNotify_", "DalAction_", "DalMontage_", "AnimNotify_", "Montage_", "Native", "Blueprint")


def reserved_reason(name: str, allow_py_keyword: bool = False) -> str | None:
    """Why ``name`` cannot be declared, or None if it is fine."""
    if not allow_py_keyword and name in PY_RESERVED:
        return "it is a Python keyword and the Python backend would emit it as an identifier"
    if name in CPP_KEYWORDS:
        return "it is a C++ keyword"
    if name in UE_RESERVED:
        return "it is a macro or symbol used by Unreal Engine headers"
    if name in PY_CLASS_API:
        return "it is an attribute of the generated class API"
    if name in ANIM_INSTANCE_MEMBERS:
        return "it is a member of UAnimInstance"
    for p in RESERVED_PREFIXES:
        if name.startswith(p):
            return f"names starting with '{p}' are reserved for generated code and the engine"
    return None
