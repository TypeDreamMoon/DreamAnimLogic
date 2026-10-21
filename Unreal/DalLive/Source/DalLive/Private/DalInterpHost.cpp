#include "DalInterpHost.h"

#include "DalLiveSubsystem.h"

#include "Animation/AnimInstance.h"
#include "Animation/AnimMontage.h"
#include "Animation/AnimTypes.h"
#include "Camera/CameraComponent.h"
#include "CollisionQueryParams.h"
#include "Components/SkeletalMeshComponent.h"
#include "EnhancedInputComponent.h"
#include "EnhancedInputSubsystems.h"
#include "EnhancedPlayerInput.h"
#include "Engine/HitResult.h"
#include "Engine/LocalPlayer.h"
#include "Engine/World.h"
#include "GameFramework/Character.h"
#include "GameFramework/CharacterMovementComponent.h"
#include "GameFramework/Pawn.h"
#include "GameFramework/PlayerController.h"
#include "GameFramework/SpringArmComponent.h"
#include "InputAction.h"
#include "InputActionValue.h"
#include "Kismet/GameplayStatics.h"
#include "Misc/ScopeLock.h"

namespace
{
	const FName KindNotify(TEXT("notify"));
	const FName KindNotifyBegin(TEXT("notify_begin"));
	const FName KindNotifyEnd(TEXT("notify_end"));
	const FName KindMontageEnded(TEXT("montage_ended"));
	const FName KindMontageBlendingOut(TEXT("montage_blending_out"));
	const FName KindInput(TEXT("input"));

	int32 GNextSessionId = 1;

	ETriggerEvent PhaseToTrigger(const FName Phase, bool& bOk)
	{
		bOk = true;
		if (Phase == TEXT("started"))
		{
			return ETriggerEvent::Started;
		}
		if (Phase == TEXT("triggered"))
		{
			return ETriggerEvent::Triggered;
		}
		if (Phase == TEXT("ongoing"))
		{
			return ETriggerEvent::Ongoing;
		}
		if (Phase == TEXT("completed"))
		{
			return ETriggerEvent::Completed;
		}
		if (Phase == TEXT("canceled"))
		{
			return ETriggerEvent::Canceled;
		}
		bOk = false;
		return ETriggerEvent::None;
	}

	/** Struct layouts PublishValues knows, by struct name: kind (1 vec2, 2 vec3, 3 rotator, 4 quat) and double / float precision. */
	void StructLayout(const UScriptStruct* Struct, int32& OutKind, bool& bOutDouble)
	{
		OutKind = -1;
		bOutDouble = true;
		if (Struct == nullptr)
		{
			return;
		}
		const FString Name = Struct->GetName();
		if (Name == TEXT("Vector2D") || Name == TEXT("Vector2d"))
		{
			OutKind = 1;
		}
		else if (Name == TEXT("Vector2f"))
		{
			OutKind = 1;
			bOutDouble = false;
		}
		else if (Name == TEXT("Vector") || Name == TEXT("Vector3d"))
		{
			OutKind = 2;
		}
		else if (Name == TEXT("Vector3f"))
		{
			OutKind = 2;
			bOutDouble = false;
		}
		else if (Name == TEXT("Rotator") || Name == TEXT("Rotator3d"))
		{
			OutKind = 3;
		}
		else if (Name == TEXT("Rotator3f"))
		{
			OutKind = 3;
			bOutDouble = false;
		}
		else if (Name == TEXT("Quat") || Name == TEXT("Quat4d"))
		{
			OutKind = 4;
		}
		else if (Name == TEXT("Quat4f"))
		{
			OutKind = 4;
			bOutDouble = false;
		}
	}

	bool IsScalarProperty(const FProperty* Prop)
	{
		return CastField<FFloatProperty>(Prop) != nullptr || CastField<FDoubleProperty>(Prop) != nullptr
			|| CastField<FIntProperty>(Prop) != nullptr || CastField<FInt64Property>(Prop) != nullptr
			|| CastField<FBoolProperty>(Prop) != nullptr || CastField<FByteProperty>(Prop) != nullptr
			|| CastField<FEnumProperty>(Prop) != nullptr;
	}

