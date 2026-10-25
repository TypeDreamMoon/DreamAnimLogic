// UDalRagdollService: the physics half of ALS ragdolling, behind the host commands ragdoll_begin / ragdoll_end / ragdoll_limit_speed /
// ragdoll_set_motor_strength / ragdoll_add_force.
//
// Ported from AAlsCharacter::StartRagdollingImplementation, StopRagdollingImplementation, ConstraintRagdollSpeed and the physics
// calls of RefreshRagdolling (AlsCharacter_Actions.cpp:759-844, 868-961, 1001-1165; ALS-Refactored, Sixze, MIT).  What stays in the
// .dal logic: when ragdolling may start and stop, the locomotion action, the capsule following the pelvis (a ground sweep and
// set_actor_location), the facing of the character on getting up, the pose snapshot, the get-up montage.  This component only opens and closes
// the mesh's physics simulation and moves bodies.
//
// Add it to the character (a component of the actor is what the generated code looks for: FindComponentByClass).

#pragma once

#include "CoreMinimal.h"
#include "Components/ActorComponent.h"
#include "DalRagdollService.generated.h"

class ACharacter;
class USkeletalMeshComponent;

UCLASS(ClassGroup = "DAL", Meta = (BlueprintSpawnableComponent))
class DALRUNTIME_API UDalRagdollService : public UActorComponent
{
	GENERATED_BODY()

public:
	/** The bone names the physics asset must have bodies for (ALS: AlsConstants.h). */
	UPROPERTY(EditAnywhere, BlueprintReadWrite, Category = "DAL|Ragdoll")
	FName PelvisBoneName = TEXT("pelvis");

	UPROPERTY(EditAnywhere, BlueprintReadWrite, Category = "DAL|Ragdoll")
	FName Spine03BoneName = TEXT("spine_03");

	/** How many frames the speed limit of Begin stays on (ALS: 8). */
	UPROPERTY(EditAnywhere, BlueprintReadWrite, Category = "DAL|Ragdoll", Meta = (ClampMin = 0))
	int32 SpeedLimitFrames = 8;

public:
	UDalRagdollService();

	virtual void TickComponent(float DeltaTime, ELevelTick TickType, FActorComponentTickFunction* ThisTickFunction) override;

	/** The mesh has a physics asset with bodies for both bones (the condition of ALS's IsRagdollingAllowedToStart). */
	UFUNCTION(BlueprintPure, Category = "DAL|Ragdoll")
	bool IsSupported() const;

	UFUNCTION(BlueprintPure, Category = "DAL|Ragdoll")
	bool IsRagdolling() const { return bRagdolling; }

	/** Detach the mesh from the capsule, turn the capsule's collision off and the mesh's physics simulation on.  With bLimitSpeed the bodies are
	 *  capped to SpeedLimit for the first SpeedLimitFrames frames (the ragdoll can get a much higher initial speed than the character had). */
	UFUNCTION(BlueprintCallable, Category = "DAL|Ragdoll")
	void Begin(bool bLimitSpeed, float SpeedLimit);

	/** The reverse: simulation off, capsule collision on, the mesh attached to the capsule again at its default relative transform.  The actor's
	 *  location and rotation are the logic's business (set_actor_location / set_actor_rotation before this command). */
	UFUNCTION(BlueprintCallable, Category = "DAL|Ragdoll")
	void End();

	/** Caps the linear speed of every body to Speed now. */
	UFUNCTION(BlueprintCallable, Category = "DAL|Ragdoll")
	void LimitSpeed(float Speed);

	/** Angular drive strength of all joint motors (the ragdoll follows its animation pose with this stiffness; ALS scales it by the speed). */
	UFUNCTION(BlueprintCallable, Category = "DAL|Ragdoll")
	void SetMotorStrength(float Strength);

	/** Adds a force (as an acceleration change) to the pelvis body, or the spine_03 body when bSpine. */
	UFUNCTION(BlueprintCallable, Category = "DAL|Ragdoll")
	void AddForce(const FVector& Force, bool bSpine);

private:
	ACharacter* GetCharacter() const;
	USkeletalMeshComponent* GetMesh() const;

	bool bRagdolling = false;
	float CurrentSpeedLimit = 0.0f;
	int32 SpeedLimitFramesRemaining = 0;
};
