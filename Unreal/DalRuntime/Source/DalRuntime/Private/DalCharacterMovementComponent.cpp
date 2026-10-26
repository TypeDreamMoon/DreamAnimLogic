// See DalCharacterMovementComponent.h.  The ALS-Refactored parts ported here (Sixze, MIT): the constructor defaults, ConsumeInputVector,
// the movement mode lock, GetMaxAcceleration, the pre-penetration velocity, PhysCustom, the improved penetration adjustment and the
// floor line trace fix (the last three as wrappers around the engine's functions instead of copies of them).

#include "DalCharacterMovementComponent.h"

#include "Components/CapsuleComponent.h"
#include "DalHost.h"
#include "DalRootMotionSource_Override.h"
#include "GameFramework/Character.h"
#include "GameFramework/Controller.h"
#include "PhysicsEngine/BodyInstance.h"

#include UE_INLINE_GENERATED_CPP_BY_NAME(DalCharacterMovementComponent)

namespace
{
	/** A FHitResult as the generated code sees a hit (the same fields its scene queries fill, see DalHost::FHit). */
	DalHost::FHit ToDalHit(const FHitResult& Hit)
	{
		DalHost::FHit Result;
		Result.bBlocking = Hit.bBlockingHit;
		Result.Point = FVector3f(Hit.ImpactPoint);
		Result.Normal = FVector3f(Hit.ImpactNormal);
		Result.Distance = Hit.Distance;
		Result.Time = Hit.Time;
		Result.Location = FVector3f(Hit.Location);
		Result.bStartPenetrating = Hit.bStartPenetrating;
		Result.Primitive = Hit.GetComponent();
		Result.FaceNormal = FVector3f(Hit.Normal);
		Result.PointD = Hit.ImpactPoint;                         // (the engine's own precision: hit.point_d / hit.location_d)
		Result.LocationD = Hit.Location;
		return Result;
	}

	void PostHook(const AActor* Owner, const DalHost::EEventKind Kind, const FHitResult* Hit = nullptr, const int32 ModeA = 0, const int32 ModeB = 0,
	              const float Value = 0.0f)
	{
		if (Owner == nullptr)
		{
			return;
		}
		DalHost::FEvent Event;
		Event.Kind = Kind;
		Event.ModeA = ModeA;
		Event.ModeB = ModeB;
		Event.Value.X = Value;
		if (Hit != nullptr)
		{
			Event.Hit = ToDalHit(*Hit);
		}
		DalHost::FEventRouter::Post(Owner, Event);
	}
}

UDalCharacterMovementComponent::UDalCharacterMovementComponent()
{
	bRunPhysicsWithNoController = true;
	bAllowPhysicsRotationDuringAnimRootMotion = true;       // required to rotate the actor by hand while a root motion montage plays

	SetCrouchedHalfHeight(56.0f);

	// Default values for standing walking movement (ALS-CMC.cpp:124-182).  The generated logic overwrites most of them every frame.
	MinAnalogWalkSpeed = 25.0f;
	MaxWalkSpeed = 375.0f;
	MaxWalkSpeedCrouched = 150.0f;
	MaxAccelerationWalking = 2000.0f;
	MaxAccelerationWalkingApplied = 2000.0f;
	BrakingDecelerationWalking = 1500.0f;
	GroundFriction = 4.0f;

	AirControl = 0.15f;

	// Only used when the actor is in the air: on the ground MaxAccelerationWalking is used.
	MaxAcceleration = 2000.0f;

	// Makes GroundFriction and FallingLateralFriction used for both acceleration and deceleration.
	bUseSeparateBrakingFriction = false;

	// Friction does not affect deceleration by default; greater than zero only for a short period of time after landing.
	BrakingFrictionFactor = 0.0f;

	bCanWalkOffLedgesWhenCrouching = true;

	PerchRadiusThreshold = 15.0f;
	PerchAdditionalHeight = 0.0f;

	JumpOffJumpZFactor = 0.0f;                              // slide down instead of bouncing on a surface the actor cannot stand on

	bIgnoreBaseRotation = true;

	// These values keep the movement component from affecting the actor's rotation: the generated logic rotates the actor.
	RotationRate = FRotator::ZeroRotator;
	bUseControllerDesiredRotation = false;
	bOrientRotationToMovement = false;

	NavAgentProps.bCanCrouch = true;
	NavMovementProperties.bUseAccelerationForPaths = true;
}