	bool WriteScalar(FProperty* Prop, void* Address, const double Value)
	{
		if (FFloatProperty* Float = CastField<FFloatProperty>(Prop))
		{
			Float->SetPropertyValue(Address, static_cast<float>(Value));
		}
		else if (FDoubleProperty* Double = CastField<FDoubleProperty>(Prop))
		{
			Double->SetPropertyValue(Address, Value);
		}
		else if (FIntProperty* Int = CastField<FIntProperty>(Prop))
		{
			Int->SetPropertyValue(Address, static_cast<int32>(FMath::RoundToDouble(Value)));
		}
		else if (FInt64Property* Int64 = CastField<FInt64Property>(Prop))
		{
			Int64->SetPropertyValue(Address, static_cast<int64>(FMath::RoundToDouble(Value)));
		}
		else if (FBoolProperty* Bool = CastField<FBoolProperty>(Prop))
		{
			Bool->SetPropertyValue(Address, Value != 0.0);
		}
		else if (FByteProperty* Byte = CastField<FByteProperty>(Prop))
		{
			Byte->SetPropertyValue(Address, static_cast<uint8>(Value));
		}
		else if (FEnumProperty* Enum = CastField<FEnumProperty>(Prop))
		{
			Enum->GetUnderlyingProperty()->SetIntPropertyValue(Address, static_cast<int64>(FMath::RoundToDouble(Value)));
		}
		else
		{
			return false;
		}
		return true;
	}

	bool WriteStruct(FStructProperty* Prop, void* Address, const uint8 Kind, const double* D)
	{
		int32 Layout = -1;
		bool bDouble = true;
		StructLayout(Prop->Struct, Layout, bDouble);
		if (Layout != static_cast<int32>(Kind))
		{
			return false;
		}
		switch (Kind)
		{
		case 1:
			if (bDouble)
			{
				*static_cast<FVector2D*>(Address) = FVector2D(D[0], D[1]);
			}
			else
			{
				*static_cast<FVector2f*>(Address) = FVector2f(static_cast<float>(D[0]), static_cast<float>(D[1]));
			}
			return true;
		case 2:
			if (bDouble)
			{
				*static_cast<FVector*>(Address) = FVector(D[0], D[1], D[2]);
			}
			else
			{
				*static_cast<FVector3f*>(Address) = FVector3f(static_cast<float>(D[0]), static_cast<float>(D[1]), static_cast<float>(D[2]));
			}
			return true;
		case 3:
			if (bDouble)
			{
				*static_cast<FRotator*>(Address) = FRotator(D[0], D[1], D[2]);
			}
			else
			{
				*static_cast<FRotator3f*>(Address) = FRotator3f(static_cast<float>(D[0]), static_cast<float>(D[1]), static_cast<float>(D[2]));
			}
			return true;
		case 4:
			if (bDouble)
			{
				*static_cast<FQuat*>(Address) = FQuat(D[0], D[1], D[2], D[3]);
			}
			else
			{
				*static_cast<FQuat4f*>(Address) = FQuat4f(static_cast<float>(D[0]), static_cast<float>(D[1]), static_cast<float>(D[2]), static_cast<float>(D[3]));
			}
			return true;
		default:
			return false;
		}
	}

	double ReadScalar(const FProperty* Prop, const void* Address)
	{
		if (const FFloatProperty* Float = CastField<FFloatProperty>(Prop))
		{
			return Float->GetPropertyValue(Address);
		}
		if (const FDoubleProperty* Double = CastField<FDoubleProperty>(Prop))
		{
			return Double->GetPropertyValue(Address);
		}
		if (const FIntProperty* Int = CastField<FIntProperty>(Prop))
		{
			return Int->GetPropertyValue(Address);
		}
		if (const FInt64Property* Int64 = CastField<FInt64Property>(Prop))
		{
			return static_cast<double>(Int64->GetPropertyValue(Address));
		}
		if (const FBoolProperty* Bool = CastField<FBoolProperty>(Prop))
		{
			return Bool->GetPropertyValue(Address) ? 1.0 : 0.0;
		}
		if (const FByteProperty* Byte = CastField<FByteProperty>(Prop))
		{
			return Byte->GetPropertyValue(Address);
		}
		if (const FEnumProperty* Enum = CastField<FEnumProperty>(Prop))
		{
			return static_cast<double>(Enum->GetUnderlyingProperty()->GetSignedIntPropertyValue(Address));
		}
		return 0.0;
	}

