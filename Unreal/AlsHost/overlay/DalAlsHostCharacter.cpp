#include "DalAlsHostCharacter.h"

#include "AlsCharacter.h"
#include "AlsInput.h"
#include "Animation/AnimInstance.h"
#include "Camera/CameraComponent.h"
#include "Components/CapsuleComponent.h"
#include "Components/SkeletalMeshComponent.h"
#include "DalHookProbe.h"
#include "DalRagdollService.h"
#include "EnhancedInputSubsystems.h"
#include "Engine/LocalPlayer.h"
#include "Engine/SkeletalMesh.h"
#include "GameFramework/PlayerController.h"
#include "GameFramework/SpringArmComponent.h"
#include "InputAction.h"
#include "InputMappingContext.h"
#include "UObject/ConstructorHelpers.h"

namespace
{
	template <typename T>
	T* FindAsset(const TCHAR* Path)
	{
		ConstructorHelpers::FObjectFinder<T> Finder(Path);
		return Finder.Succeeded() ? Finder.Object : nullptr;
	}
}

ADalAlsHostCharacter::ADalAlsHostCharacter(const FObjectInitializer& ObjectInitializer) : Super(ObjectInitializer)
{
	// AAlsCharacter's constructor (AlsCharacter.cpp:28-53)
	bUseControllerRotationYaw = false;
	GetCapsuleComponent()->InitCapsuleSize(30.0f, 90.0f);

	if (USkeletalMeshComponent* MeshComponent = GetMesh())
	{
		MeshComponent->SetRelativeLocation_Direct({0.0f, 0.0f, -92.0f});
		MeshComponent->SetRelativeRotation_Direct({0.0f, -90.0f, 0.0f});
		MeshComponent->VisibilityBasedAnimTickOption = EVisibilityBasedAnimTickOption::OnlyTickMontagesWhenNotRendered;
		MeshComponent->bEnableUpdateRateOptimizations = false;

		// A run without a renderer (-nullrhi, scripts/run_editor.ps1 without -Rendered, or DAL_HOST_ALWAYS_TICK_POSE=1 in the environment of the editor): the mesh is never
		// rendered, and a mesh that is not rendered only ticks its montages.  The pose is ticked like a rendered mesh's from the first frame on (otherwise the animation
		// instance has had a different number of updates, i.e. another phase of its 160-frame loops, in every run).
		if (FPlatformMisc::GetEnvironmentVariable(TEXT("DAL_HOST_ALWAYS_TICK_POSE")) == TEXT("1"))
		{
			MeshComponent->VisibilityBasedAnimTickOption = EVisibilityBasedAnimTickOption::AlwaysTickPoseAndRefreshBones;
		}

		// B_Als_Character: the mannequin and the animation blueprint rebuilt from AB_Als.dab
		MeshComponent->SetSkeletalMeshAsset(FindAsset<USkeletalMesh>(TEXT("/ALS/ALS/Character/SKM_Als.SKM_Als")));
		static ConstructorHelpers::FClassFinder<UAnimInstance> AnimClassFinder(TEXT("/ALS/ALS/Character/AB_Als.AB_Als_C"));
		if (AnimClassFinder.Succeeded())
		{
			MeshComponent->SetAnimationMode(EAnimationMode::AnimationBlueprint);
			MeshComponent->SetAnimInstanceClass(AnimClassFinder.Class);
		}
	}

	AlsLogic = CreateDefaultSubobject<UAlsCharacter>(TEXT("AlsLogic"));
	AlsLogic->Settings = FindAsset<UAlsCharacterSettings>(TEXT("/ALS/ALS/Data/Character/CS_Als_Default.CS_Als_Default"));
	AlsLogic->MovementSettings = FindAsset<UAlsMovementSettings>(TEXT("/ALS/ALS/Data/Character/Movement/MS_Als_Normal.MS_Als_Normal"));
	// SelectMantlingSettings is a Blueprint event in ALS (B_Als_Character overrides it): the logic has one param per data asset (als/bindings.json)
	AlsLogic->MantlingHigh = FindAsset<UAlsMantlingSettings>(TEXT("/ALS/ALS/Data/Character/Mantle/MS_Als_High.MS_Als_High"));
	AlsLogic->MantlingHighInAir = FindAsset<UAlsMantlingSettings>(TEXT("/ALS/ALS/Data/Character/Mantle/MS_Als_High_InAir.MS_Als_High_InAir"));
	AlsLogic->MantlingLow = FindAsset<UAlsMantlingSettings>(TEXT("/ALS/ALS/Data/Character/Mantle/MS_Als_Low.MS_Als_Low"));
	AlsLogic->MantlingLowBox = FindAsset<UAlsMantlingSettings>(TEXT("/ALS/ALS/Data/Character/Mantle/MS_Als_Low_Box.MS_Als_Low_Box"));

	AlsInputLogic = CreateDefaultSubobject<UAlsInput>(TEXT("AlsInputLogic"));
	AlsInputLogic->DalAction_LookMouse = FindAsset<UInputAction>(TEXT("/ALS/ALS/Data/Input/IA_Als_LookMouse.IA_Als_LookMouse"));
	AlsInputLogic->DalAction_Look = FindAsset<UInputAction>(TEXT("/ALS/ALS/Data/Input/IA_Als_Look.IA_Als_Look"));
	AlsInputLogic->DalAction_Move = FindAsset<UInputAction>(TEXT("/ALS/ALS/Data/Input/IA_Als_Move.IA_Als_Move"));
	AlsInputLogic->DalAction_Sprint = FindAsset<UInputAction>(TEXT("/ALS/ALS/Data/Input/IA_Als_Sprint.IA_Als_Sprint"));
	AlsInputLogic->DalAction_Walk = FindAsset<UInputAction>(TEXT("/ALS/ALS/Data/Input/IA_Als_Walk.IA_Als_Walk"));
	AlsInputLogic->DalAction_Crouch = FindAsset<UInputAction>(TEXT("/ALS/ALS/Data/Input/IA_Als_Crouch.IA_Als_Crouch"));
	AlsInputLogic->DalAction_Jump = FindAsset<UInputAction>(TEXT("/ALS/ALS/Data/Input/IA_Als_Jump.IA_Als_Jump"));
	AlsInputLogic->DalAction_Aim = FindAsset<UInputAction>(TEXT("/ALS/ALS/Data/Input/IA_Als_Aim.IA_Als_Aim"));
	AlsInputLogic->DalAction_Ragdoll = FindAsset<UInputAction>(TEXT("/ALS/ALS/Data/Input/IA_Als_Ragdoll.IA_Als_Ragdoll"));
	AlsInputLogic->DalAction_Roll = FindAsset<UInputAction>(TEXT("/ALS/ALS/Data/Input/IA_Als_Roll.IA_Als_Roll"));
	AlsInputLogic->DalAction_RotationMode = FindAsset<UInputAction>(TEXT("/ALS/ALS/Data/Input/IA_Als_RotationMode.IA_Als_RotationMode"));
	AlsInputLogic->DalAction_ViewMode = FindAsset<UInputAction>(TEXT("/ALS/ALS/Data/Input/IA_Als_ViewMode.IA_Als_ViewMode"));
	AlsInputLogic->DalAction_SwitchShoulder = FindAsset<UInputAction>(TEXT("/ALS/ALS/Data/Input/IA_Als_SwitchShoulder.IA_Als_SwitchShoulder"));
	InputMappingContext = FindAsset<UInputMappingContext>(TEXT("/ALS/ALS/Data/Input/IMC_Als_Default.IMC_Als_Default"));

	// B_Als_Character.OverlayAnimationInstanceClasses (the 13 overlay modes of AlsOverlayMode, each with the instance AB_Als_<Mode>)
	for (const TCHAR* Mode : {TEXT("Default"), TEXT("Masculine"), TEXT("Feminine"), TEXT("Injured"), TEXT("HandsTied"), TEXT("Rifle"), TEXT("PistolOneHanded"),
	                          TEXT("PistolTwoHanded"), TEXT("Bow"), TEXT("Torch"), TEXT("Binoculars"), TEXT("Box"), TEXT("Barrel")})
	{
		const FString Path = FString::Printf(TEXT("/ALS/ALS/Character/AnimationInstances/Overlays/AB_Als_%s.AB_Als_%s_C"), Mode, Mode);
		if (UClass* OverlayClass = ConstructorHelpersInternal::FindOrLoadClass(const_cast<FString&>(Path), UAnimInstance::StaticClass()))
		{
			OverlayAnimationInstanceClasses.Add(FName(*FString::Printf(TEXT("Als.OverlayMode.%s"), Mode)), OverlayClass);
		}
	}

	RagdollService = CreateDefaultSubobject<UDalRagdollService>(TEXT("RagdollService"));
	HookProbe = CreateDefaultSubobject<UDalHookProbe>(TEXT("HookProbe"));

	CameraArm = CreateDefaultSubobject<USpringArmComponent>(TEXT("CameraArm"));
	CameraArm->SetupAttachment(GetCapsuleComponent());
	CameraArm->TargetArmLength = 300.0f;
	CameraArm->SocketOffset = FVector(0.0f, 0.0f, 60.0f);
	CameraArm->bUsePawnControlRotation = true;
	Camera = CreateDefaultSubobject<UCameraComponent>(TEXT("Camera"));
	Camera->SetupAttachment(CameraArm);
}