void UDalCharacterMovementComponent::BeginPlay()
{
	Super::BeginPlay();

	if (CharacterOwner != nullptr)
	{
		CharacterOwner->LandedDelegate.AddUniqueDynamic(this, &UDalCharacterMovementComponent::OnCharacterLanded);
	}
	if (UpdatedComponent != nullptr)
	{
		BoundTransformComponent = UpdatedComponent;
		TransformUpdatedHandle = UpdatedComponent->TransformUpdated.AddUObject(this, &UDalCharacterMovementComponent::OnUpdatedComponentTransformUpdated);
	}
}

void UDalCharacterMovementComponent::EndPlay(const EEndPlayReason::Type EndPlayReason)
{
	if (CharacterOwner != nullptr)
	{
		CharacterOwner->LandedDelegate.RemoveDynamic(this, &UDalCharacterMovementComponent::OnCharacterLanded);
	}
	if (USceneComponent* Bound = BoundTransformComponent.Get())
	{
		Bound->TransformUpdated.Remove(TransformUpdatedHandle);
	}
	TransformUpdatedHandle.Reset();
	BoundTransformComponent.Reset();

	Super::EndPlay(EndPlayReason);
}

namespace
{
	/** The angular velocity of the movement base (the physics body of the component it stands on), in radians per second.  False without a base that
	 *  moves (ALS: UAlsUtility::TryGetMovementBaseAngularVelocity). */
	bool TryGetMovementBaseAngularVelocity(const ACharacter& Character, FVector& OutAngularVelocity)
	{
		OutAngularVelocity = FVector::ZeroVector;

		const UPrimitiveComponent* Base = Cast<UPrimitiveComponent>(Character.GetMovementBaseObject());
		if (Base == nullptr || !MovementBaseUtility::UseRelativeLocation(Character.GetMovementBaseInterfaceData()))
		{
			return false;
		}

		const FBodyInstance* Body = Base->GetBodyInstance(Character.GetBasedMovement().BoneName);
		if (Body == nullptr)
		{
			return false;
		}

		OutAngularVelocity = Body->GetUnrealWorldAngularVelocityInRadians();
		return true;
	}
}

FVector UDalCharacterMovementComponent::ConsumeInputVector()
{
	FVector InputVector = Super::ConsumeInputVector();            // consumed either way: a blocked input is dropped, not kept for later
	if (bInputBlocked)
	{
		return FVector::ZeroVector;
	}

	FVector AngularVelocity;
	if (!bIgnoreBaseRotation && CharacterOwner != nullptr && TryGetMovementBaseAngularVelocity(*CharacterOwner, AngularVelocity))
	{
		// ALS-CMC.cpp:213-217: offset the input to keep it in the movement base space (only a character that follows the rotation of its base).
		InputVector = FQuat::MakeFromRotationVector(AngularVelocity * GetWorld()->GetDeltaSeconds()).RotateVector(InputVector);
	}

	return InputVector;
}