	void ReadStruct(const FStructProperty* Prop, const void* Address, const uint8 Kind, double* Out)
	{
		int32 Layout = -1;
		bool bDouble = true;
		StructLayout(Prop->Struct, Layout, bDouble);
		switch (Kind)
		{
		case 1:
			if (bDouble)
			{
				const FVector2D& V = *static_cast<const FVector2D*>(Address);
				Out[0] = V.X;
				Out[1] = V.Y;
			}
			else
			{
				const FVector2f& V = *static_cast<const FVector2f*>(Address);
				Out[0] = V.X;
				Out[1] = V.Y;
			}
			break;
		case 2:
			if (bDouble)
			{
				const FVector& V = *static_cast<const FVector*>(Address);
				Out[0] = V.X;
				Out[1] = V.Y;
				Out[2] = V.Z;
			}
			else
			{
				const FVector3f& V = *static_cast<const FVector3f*>(Address);
				Out[0] = V.X;
				Out[1] = V.Y;
				Out[2] = V.Z;
			}
			break;
		case 3:
			if (bDouble)
			{
				const FRotator& R = *static_cast<const FRotator*>(Address);
				Out[0] = R.Pitch;
				Out[1] = R.Yaw;
				Out[2] = R.Roll;
			}
			else
			{
				const FRotator3f& R = *static_cast<const FRotator3f*>(Address);
				Out[0] = R.Pitch;
				Out[1] = R.Yaw;
				Out[2] = R.Roll;
			}
			break;
		case 4:
			if (bDouble)
			{
				const FQuat& Q = *static_cast<const FQuat*>(Address);
				Out[0] = Q.X;
				Out[1] = Q.Y;
				Out[2] = Q.Z;
				Out[3] = Q.W;
			}
			else
			{
				const FQuat4f& Q = *static_cast<const FQuat4f*>(Address);
				Out[0] = Q.X;
				Out[1] = Q.Y;
				Out[2] = Q.Z;
				Out[3] = Q.W;
			}
			break;
		default:
			break;
		}
	}

	int32 ComponentCount(const uint8 Kind)
	{
		switch (Kind)
		{
		case 0:
			return 1;
		case 1:
			return 2;
		case 2:
		case 3:
			return 3;
		case 4:
			return 4;
		default:
			return 1;
		}
	}
}

UDalInterpHost::UDalInterpHost()
{
	// The same tick group as the generated actor component, so the interpreted twin sees the same frame.
	PrimaryComponentTick.bCanEverTick = true;
	PrimaryComponentTick.TickGroup = TG_PrePhysics;
}

void UDalInterpHost::BeginPlay()
{
	Super::BeginPlay();

	SessionId = GNextSessionId++;
	{
		FScopeLock Lock(&QueueLock);
		EventQueue.Reset();
		NotifyFlags.Reset();
	}
	Targets.Reset();
	bTargetsFresh = false;

	if (UDalLiveSubsystem* Subsystem = UDalLiveSubsystem::Get())
	{
		if (!Subsystem->IsPythonReady() && !bWarnedNoPython)
		{
			bWarnedNoPython = true;
			UE_LOG(LogDalLive, Warning, TEXT("DalInterpHost %s on %s: the DalLive Python side is not running (editor with PythonScriptPlugin only); the component does nothing."),
				*GetName(), *GetNameSafe(GetOwner()));
		}
		Subsystem->RegisterHost(this);
	}
	DalBindHost();
}

void UDalInterpHost::EndPlay(const EEndPlayReason::Type EndPlayReason)
{
	if (UDalLiveSubsystem* Subsystem = UDalLiveSubsystem::Get())
	{
		Subsystem->UnregisterHost(this);
	}
	if (BoundAnim.IsValid())
	{
		UnbindAnim(BoundAnim.Get());
	}
	BoundAnim.Reset();
	UnbindInput();

	Super::EndPlay(EndPlayReason);
}

