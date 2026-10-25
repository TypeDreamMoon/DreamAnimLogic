// UDalCharacterMovementComponent: the character movement component of the generated DAL code.
//
// It is the native part of the ALS-Refactored movement component (Sixze, MIT: AlsCharacterMovementComponent.h / .cpp) that the .dal
// rewrite cannot hold, and nothing else: the gait / stance bookkeeping, the movement settings push and the networking of the original are
// not here (the .dal logic does the first two, a single player character needs no third).  What is here:
//
//   * the extension points the host protocol names (dal/hostapi.py): MaxAccelerationWalking, bMovementModeLocked, bInputBlocked, the
//     consume-on-read pre-penetration velocity, the root motion override (SetRootMotionOverride / ClearRootMotionOverride), the custom
//     movement mode that moves the actor by its root motion only;
//   * the hook events of the character posted to DalHost::FEventRouter (movement mode changed, jumped, landed, crouch started / ended,
//     teleported, physics rotation) for the logics of the actor to handle;
//   * the three changes ALS makes to the walking physics (improved penetration adjustment, the floor line trace keeping the sweep's
//     penetration, the velocity before the penetration adjustment).  The original copies two engine functions to do it; here they are
//     overrides around the engine's own, built from its public / protected API (no engine code is copied).
//
// Semantics follow anim-3c-design.md section 11 (the Blender twin of this class is c3/movement.py).

#pragma once

#include "CoreMinimal.h"
#include "GameFramework/CharacterMovementComponent.h"
#include "DalCharacterMovementComponent.generated.h"

struct FDalRootMotionSource_Override;

/** Broadcast by PhysicsRotation after the engine's rotation update (the ALS OnPhysicsRotation). */
using FDalPhysicsRotationDelegate = TMulticastDelegate<void(float DeltaTime)>;

UCLASS(ClassGroup = "DAL", Meta = (BlueprintSpawnableComponent))
class DALRUNTIME_API UDalCharacterMovementComponent : public UCharacterMovementComponent
{
	GENERATED_BODY()

public:
	// ---- settings (ALS-CMC.h:80 and the three switches of anim-3c-design.md 11.2) -------------------------------------------------------

	/** While this improves the response to interactions with moving kinematic physical bodies, it may cause some issues when interacting
	 *  with simulated bodies.  When the floor under the capsule is walkable but the floor check started in penetration, the penetration is
	 *  resolved anyway (the engine does it only when the floor is not walkable). */
	UPROPERTY(EditAnywhere, BlueprintReadWrite, Category = "DAL|Settings")
	uint8 bAllowImprovedPenetrationAdjustment : 1 {true};

	/** When the floor was found by the line trace fallback, keep the sweep's normal, penetration depth, start-penetrating flag and hit object
	 *  handle in the floor hit (ALS-CMC.cpp:775-792).  Only meaningful with bAllowImprovedPenetrationAdjustment. */
	UPROPERTY(EditAnywhere, BlueprintReadWrite, Category = "DAL|Settings")
	uint8 bKeepSweepPenetrationOnFloorLineTrace : 1 {true};

	/** After every movement mode change, keep the base location when crouching in any mode (ALS-CMC.cpp:230-238): in the air the feet stay
	 *  where they are instead of the capsule center. */
	UPROPERTY(EditAnywhere, BlueprintReadWrite, Category = "DAL|Settings")
	uint8 bCrouchMaintainsBaseLocationAlways : 1 {true};

	// ---- state: written by the generated logic (host commands) and by the physics --------------------------------------------------------

	/** The acceleration on the ground the logic pushed last (host command set_max_acceleration_walking, every frame).  It becomes the one
	 *  GetMaxAcceleration returns on the ground (MaxAccelerationWalkingApplied) when the next PhysWalking starts: ALS refreshes its movement
	 *  settings there (ALS-CMC.cpp:335), after the input was scaled by the acceleration of the frame before, and the grounded logic
	 *  reads the value of the current step.  Applying a push at once would make the character accelerate with the new value one step early. */
	UPROPERTY(VisibleAnywhere, BlueprintReadWrite, Category = "DAL|State", Transient, Meta = (ClampMin = 0, ForceUnits = "cm/s^2"))
	float MaxAccelerationWalking = 0.0f;

	/** What GetMaxAcceleration returns on the ground (ALS-CMC.cpp:292-300): MaxAccelerationWalking as of the last PhysWalking start. */
	UPROPERTY(VisibleAnywhere, BlueprintReadOnly, Category = "DAL|State", Transient, Meta = (ClampMin = 0, ForceUnits = "cm/s^2"))
	float MaxAccelerationWalkingApplied = 0.0f;

