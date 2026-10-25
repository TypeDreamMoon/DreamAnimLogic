// FDalRootMotionSource_Override: the root motion source behind the host commands root_motion_override / root_motion_clear.
//
// The generated logic computes, every frame, the velocity the actor must have and the rotation it must turn by (ALS mantling: the warped
// target transform minus the current actor transform, AlsRootMotionSource_Mantling.cpp:117-127, Sixze, MIT) and hands them to
// UDalCharacterMovementComponent::SetRootMotionOverride.  The source is what the engine's root motion machinery needs to carry them:
// an Override-mode source of priority 1000 and no duration, whose root motion is the transform (rotation delta, velocity) it was given.

#pragma once

#include "CoreMinimal.h"
#include "GameFramework/RootMotionSource.h"
#include "DalRootMotionSource_Override.generated.h"

USTRUCT()
struct DALRUNTIME_API FDalRootMotionSource_Override : public FRootMotionSource
{
	GENERATED_BODY()

public:
	/** The velocity of the actor in world space (cm/s). */
	UPROPERTY()
	FVector Velocity{ForceInit};

	/** The rotation applied to the actor in every update (multiplied in front of its current rotation). */
	UPROPERTY()
	FQuat RotationDelta{FQuat::Identity};

public:
	FDalRootMotionSource_Override();

	virtual FRootMotionSource* Clone() const override;

	virtual bool Matches(const FRootMotionSource* Other) const override;

	virtual bool UpdateStateFrom(const FRootMotionSource* SourceToTakeStateFrom, bool bMarkForSimulatedCatchup = false) override;

	virtual void PrepareRootMotion(float SimulationDeltaTime, float DeltaTime, const ACharacter& Character,
	                               const UCharacterMovementComponent& Movement) override;

	virtual bool NetSerialize(FArchive& Archive, UPackageMap* Map, bool& bSuccess) override;

	virtual UScriptStruct* GetScriptStruct() const override;

	virtual FString ToSimpleString() const override;
};

template <>
struct TStructOpsTypeTraits<FDalRootMotionSource_Override> : public TStructOpsTypeTraitsBase2<FDalRootMotionSource_Override>
{
	enum
	{
		WithNetSerializer = true
	};
};