void UDalInterpHost::TickComponent(const float DeltaTime, const ELevelTick TickType, FActorComponentTickFunction* ThisTickFunction)
{
	Super::TickComponent(DeltaTime, TickType, ThisTickFunction);

	bTargetsFresh = false;
	DalBindHost();

	UDalLiveSubsystem* Subsystem = UDalLiveSubsystem::Get();
	if (Subsystem != nullptr && Subsystem->IsPythonReady())
	{
		int32 PendingEvents = 0;
		int32 PendingFlags = 0;
		{
			FScopeLock Lock(&QueueLock);
			PendingEvents = EventQueue.Num();
			PendingFlags = NotifyFlags.Num();
		}
		const uint64 Start = FPlatformTime::Cycles64();
		Subsystem->OnHostTick.Broadcast(this, DeltaTime, PendingEvents, PendingFlags);
		const double Micros = FPlatformTime::ToMilliseconds64(FPlatformTime::Cycles64() - Start) * 1000.0;
		TickMicrosTotal += Micros;
		TickMicrosMax = FMath::Max(TickMicrosMax, Micros);
		++TickCalls;
		if (TickSamples.Num() < 20000)
		{
			TickSamples.Add(static_cast<float>(Micros));
		}
	}
}

void UDalInterpHost::OrderBeforeMesh()
{
	if (USkeletalMeshComponent* Mesh = FindMesh())
	{
		Mesh->AddTickPrerequisiteComponent(this);
	}
}

void UDalInterpHost::SetStatus(const FString& InStatus, const FString& InLogicName, const FString& InLogicKind, const int32 InGeneration)
{
	Status = InStatus;
	LogicName = InLogicName;
	LogicKind = InLogicKind;
	Generation = InGeneration;
}

// ---- host bindings -------------------------------------------------------------------------------------------------

APawn* UDalInterpHost::GetPawn() const
{
	return Cast<APawn>(GetOwner());
}

ACharacter* UDalInterpHost::GetCharacter() const
{
	return Cast<ACharacter>(GetOwner());
}

UCharacterMovementComponent* UDalInterpHost::GetMovement() const
{
	const ACharacter* Character = GetCharacter();
	return Character != nullptr ? Character->GetCharacterMovement() : nullptr;
}

USkeletalMeshComponent* UDalInterpHost::FindMesh() const
{
	if (const ACharacter* Character = GetCharacter())
	{
		return Character->GetMesh();
	}
	const AActor* Owner = GetOwner();
	return Owner != nullptr ? Owner->FindComponentByClass<USkeletalMeshComponent>() : nullptr;
}

UAnimInstance* UDalInterpHost::GetAnimInstance() const
{
	const USkeletalMeshComponent* Mesh = FindMesh();
	return Mesh != nullptr ? Mesh->GetAnimInstance() : nullptr;
}

USpringArmComponent* UDalInterpHost::GetSpringArm() const
{
	const AActor* Owner = GetOwner();
	return Owner != nullptr ? Owner->FindComponentByClass<USpringArmComponent>() : nullptr;
}

UCameraComponent* UDalInterpHost::GetCamera() const
{
	const AActor* Owner = GetOwner();
	return Owner != nullptr ? Owner->FindComponentByClass<UCameraComponent>() : nullptr;
}

UAnimMontage* UDalInterpHost::GetMontageAsset(const FName MontageName) const
{
	const TObjectPtr<UAnimMontage>* Asset = Montages.Find(MontageName);
	return Asset != nullptr ? Asset->Get() : nullptr;
}

FName UDalInterpHost::MontageNameOf(const UAnimMontage* Montage) const
{
	if (Montage != nullptr)
	{
		for (const TPair<FName, TObjectPtr<UAnimMontage>>& Pair : Montages)
		{
			if (Pair.Value == Montage)
			{
				return Pair.Key;
			}
		}
	}
	return NAME_None;
}

void UDalInterpHost::DalBindHost()
{
	UAnimInstance* Anim = GetAnimInstance();
	if (Anim != BoundAnim.Get())
	{
		if (BoundAnim.IsValid())
		{
			UnbindAnim(BoundAnim.Get());
		}
		BoundAnim = Anim;
		++AnimEpoch;
		if (Anim != nullptr)
		{
			BindAnim(Anim);
		}
	}

	const APawn* Pawn = GetPawn();
	UEnhancedInputComponent* Input = Pawn != nullptr ? Cast<UEnhancedInputComponent>(Pawn->InputComponent) : nullptr;
	if (Input != nullptr && (Input != BoundInput.Get() || bInputWatchDirty))
	{
		RebindInput(Input);
	}
}