	/** SetMovementMode does nothing while this is set, also when the component calls it itself (jump, launch, landing). */
	UPROPERTY(VisibleAnywhere, BlueprintReadWrite, Category = "DAL|State", Transient)
	uint8 bMovementModeLocked : 1 {false};

	/** Temporarily prevents the player from moving the character: the movement input of every frame is consumed and dropped. */
	UPROPERTY(VisibleAnywhere, BlueprintReadWrite, Category = "DAL|State", Transient)
	uint8 bInputBlocked : 1 {false};

	/** The velocity the walking move wanted before the penetration adjustment changed it (ALS-CMC.cpp:568-575), see
	 *  TryConsumePrePenetrationAdjustmentVelocity. */
	UPROPERTY(VisibleAnywhere, BlueprintReadOnly, Category = "DAL|State", Transient)
	FVector PrePenetrationAdjustmentVelocity{ForceInit};

	UPROPERTY(VisibleAnywhere, BlueprintReadOnly, Category = "DAL|State", Transient)
	uint8 bPrePenetrationAdjustmentVelocityValid : 1 {false};

	/** Broadcast after the engine's PhysicsRotation (when the engine would run it for this character). */
	FDalPhysicsRotationDelegate OnPhysicsRotation;

public:
	UDalCharacterMovementComponent();

	virtual void BeginPlay() override;
	virtual void EndPlay(const EEndPlayReason::Type EndPlayReason) override;

	virtual FVector ConsumeInputVector() override;
	virtual void SetMovementMode(EMovementMode NewMovementMode, uint8 NewCustomMode = 0) override;
	virtual void OnMovementModeChanged(EMovementMode PreviousMovementMode, uint8 PreviousCustomMode) override;
	virtual float GetMaxAcceleration() const override;
	virtual bool DoJump(bool bReplayingMoves, float DeltaTime) override;
	virtual void Crouch(bool bClientSimulation = false) override;
	virtual void UnCrouch(bool bClientSimulation = false) override;
	virtual void CalcVelocity(float DeltaTime, float Friction, bool bFluid, float BrakingDeceleration) override;
	virtual void PhysicsRotation(float DeltaTime) override;

	virtual void ComputeFloorDist(const FVector& CapsuleLocation, float LineDistance, float SweepDistance, FFindFloorResult& OutFloorResult,
	                              float SweepRadius, const FHitResult* DownwardSweepResult) const override;

protected:
	virtual void PhysWalking(float DeltaTime, int32 IterationsCount) override;
	virtual void PhysCustom(float DeltaTime, int32 IterationsCount) override;

public:
	UFUNCTION(BlueprintCallable, Category = "DAL|Character Movement")
	void SetMovementModeLocked(bool bNewMovementModeLocked);

	UFUNCTION(BlueprintCallable, Category = "DAL|Character Movement")
	void SetInputBlocked(bool bNewInputBlocked);

	/** The pre-penetration velocity, once: reading it clears it (ALS-CMC.cpp:1032-1046).  False when nothing was recorded since the last read. */
	bool TryConsumePrePenetrationAdjustmentVelocity(FVector& OutVelocity);

	/** Sets the velocity (cm/s) and the rotation delta of the root motion override.  The first call adds the source (a FDalRootMotionSource_Override
	 *  of priority 1000 and no duration), later calls change its values: the generated logic calls it every frame while it drives the character.
	 *  It lasts until ClearRootMotionOverride.  RotationDelta is applied to the actor rotation every update (rotation * current rotation). */
	UFUNCTION(BlueprintCallable, Category = "DAL|Character Movement")
	void SetRootMotionOverride(const FVector& NewVelocity, const FQuat& NewRotationDelta);

	/** Removes the root motion override source. */
	UFUNCTION(BlueprintCallable, Category = "DAL|Character Movement")
	void ClearRootMotionOverride();

	UFUNCTION(BlueprintPure, Category = "DAL|Character Movement")
	bool HasRootMotionOverride() const { return OverrideSource.IsValid(); }

private:
	/** ACharacter::LandedDelegate. */
	UFUNCTION()
	void OnCharacterLanded(const FHitResult& Hit);

	void OnUpdatedComponentTransformUpdated(USceneComponent* Updated, EUpdateTransformFlags UpdateTransformFlags, ETeleportType Teleport);

	/** The velocity CalcVelocity left behind during this PhysWalking call (the last one wins), see PhysWalking. */
	FVector WalkingMoveVelocity{ForceInit};
	bool bWalkingMoveVelocityRecorded = false;
	bool bInPhysWalking = false;

	TSharedPtr<FDalRootMotionSource_Override> OverrideSource;
	uint16 OverrideSourceId = 0;

	FDelegateHandle TransformUpdatedHandle;
	TWeakObjectPtr<USceneComponent> BoundTransformComponent;
};