void ADalAlsHostCharacter::PostInitializeComponents()
{
	Super::PostInitializeComponents();

	// The overlay layer is linked in this actor's tick (RefreshOverlayLinkedAnimationLayer): the mesh updates its animation after it, like the
	// original, where the Blueprint event ran inside SetOverlayMode before the frame's animation update.
	if (USkeletalMeshComponent* MeshComponent = GetMesh())
	{
		MeshComponent->PrimaryComponentTick.AddPrerequisite(this, PrimaryActorTick);
	}
}

void ADalAlsHostCharacter::BeginPlay()
{
	Super::BeginPlay();

	RefreshOverlayLinkedAnimationLayer();                    // AAlsCharacter::BeginPlay: OnOverlayModeChanged(OverlayMode)
}

void ADalAlsHostCharacter::Tick(const float DeltaSeconds)
{
	Super::Tick(DeltaSeconds);

	if (AlsLogic != nullptr && AlsLogic->OverlayMode != LinkedOverlayMode)
	{
		RefreshOverlayLinkedAnimationLayer();
	}
}

void ADalAlsHostCharacter::RefreshOverlayLinkedAnimationLayer()
{
	if (AlsLogic == nullptr)
	{
		return;
	}
	LinkedOverlayMode = AlsLogic->OverlayMode;
	const TSubclassOf<UAnimInstance>* OverlayClass = OverlayAnimationInstanceClasses.Find(LinkedOverlayMode.GetTagName());
	if (OverlayClass != nullptr && *OverlayClass != nullptr && GetMesh() != nullptr)
	{
		GetMesh()->LinkAnimClassLayers(*OverlayClass);
	}
}

void ADalAlsHostCharacter::NotifyControllerChanged()
{
	if (const APlayerController* PreviousPlayer = Cast<APlayerController>(PreviousController))
	{
		if (UEnhancedInputLocalPlayerSubsystem* InputSubsystem = ULocalPlayer::GetSubsystem<UEnhancedInputLocalPlayerSubsystem>(PreviousPlayer->GetLocalPlayer()))
		{
			InputSubsystem->RemoveMappingContext(InputMappingContext);
		}
	}

	if (APlayerController* NewPlayer = Cast<APlayerController>(GetController()))
	{
		if (UEnhancedInputLocalPlayerSubsystem* InputSubsystem = ULocalPlayer::GetSubsystem<UEnhancedInputLocalPlayerSubsystem>(NewPlayer->GetLocalPlayer()))
		{
			InputSubsystem->AddMappingContext(InputMappingContext, 0);
		}
	}

	Super::NotifyControllerChanged();
}