void UDalInterpHost::BindAnim(UAnimInstance* Anim)
{
	Anim->OnMontageEnded.AddUniqueDynamic(this, &UDalInterpHost::HandleMontageEnded);
	Anim->OnMontageBlendingOut.AddUniqueDynamic(this, &UDalInterpHost::HandleMontageBlendingOut);
	Anim->OnPlayMontageNotifyBegin.AddUniqueDynamic(this, &UDalInterpHost::HandlePlayMontageNotifyBegin);
	Anim->OnPlayMontageNotifyEnd.AddUniqueDynamic(this, &UDalInterpHost::HandlePlayMontageNotifyEnd);
}

void UDalInterpHost::UnbindAnim(UAnimInstance* Anim)
{
	Anim->OnMontageEnded.RemoveDynamic(this, &UDalInterpHost::HandleMontageEnded);
	Anim->OnMontageBlendingOut.RemoveDynamic(this, &UDalInterpHost::HandleMontageBlendingOut);
	Anim->OnPlayMontageNotifyBegin.RemoveDynamic(this, &UDalInterpHost::HandlePlayMontageNotifyBegin);
	Anim->OnPlayMontageNotifyEnd.RemoveDynamic(this, &UDalInterpHost::HandlePlayMontageNotifyEnd);
}

void UDalInterpHost::UnbindInput()
{
	if (UEnhancedInputComponent* Input = BoundInput.Get())
	{
		for (const uint32 Handle : InputBindingHandles)
		{
			Input->RemoveBindingByHandle(Handle);
		}
	}
	InputBindingHandles.Reset();
	BoundInput.Reset();
}

void UDalInterpHost::RebindInput(UEnhancedInputComponent* Input)
{
	UnbindInput();
	BoundInput = Input;
	bInputWatchDirty = false;

	for (const TPair<FName, FName>& Watch : WatchedInputs)
	{
		const TObjectPtr<UInputAction>* Asset = Actions.Find(Watch.Key);
		bool bOk = false;
		const ETriggerEvent Trigger = PhaseToTrigger(Watch.Value, bOk);
		if (Asset == nullptr || Asset->Get() == nullptr || !bOk)
		{
			UE_LOG(LogDalLive, Warning, TEXT("DalInterpHost %s: no input action asset for '%s' (phase %s); assign it in Actions."),
				*GetName(), *Watch.Key.ToString(), *Watch.Value.ToString());
			continue;
		}
		const FName ActionName = Watch.Key;
		const FName PhaseName = Watch.Value;
		const TWeakObjectPtr<UDalInterpHost> WeakThis(this);
		FEnhancedInputActionEventBinding& Binding = Input->BindActionValueLambda(Asset->Get(), Trigger,
			[WeakThis, ActionName, PhaseName](const FInputActionValue& Value)
			{
				if (UDalInterpHost* Self = WeakThis.Get())
				{
					Self->PostInput(ActionName, PhaseName, Value);
				}
			});
		InputBindingHandles.Add(Binding.GetHandle());
	}
}

void UDalInterpHost::ConfigureWatch(const TArray<FName>& EventNotifies, const TArray<FName>& InFlagNotifies, const bool bMontageEnded, const bool bMontageBlendingOut, const TArray<FString>& InputWatches)
{
	{
		FScopeLock Lock(&QueueLock);
		WatchedNotifies.Reset();
		WatchedNotifies.Append(EventNotifies);
		WatchedNotifies.Append(InFlagNotifies);
		FlagNotifies.Reset();
		FlagNotifies.Append(InFlagNotifies);
		NotifyFlags.Reset();
		bWatchMontageEnded = bMontageEnded;
		bWatchBlendingOut = bMontageBlendingOut;
	}

	TArray<TPair<FName, FName>> NewInputs;
	for (const FString& Watch : InputWatches)
	{
		FString Action;
		FString Phase;
		if (Watch.Split(TEXT(":"), &Action, &Phase))
		{
			NewInputs.AddUnique(TPair<FName, FName>(FName(*Action), FName(*Phase)));
		}
	}
	if (NewInputs != WatchedInputs)
	{
		WatchedInputs = MoveTemp(NewInputs);
		bInputWatchDirty = true;
	}
	DalBindHost();
}

// ---- events ------------------------------------------------------------------------------------------------------------

void UDalInterpHost::PostEvent(const FDalLiveEvent& Event)
{
	FScopeLock Lock(&QueueLock);
	EventQueue.Add(Event);
}