void UDalCharacterMovementComponent::UpdateBasedRotation(FRotator& FinalRotation, const FRotator& ReducedRotation)
{
	// ALS-CMC.cpp:244-270: the engine's version has no effect when the rotation changes of the base are ignored in FaceRotation (the generated logic
	// rotates the actor itself), so the control rotation is what follows the base (UpdateBasedMovement only calls this when bIgnoreBaseRotation is off).
	if (CharacterOwner == nullptr || CharacterOwner->GetController() == nullptr)
	{
		return;
	}

	const FBasedMovementInfo& BasedMovement = CharacterOwner->GetBasedMovement();

	FVector MovementBaseLocation;
	FQuat MovementBaseRotation;
	MovementBaseUtility::GetMovementBaseTransform(&BasedMovement.MovementBaseInterfaceData, BasedMovement.BoneName, MovementBaseLocation, MovementBaseRotation);

	if (!OldBaseQuat.Equals(MovementBaseRotation, UE_SMALL_NUMBER))
	{
		const FRotator DeltaRotation = (MovementBaseRotation * OldBaseQuat.Inverse()).Rotator();
		FRotator NewControlRotation = CharacterOwner->GetController()->GetControlRotation();

		NewControlRotation.Pitch += DeltaRotation.Pitch;
		NewControlRotation.Yaw += DeltaRotation.Yaw;
		NewControlRotation.Normalize();

		CharacterOwner->GetController()->SetControlRotation(NewControlRotation);
	}
}

void UDalCharacterMovementComponent::SetMovementMode(const EMovementMode NewMovementMode, const uint8 NewCustomMode)
{
	if (!bMovementModeLocked)
	{
		Super::SetMovementMode(NewMovementMode, NewCustomMode);
	}
}

void UDalCharacterMovementComponent::OnMovementModeChanged(const EMovementMode PreviousMovementMode, const uint8 PreviousCustomMode)
{
	Super::OnMovementModeChanged(PreviousMovementMode, PreviousCustomMode);

	if (bCrouchMaintainsBaseLocationAlways)
	{
		// Removes the very noticeable changes in the mesh location when the character uncrouches at the end of a roll in the air.
		bCrouchMaintainsBaseLocation = true;
	}

	PostHook(CharacterOwner, DalHost::EEventKind::MovementModeChanged, nullptr, static_cast<int32>(PreviousMovementMode), static_cast<int32>(MovementMode.GetValue()));
}

float UDalCharacterMovementComponent::GetMaxAcceleration() const
{
	if (IsMovingOnGround())
	{
		return MaxAccelerationWalkingApplied;
	}

	return Super::GetMaxAcceleration();
}

bool UDalCharacterMovementComponent::DoJump(const bool bReplayingMoves, const float DeltaTime)
{
	const bool bJumped = Super::DoJump(bReplayingMoves, DeltaTime);
	if (bJumped)
	{
		PostHook(CharacterOwner, DalHost::EEventKind::Jumped);
	}
	return bJumped;
}

void UDalCharacterMovementComponent::Crouch(const bool bClientSimulation)
{
	const bool bWasCrouched = CharacterOwner != nullptr && CharacterOwner->bIsCrouched;
	Super::Crouch(bClientSimulation);
	if (!bWasCrouched && CharacterOwner != nullptr && CharacterOwner->bIsCrouched)
	{
		PostHook(CharacterOwner, DalHost::EEventKind::CrouchStarted);
	}
}

void UDalCharacterMovementComponent::UnCrouch(const bool bClientSimulation)
{
	const bool bWasCrouched = CharacterOwner != nullptr && CharacterOwner->bIsCrouched;
	Super::UnCrouch(bClientSimulation);
	if (bWasCrouched && CharacterOwner != nullptr && !CharacterOwner->bIsCrouched)
	{
		PostHook(CharacterOwner, DalHost::EEventKind::CrouchEnded);
	}
}

void UDalCharacterMovementComponent::OnCharacterLanded(const FHitResult& Hit)
{
	PostHook(CharacterOwner, DalHost::EEventKind::Landed, &Hit);
}

void UDalCharacterMovementComponent::OnUpdatedComponentTransformUpdated(USceneComponent* Updated, EUpdateTransformFlags UpdateTransformFlags, const ETeleportType Teleport)
{
	if (Teleport != ETeleportType::None)
	{
		PostHook(CharacterOwner, DalHost::EEventKind::Teleported);
	}
}

