#include "DalRootMotionSource_Override.h"

#include "DalHost.h"
#include "GameFramework/Character.h"
#include "GameFramework/CharacterMovementComponent.h"

#include UE_INLINE_GENERATED_CPP_BY_NAME(DalRootMotionSource_Override)

FDalRootMotionSource_Override::FDalRootMotionSource_Override()
{
	Priority = 1000;                               // above everything else a project applies (ALS mantling uses the same)
	Duration = -1.0f;                              // until it is removed
	AccumulateMode = ERootMotionAccumulateMode::Override;
}

FRootMotionSource* FDalRootMotionSource_Override::Clone() const
{
	return new FDalRootMotionSource_Override{*this};
}

bool FDalRootMotionSource_Override::Matches(const FRootMotionSource* Other) const
{
	// One override source per character: any two of them are the same source (their values change every frame).
	return Other != nullptr && Other->GetScriptStruct() == GetScriptStruct() && Priority == Other->Priority && InstanceName == Other->InstanceName;
}

bool FDalRootMotionSource_Override::UpdateStateFrom(const FRootMotionSource* SourceToTakeStateFrom, const bool bMarkForSimulatedCatchup)
{
	if (!FRootMotionSource::UpdateStateFrom(SourceToTakeStateFrom, bMarkForSimulatedCatchup))
	{
		return false;
	}

	const auto* Other = static_cast<const FDalRootMotionSource_Override*>(SourceToTakeStateFrom); // NOLINT(cppcoreguidelines-pro-type-static-cast-downcast)
	Velocity = Other->Velocity;
	RotationDelta = Other->RotationDelta;
	return true;
}

void FDalRootMotionSource_Override::PrepareRootMotion(const float SimulationDeltaTime, const float DeltaTime,
                                                      const ACharacter& Character, const UCharacterMovementComponent& Movement)
{
	SetTime(GetTime() + SimulationDeltaTime);

	if (DeltaTime <= UE_SMALL_NUMBER)
	{
		RootMotionParams.Clear();
		return;
	}

	// The logic that drives the override (ALS's mantling: FAlsRootMotionSource_Mantling::PrepareRootMotion) works out the velocity of this step now,
	// where the engine prepares its root motion sources: inside PerformMovement, after the character has followed its movement base.  Its block
	// (update in prepare_root_motion) hands over a root_motion_override that changes Velocity / RotationDelta of this source.
	static const FName PrepareRootMotionHook(TEXT("prepare_root_motion"));
	DalHost::FEventRouter::RunHook(&Character, PrepareRootMotionHook, DeltaTime);

	// The translation of an Override source's root motion is a velocity (cm/s); the rotation is the delta of this update.
	RootMotionParams.Set(FTransform{RotationDelta, Velocity});
}

bool FDalRootMotionSource_Override::NetSerialize(FArchive& Archive, UPackageMap* Map, bool& bSuccess)
{
	if (!FRootMotionSource::NetSerialize(Archive, Map, bSuccess))
	{
		bSuccess = false;
		return false;
	}

	bSuccess = true;
	Archive << Velocity;
	Archive << RotationDelta;
	return bSuccess;
}

UScriptStruct* FDalRootMotionSource_Override::GetScriptStruct() const
{
	return StaticStruct();
}

FString FDalRootMotionSource_Override::ToSimpleString() const
{
	return FString::Printf(TEXT("FDalRootMotionSource_Override (%s, %d)"), *InstanceName.ToString(), LocalID);
}