TArray<FDalLiveEvent> UDalInterpHost::TakeEvents()
{
	FScopeLock Lock(&QueueLock);
	TArray<FDalLiveEvent> Out = MoveTemp(EventQueue);
	EventQueue.Reset();
	return Out;
}

bool UDalInterpHost::ConsumeNotifyFlag(const FName NotifyName)
{
	FScopeLock Lock(&QueueLock);
	return NotifyFlags.Remove(NotifyName) > 0;
}

void UDalInterpHost::PostInput(const FName ActionName, const FName PhaseName, const FInputActionValue& Value)
{
	FDalLiveEvent Event;
	Event.Kind = KindInput;
	Event.Name = ActionName;
	Event.Phase = PhaseName;
	Event.Value = Value.Get<FVector>();
	PostEvent(Event);
}

void UDalInterpHost::HandleMontageEnded(UAnimMontage* Montage, const bool bInterrupted)
{
	if (!bWatchMontageEnded)
	{
		return;
	}
	const FName Name = MontageNameOf(Montage);
	if (!Name.IsNone())
	{
		FDalLiveEvent Event;
		Event.Kind = KindMontageEnded;
		Event.Name = Name;
		Event.bFlag = bInterrupted;
		PostEvent(Event);
	}
}

void UDalInterpHost::HandleMontageBlendingOut(UAnimMontage* Montage, const bool bInterrupted)
{
	if (!bWatchBlendingOut)
	{
		return;
	}
	const FName Name = MontageNameOf(Montage);
	if (!Name.IsNone())
	{
		FDalLiveEvent Event;
		Event.Kind = KindMontageBlendingOut;
		Event.Name = Name;
		Event.bFlag = bInterrupted;
		PostEvent(Event);
	}
}

void UDalInterpHost::HandlePlayMontageNotifyBegin(const FName NotifyName, const FBranchingPointNotifyPayload& Payload)
{
	{
		FScopeLock Lock(&QueueLock);
		if (!WatchedNotifies.Contains(NotifyName))
		{
			return;
		}
	}
	// a notify window (PlayMontageNotifyWindow) is a state; a PlayMontageNotify is a point
	const bool bWindow = Payload.NotifyEvent != nullptr && Payload.NotifyEvent->NotifyStateClass != nullptr;
	FDalLiveEvent Event;
	Event.Kind = bWindow ? KindNotifyBegin : KindNotify;
	Event.Name = NotifyName;
	PostEvent(Event);
	if (!bWindow)
	{
		FScopeLock Lock(&QueueLock);
		if (FlagNotifies.Contains(NotifyName))
		{
			NotifyFlags.Add(NotifyName);
		}
	}
}

void UDalInterpHost::HandlePlayMontageNotifyEnd(const FName NotifyName, const FBranchingPointNotifyPayload& Payload)
{
	{
		FScopeLock Lock(&QueueLock);
		if (!WatchedNotifies.Contains(NotifyName))
		{
			return;
		}
	}
	FDalLiveEvent Event;
	Event.Kind = KindNotifyEnd;
	Event.Name = NotifyName;
	PostEvent(Event);
}

// ---- reads, queries ------------------------------------------------------------------------------------------------------

FVector UDalInterpHost::GetActionValue(const FName ActionName)
{
	const TObjectPtr<UInputAction>* Asset = Actions.Find(ActionName);
	const APawn* Pawn = GetPawn();
	const APlayerController* Controller = Pawn != nullptr ? Cast<APlayerController>(Pawn->GetController()) : nullptr;
	const ULocalPlayer* LocalPlayer = Controller != nullptr ? Controller->GetLocalPlayer() : nullptr;
	const UEnhancedInputLocalPlayerSubsystem* Subsystem = LocalPlayer != nullptr ? LocalPlayer->GetSubsystem<UEnhancedInputLocalPlayerSubsystem>() : nullptr;
	const UEnhancedPlayerInput* PlayerInput = Subsystem != nullptr ? Subsystem->GetPlayerInput() : nullptr;
	if (Asset == nullptr || Asset->Get() == nullptr)
	{
		if (!WarnedActions.Contains(ActionName))
		{
			WarnedActions.Add(ActionName);
			UE_LOG(LogDalLive, Warning, TEXT("DalInterpHost %s: no input action asset assigned for '%s' (Actions map); it reads as zero."), *GetName(), *ActionName.ToString());
		}
		return FVector::ZeroVector;
	}
	if (PlayerInput == nullptr)
	{
		return FVector::ZeroVector;
	}
	return PlayerInput->GetActionValue(Asset->Get()).Get<FVector>();
}