void UDalCharacterMovementComponent::PhysicsRotation(const float DeltaTime)
{
	Super::PhysicsRotation(DeltaTime);

	if (HasValidData() && (bRunPhysicsWithNoController || IsValid(CharacterOwner->GetController())))
	{
		OnPhysicsRotation.Broadcast(DeltaTime);
		PostHook(CharacterOwner, DalHost::EEventKind::PhysicsRotation, nullptr, 0, 0, DeltaTime);
	}
}

void UDalCharacterMovementComponent::CalcVelocity(const float DeltaTime, const float Friction, const bool bFluid, const float BrakingDeceleration)
{
	FVector AngularVelocity;
	if (!bIgnoreBaseRotation && CharacterOwner != nullptr && TryGetMovementBaseAngularVelocity(*CharacterOwner, AngularVelocity))
	{
		// ALS-CMC.cpp:283-287: offset the velocity to keep it in the movement base space (only a character that follows the rotation of its base).
		Velocity = FQuat::MakeFromRotationVector(AngularVelocity * DeltaTime).RotateVector(Velocity);
	}

	Super::CalcVelocity(DeltaTime, Friction, bFluid, BrakingDeceleration);

	if (bInPhysWalking)
	{
		// This is the velocity the walking move is about to try (PhysWalking's MoveVelocity): the last sub step wins.
		WalkingMoveVelocity = Velocity;
		bWalkingMoveVelocityRecorded = true;
	}
}

void UDalCharacterMovementComponent::PhysWalking(const float DeltaTime, const int32 IterationsCount)
{
	// ALS-CMC.cpp:333-335: PhysWalking starts with RefreshGroundedMovementSettings.  The logic's `update(dt) in physics_walking` block is that, so it
	// sees the state of this very step: a stance that OnStartCrouch changed a moment ago, the mode the character has just landed in, the velocity
	// the walking move is about to start with.  The events the movement component raised so far in this step have been handled by then (sync_events: when it raised them; slot_events: first thing in the block).
	static const FName PhysicsWalkingHook(TEXT("physics_walking"));
	DalHost::FEventRouter::RunHook(CharacterOwner, PhysicsWalkingHook, DeltaTime);

	bWalkingMoveVelocityRecorded = false;
	bInPhysWalking = true;
	MaxAccelerationWalkingApplied = MaxAccelerationWalking;       // ALS-CMC.cpp:335: the settings refresh at the start of PhysWalking
	Super::PhysWalking(DeltaTime, IterationsCount);
	bInPhysWalking = false;

	if (!HasValidData() || !IsMovingOnGround())
	{
		return;
	}

	// ALS-CMC.cpp:568-575: remember what the move wanted before the engine replaced the velocity by the distance actually covered.
	if (bWalkingMoveVelocityRecorded && !bJustTeleported && !HasAnimRootMotion() && !CurrentRootMotion.HasOverrideVelocity())
	{
		PrePenetrationAdjustmentVelocity = WalkingMoveVelocity;
		bPrePenetrationAdjustmentVelocityValid = true;
	}

	// ALS-CMC.cpp:531-541: the engine pops the capsule out of a floor it started in penetration with only when that floor is not walkable.
	if (bAllowImprovedPenetrationAdjustment && CurrentFloor.IsWalkableFloor() && CurrentFloor.HitResult.bStartPenetrating)
	{
		FHitResult Hit(CurrentFloor.HitResult);
		Hit.TraceEnd = Hit.TraceStart + MAX_FLOOR_DIST * -GetGravityDirection();
		const FVector RequestedAdjustment = GetPenetrationAdjustment(Hit);
		ResolvePenetration(RequestedAdjustment, Hit, UpdatedComponent->GetComponentQuat());
		bForceNextFloorCheck = true;
	}
}

