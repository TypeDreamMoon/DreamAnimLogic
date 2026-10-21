"""Names a ``.dal`` program may not declare, because a backend (or Unreal) uses them."""

from __future__ import annotations

import keyword
import re

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
) | frozenset(
    # locals and parameters of the generated sampling code and of the overrides
    """Pawn Character Movement World PlayerInput LocalPlayer Subsystem Controller
    DeltaSeconds DeltaTime TickType ThisTickFunction EndPlayReason""".split()
)

# The locals of the generated sampling code.  A peer may carry one of these names (`peer Character: AlsCharacter;`): the
# generator renames its own local then.
SAMPLE_LOCALS = frozenset("Pawn Character Movement World PlayerInput LocalPlayer Subsystem Controller".split())

# Members of UAnimInstance that a state/input/param could shadow.
ANIM_INSTANCE_MEMBERS = frozenset(
    """ActiveAnimNotifyEventReference ActiveAnimNotifyState Bones ComponentSpaceFlags CurrentSkeleton NotifyQueue
    OnAllMontageInstancesEnded OnMontageBlendedIn OnMontageBlendingOut OnMontageEnded OnMontageSectionChanged
    OnMontageStarted PostCompileValidationClassName SlotGroupInertializationRequestDataMap
    bPropagateNotifiesToLinkedInstances bQueueMontageEvents bReceiveNotifiesFromLinkedInstances
    bUseMainInstanceMontageEvaluationData bUseMultiThreadedAnimationUpdate bUsingCopyPoseFromMesh RootMotionMode
    Montage_Play Montage_Stop Montage_Pause Montage_Resume""".split()
)

# Members of UActorComponent (and UObject) that a state/input/param of actor logic could shadow.
ACTOR_COMPONENT_MEMBERS = frozenset(
    """Activate Deactivate ToggleActive IsActive SetActive GetOwner GetWorld Owner bAutoActivate bIsActive ComponentTags
    PrimaryComponentTick RegisterComponent UnregisterComponent DestroyComponent IsRegistered BeginPlay EndPlay
    TickComponent InitializeComponent UninitializeComponent OnRegister OnUnregister GetName GetClass GetOuter IsA
    Rename""".split()
)

# Attributes of the generated Python class (and its LogicBase) that a DAL name must not hide.
PY_CLASS_API = frozenset(
    ("reset snapshot overrides set_input sample_inputs call_entry values host update update_phase begin_play end_play "
     "peers connect unresolved variables dal_get dal_call dal_enqueue take_commands post_event").split()
)

RESERVED_PREFIXES = ("Dal_", "DAL_", "DalHost_", "DalNotify_", "DalAction_", "DalMontage_", "AnimNotify_", "Montage_", "Native", "Blueprint")

# Everything the C++ backend adds to the generated class is called Dal<Upper> / Dal_<...> / bDal<Upper>.
_GENERATED_NAME = re.compile(r"^b?Dal[A-Z_]")


def reserved_reason(name: str, allow_py_keyword: bool = False, allow_sample_locals: bool = False) -> str | None:
    """Why ``name`` cannot be declared, or None if it is fine."""
    if not allow_py_keyword and name in PY_RESERVED:
        return "it is a Python keyword and the Python backend would emit it as an identifier"
    if name in CPP_KEYWORDS:
        return "it is a C++ keyword"
    if name in UE_RESERVED and not (allow_sample_locals and name in SAMPLE_LOCALS):
        return "it is a macro or symbol used by Unreal Engine headers"
    if name in PY_CLASS_API:
        return "it is an attribute of the generated class API"
    if name in ANIM_INSTANCE_MEMBERS:
        return "it is a member of UAnimInstance"
    if name in ACTOR_COMPONENT_MEMBERS:
        return "it is a member of UActorComponent"
    if _GENERATED_NAME.match(name):
        return "names starting with 'Dal' followed by a capital letter or '_' are reserved for generated code"
    for p in RESERVED_PREFIXES:
        if name.startswith(p):
            return f"names starting with '{p}' are reserved for generated code and the engine"
    return None