FDalLiveHit UDalInterpHost::TraceLine(const FVector Start, const FVector End)
{
	FDalLiveHit Result;
	Result.Point = End;
	const UWorld* World = GetWorld();
	if (World == nullptr)
	{
		return Result;
	}
	FHitResult Hit;
	FCollisionQueryParams Params(SCENE_QUERY_STAT(DalTrace), false, GetOwner());
	if (World->LineTraceSingleByChannel(Hit, Start, End, TraceChannel, Params))
	{
		Result.bBlocking = true;
		Result.Point = Hit.ImpactPoint;
		Result.Normal = Hit.ImpactNormal;
		Result.Distance = Hit.Distance;
		Result.Time = Hit.Time;
	}
	return Result;
}

FDalLiveHit UDalInterpHost::TraceSphere(const FVector Start, const FVector End, const float Radius)
{
	FDalLiveHit Result;
	Result.Point = End;
	const UWorld* World = GetWorld();
	if (World == nullptr)
	{
		return Result;
	}
	FHitResult Hit;
	FCollisionQueryParams Params(SCENE_QUERY_STAT(DalTrace), false, GetOwner());
	if (World->SweepSingleByChannel(Hit, Start, End, FQuat::Identity, TraceChannel, FCollisionShape::MakeSphere(Radius), Params))
	{
		Result.bBlocking = true;
		Result.Point = Hit.ImpactPoint;
		Result.Normal = Hit.ImpactNormal;
		Result.Distance = Hit.Distance;
		Result.Time = Hit.Time;
	}
	return Result;
}

void UDalInterpHost::RefreshTargets()
{
	if (bTargetsFresh)
	{
		return;
	}
	bTargetsFresh = true;
	Targets.Reset();
	const UWorld* World = GetWorld();
	if (World == nullptr)
	{
		return;
	}
	TArray<AActor*> Found;
	UGameplayStatics::GetAllActorsWithTag(World, TargetTag, Found);
	for (AActor* Actor : Found)
	{
		if (Actor != GetOwner())
		{
			Targets.Add(Actor);
		}
	}
}

int32 UDalInterpHost::TargetCount()
{
	RefreshTargets();
	return Targets.Num();
}

int32 UDalInterpHost::TargetId(const int32 Index)
{
	RefreshTargets();
	const AActor* Actor = Targets.IsValidIndex(Index) ? Targets[Index].Get() : nullptr;
	return Actor != nullptr ? static_cast<int32>(Actor->GetUniqueID()) : -1;
}

FVector UDalInterpHost::TargetLocation(const int32 Index)
{
	RefreshTargets();
	const AActor* Actor = Targets.IsValidIndex(Index) ? Targets[Index].Get() : nullptr;
	return Actor != nullptr ? Actor->GetActorLocation() : FVector::ZeroVector;
}

int32 UDalInterpHost::TargetIndex(const int32 Id)
{
	RefreshTargets();
	for (int32 Index = 0; Index < Targets.Num(); ++Index)
	{
		const AActor* Actor = Targets[Index].Get();
		if (Actor != nullptr && static_cast<int32>(Actor->GetUniqueID()) == Id)
		{
			return Index;
		}
	}
	return -1;
}

// ---- writes by property name (anim logic -> graph variables) ----------------------------------------------------------------

TArray<FName> UDalInterpHost::SetPublishPlan(UObject* Target, const TArray<FName>& Names, const TArray<uint8>& Kinds)
{
	TArray<FName> Accepted;
	PublishPlan.Reset();
	PublishTarget = Target;
	PublishClass = Target != nullptr ? Target->GetClass() : nullptr;
	if (Target == nullptr || Names.Num() != Kinds.Num())
	{
		return Accepted;
	}
	const UClass* Class = Target->GetClass();
	for (int32 Index = 0; Index < Names.Num(); ++Index)
	{
		FProperty* Property = Class->FindPropertyByName(Names[Index]);
		if (Property == nullptr)
		{
			continue;
		}
		const uint8 Kind = Kinds[Index];
		bool bOk = false;
		if (Kind == 0)
		{
			bOk = IsScalarProperty(Property);
		}
		else if (const FStructProperty* StructProperty = CastField<FStructProperty>(Property))
		{
			int32 Layout = -1;
			bool bDouble = true;
			StructLayout(StructProperty->Struct, Layout, bDouble);
			bOk = Layout == static_cast<int32>(Kind);
		}
		if (bOk)
		{
			FPublishEntry Entry;
			Entry.Property = Property;
			Entry.Kind = Kind;
			PublishPlan.Add(Entry);
			Accepted.Add(Names[Index]);
		}
	}
	return Accepted;
}