void UDalCharacterMovementComponent::PhysCustom(const float DeltaTime, int32 IterationsCount)
{
	// ALS-CMC.cpp:606-629.  The custom mode moves the actor by its root motion (animation or override) alone: no gravity, no floor, no
	// collision sweep (the capsule goes through walls), the velocity is whatever the root motion says.
	if (DeltaTime < MIN_TICK_TIME)
	{
		Super::PhysCustom(DeltaTime, IterationsCount);
		return;
	}

	IterationsCount += 1;
	bJustTeleported = false;

	RestorePreAdditiveRootMotionVelocity();

	if (!HasAnimRootMotion() && !CurrentRootMotion.HasOverrideVelocity())
	{
		Velocity = FVector::ZeroVector;
	}

	ApplyRootMotionToVelocity(DeltaTime);

	MoveUpdatedComponent(Velocity * DeltaTime, UpdatedComponent->GetComponentQuat(), false);

	Super::PhysCustom(DeltaTime, IterationsCount);
}

void UDalCharacterMovementComponent::ComputeFloorDist(const FVector& CapsuleLocation, const float LineDistance, const float SweepDistance,
                                                      FFindFloorResult& OutFloorResult, const float SweepRadius, const FHitResult* DownwardSweepResult) const
{
	Super::ComputeFloorDist(CapsuleLocation, LineDistance, SweepDistance, OutFloorResult, SweepRadius, DownwardSweepResult);

	// ALS-CMC.cpp:775-792.  When the line trace replaced the sweep's hit, keep what the sweep found about penetration: PhysWalking needs it
	// to see that the floor check started in penetration.  The sweep alone is asked again (no line distance, no line trace).
	if (bKeepSweepPenetrationOnFloorLineTrace && OutFloorResult.bLineTrace && LineDistance > 0.0f)
	{
		FFindFloorResult SweepOnly;
		Super::ComputeFloorDist(CapsuleLocation, 0.0f, SweepDistance, SweepOnly, SweepRadius, DownwardSweepResult);

		OutFloorResult.HitResult.Normal = SweepOnly.HitResult.Normal;
		OutFloorResult.HitResult.PenetrationDepth = SweepOnly.HitResult.PenetrationDepth;
		OutFloorResult.HitResult.bStartPenetrating = SweepOnly.HitResult.bStartPenetrating;
		OutFloorResult.HitResult.HitObjectHandle = SweepOnly.HitResult.HitObjectHandle;
	}
}

void UDalCharacterMovementComponent::SetMovementModeLocked(const bool bNewMovementModeLocked)
{
	bMovementModeLocked = bNewMovementModeLocked;
}

void UDalCharacterMovementComponent::SetInputBlocked(const bool bNewInputBlocked)
{
	bInputBlocked = bNewInputBlocked;
}

bool UDalCharacterMovementComponent::TryConsumePrePenetrationAdjustmentVelocity(FVector& OutVelocity)
{
	if (!bPrePenetrationAdjustmentVelocityValid)
	{
		OutVelocity = FVector::ZeroVector;
		return false;
	}

	OutVelocity = PrePenetrationAdjustmentVelocity;

	PrePenetrationAdjustmentVelocity = FVector::ZeroVector;
	bPrePenetrationAdjustmentVelocityValid = false;

	return true;
}

void UDalCharacterMovementComponent::SetRootMotionOverride(const FVector& NewVelocity, const FQuat& NewRotationDelta)
{
	// The engine drops sources by itself (a movement mode change to None clears them all): look the source up before reusing it.
	if (OverrideSource.IsValid() && OverrideSourceId != 0 && GetRootMotionSourceByID(OverrideSourceId).IsValid())
	{
		OverrideSource->Velocity = NewVelocity;
		OverrideSource->RotationDelta = NewRotationDelta;
		return;
	}

	OverrideSource = MakeShared<FDalRootMotionSource_Override>();
	OverrideSource->InstanceName = TEXT("DalRootMotionOverride");
	OverrideSource->Velocity = NewVelocity;
	OverrideSource->RotationDelta = NewRotationDelta;
	OverrideSourceId = ApplyRootMotionSource(OverrideSource);
}

void UDalCharacterMovementComponent::ClearRootMotionOverride()
{
	if (OverrideSourceId != 0)
	{
		RemoveRootMotionSourceByID(OverrideSourceId);
	}
	OverrideSource.Reset();
	OverrideSourceId = 0;
}