int32 UDalInterpHost::PublishValues(const TArray<double>& Data)
{
	UObject* Target = PublishTarget.Get();
	if (Target == nullptr || Target->GetClass() != PublishClass.Get())
	{
		return -1;
	}
	int32 Written = 0;
	int32 Offset = 0;
	for (const FPublishEntry& Entry : PublishPlan)
	{
		const int32 Count = ComponentCount(Entry.Kind);
		if (Offset + Count > Data.Num())
		{
			break;
		}
		void* Address = Entry.Property->ContainerPtrToValuePtr<void>(Target);
		bool bOk = false;
		if (Entry.Kind == 0)
		{
			bOk = WriteScalar(Entry.Property, Address, Data[Offset]);
		}
		else if (FStructProperty* StructProperty = CastField<FStructProperty>(Entry.Property))
		{
			bOk = WriteStruct(StructProperty, Address, Entry.Kind, Data.GetData() + Offset);
		}
		Written += bOk ? 1 : 0;
		Offset += Count;
	}
	return Written;
}

TArray<FName> UDalInterpHost::SetReadPlan(UObject* Target, const TArray<FName>& Names, const TArray<uint8>& Kinds)
{
	TArray<FName> Accepted;
	ReadPlan.Reset();
	ReadTarget = Target;
	ReadClass = Target != nullptr ? Target->GetClass() : nullptr;
	if (Target == nullptr || Names.Num() != Kinds.Num())
	{
		return Accepted;
	}
	const UClass* Class = Target->GetClass();
	for (int32 Index = 0; Index < Names.Num(); ++Index)
	{
		FProperty* Property = Class->FindPropertyByName(Names[Index]);
		if (Property == nullptr)
		{
			continue;
		}
		const uint8 Kind = Kinds[Index];
		bool bOk = false;
		if (Kind == 0)
		{
			bOk = IsScalarProperty(Property);
		}
		else if (const FStructProperty* StructProperty = CastField<FStructProperty>(Property))
		{
			int32 Layout = -1;
			bool bDouble = true;
			StructLayout(StructProperty->Struct, Layout, bDouble);
			bOk = Layout == static_cast<int32>(Kind);
		}
		if (bOk)
		{
			FPublishEntry Entry;
			Entry.Property = Property;
			Entry.Kind = Kind;
			ReadPlan.Add(Entry);
			Accepted.Add(Names[Index]);
		}
	}
	return Accepted;
}

TArray<double> UDalInterpHost::ReadValues()
{
	TArray<double> Out;
	UObject* Target = ReadTarget.Get();
	if (Target == nullptr || Target->GetClass() != ReadClass.Get())
	{
		return Out;
	}
	for (const FPublishEntry& Entry : ReadPlan)
	{
		const void* Address = Entry.Property->ContainerPtrToValuePtr<void>(Target);
		const int32 Count = ComponentCount(Entry.Kind);
		const int32 Start = Out.Num();
		Out.AddZeroed(Count);
		if (Entry.Kind == 0)
		{
			Out[Start] = ReadScalar(Entry.Property, Address);
		}
		else if (const FStructProperty* StructProperty = CastField<FStructProperty>(Entry.Property))
		{
			ReadStruct(StructProperty, Address, Entry.Kind, Out.GetData() + Start);
		}
	}
	return Out;
}

void UDalInterpHost::GetTickStats(double& OutTotalMicros, double& OutMaxMicros, int32& OutCalls) const
{
	OutTotalMicros = TickMicrosTotal;
	OutMaxMicros = TickMicrosMax;
	OutCalls = TickCalls;
}

TArray<float> UDalInterpHost::GetTickSamples() const
{
	return TickSamples;
}

void UDalInterpHost::ResetTickStats()
{
	TickSamples.Reset();
	TickMicrosTotal = 0.0;
	TickMicrosMax = 0.0;
	TickCalls = 0;
}
