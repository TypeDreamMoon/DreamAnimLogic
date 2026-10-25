#include "DalRagdollService.h"

#include "Animation/AnimInstance.h"
#include "Components/CapsuleComponent.h"
#include "Components/SkeletalMeshComponent.h"
#include "GameFramework/Character.h"
#include "PhysicsEngine/BodyInstance.h"
#include "PhysicsEngine/PhysicsAsset.h"

#include UE_INLINE_GENERATED_CPP_BY_NAME(DalRagdollService)

UDalRagdollService::UDalRagdollService()
{
	PrimaryComponentTick.bCanEverTick = true;
	PrimaryComponentTick.TickGroup = TG_PrePhysics;
	PrimaryComponentTick.bStartWithTickEnabled = false;
}

ACharacter* UDalRagdollService::GetCharacter() const
{
	return Cast<ACharacter>(GetOwner());
}

USkeletalMeshComponent* UDalRagdollService::GetMesh() const
{
	const ACharacter* Character = GetCharacter();
	return Character != nullptr ? Character->GetMesh() : nullptr;
}

bool UDalRagdollService::IsSupported() const
{
	const USkeletalMeshComponent* Mesh = GetMesh();
	const UPhysicsAsset* PhysicsAsset = Mesh != nullptr ? Mesh->GetPhysicsAsset() : nullptr;
	return PhysicsAsset != nullptr && PhysicsAsset->FindBodyIndex(PelvisBoneName) != INDEX_NONE && PhysicsAsset->FindBodyIndex(Spine03BoneName) != INDEX_NONE;
}

void UDalRagdollService::Begin(const bool bLimitSpeed, const float SpeedLimit)
{
	ACharacter* Character = GetCharacter();
	USkeletalMeshComponent* Mesh = GetMesh();
	if (bRagdolling || Character == nullptr || Mesh == nullptr || !IsSupported())
	{
		return;
	}

	Mesh->bUpdateJointsFromAnimation = true;               // required for the flail animation to work properly

	if (!Mesh->IsRunningParallelEvaluation() && !Mesh->GetBoneSpaceTransforms().IsEmpty())
	{
		Mesh->UpdateRBJointMotors();
	}

	// Stop any active montages.
	if (UAnimInstance* Anim = Mesh->GetAnimInstance())
	{
		Anim->Montage_Stop(0.2f);
	}

	// Detach the mesh so that character transformation changes will not affect it in any way.
	Mesh->DetachFromComponent(FDetachmentTransformRules::KeepWorldTransform);

	// Disable the capsule's collision and enable the mesh's physics simulation.
	Character->GetCapsuleComponent()->SetCollisionEnabled(ECollisionEnabled::NoCollision);

	Mesh->SetCollisionObjectType(ECC_PhysicsBody);
	Mesh->SetCollisionEnabled(ECollisionEnabled::QueryAndPhysics);
	Mesh->SetSimulatePhysics(true);

	// Required for the ragdoll to behave properly when a body instance is set to simulated in a physics asset.
	Mesh->ResetAllBodiesSimulatePhysics();

	bRagdolling = true;
	SetComponentTickEnabled(true);

	if (bLimitSpeed)
	{
		// The ragdoll can get a much higher initial speed than the character's last speed: cap it for a few frames.
		CurrentSpeedLimit = SpeedLimit;
		SpeedLimitFramesRemaining = SpeedLimitFrames;
		LimitSpeed(CurrentSpeedLimit);
	}
	else
	{
		SpeedLimitFramesRemaining = 0;
	}
}

void UDalRagdollService::End()
{
	ACharacter* Character = GetCharacter();
	USkeletalMeshComponent* Mesh = GetMesh();
	if (!bRagdolling || Character == nullptr || Mesh == nullptr)
	{
		return;
	}

	bRagdolling = false;
	SpeedLimitFramesRemaining = 0;
	SetComponentTickEnabled(false);

	// Where the detached mesh is while it still simulates: the pose the ragdoll ends in is in the component space of this transform.
	const FTransform DetachedMeshTransform = Mesh->GetComponentTransform();

	// Disable the mesh's physics simulation and enable the capsule's collision.
	Mesh->bUpdateJointsFromAnimation = false;

	Mesh->SetSimulatePhysics(false);
	Mesh->SetCollisionEnabled(ECollisionEnabled::QueryOnly);
	Mesh->SetCollisionObjectType(ECC_Pawn);

	Character->GetCapsuleComponent()->SetCollisionEnabled(ECollisionEnabled::QueryAndPhysics);

	// Attach the mesh back to the capsule at its default relative transform (what the character class remembered at BeginPlay).  The logic has moved
	// the actor before this command, as AAlsCharacter does (AlsCharacter_Actions.cpp:1100-1126): the mesh goes where the capsule is now.
	const FTransform ActorTransform = Character->GetActorTransform();
	Mesh->SetWorldLocationAndRotationNoPhysics(ActorTransform.TransformPositionNoScale(Character->GetBaseTranslationOffset()),
	                                           ActorTransform.TransformRotation(Character->GetBaseRotationOffset()).Rotator());
	Mesh->AttachToComponent(Character->GetCapsuleComponent(), FAttachmentTransformRules::KeepWorldTransform);

	// The pose of the ragdoll is the one the get up blends from (the logic takes a snapshot of it in the next animation update).  It is in the component
	// space of the detached mesh, which was wherever the ragdoll ended up; the mesh has moved to the capsule since.  ALS puts the pelvis of the final
	// pose back where it was in the world, relative to the mesh now (AlsCharacter_Actions.cpp:1128-1138: PelvisTransform.GetRelativeTransform(mesh)).
	// The snapshot keeps every other bone relative to its parent, so moving all the bones but the root from the old component space to the new one
	// gives the same pose: the pelvis is where ALS puts it and nothing else changes.
	const FTransform Shift = DetachedMeshTransform * Mesh->GetComponentTransform().Inverse();
	TArray<FTransform>& ComponentSpace = const_cast<TArray<FTransform>&>(Mesh->GetComponentSpaceTransforms());
	for (int32 BoneIndex = 1; BoneIndex < ComponentSpace.Num(); ++BoneIndex)
	{
		ComponentSpace[BoneIndex] = ComponentSpace[BoneIndex] * Shift;
	}

	if (Mesh->ShouldUseUpdateRateOptimizations())
	{
		// Update rate optimization off for one frame forces the animation blueprint to update and get rid of the incorrect mesh pose.
		Mesh->bEnableUpdateRateOptimizations = false;

		GetWorld()->GetTimerManager().SetTimerForNextTick(FTimerDelegate::CreateWeakLambda(this, [Mesh]
		{
			if (IsValid(Mesh))
			{
				Mesh->bEnableUpdateRateOptimizations = true;
			}
		}));
	}
}

void UDalRagdollService::LimitSpeed(const float Speed)
{
	USkeletalMeshComponent* Mesh = GetMesh();
	if (Mesh == nullptr || !bRagdolling)
	{
		return;
	}

	const float Limit = FMath::Max(0.0f, Speed);
	Mesh->ForEachBodyBelow(NAME_None, true, false, [Limit](FBodyInstance* Body)
	{
		const FVector Velocity = Body->GetUnrealWorldVelocity();
		if (Velocity.SizeSquared() > FMath::Square(Limit))
		{
			Body->SetLinearVelocity(Velocity.GetSafeNormal() * Limit, false);
		}
	});
}

void UDalRagdollService::SetMotorStrength(const float Strength)
{
	if (USkeletalMeshComponent* Mesh = GetMesh())
	{
		Mesh->SetAllMotorsAngularDriveParams(Strength, 0.0f, 0.0f);
	}
}

void UDalRagdollService::AddForce(const FVector& Force, const bool bSpine)
{
	USkeletalMeshComponent* Mesh = GetMesh();
	if (Mesh == nullptr || !bRagdolling)
	{
		return;
	}

	if (FBodyInstance* Body = Mesh->GetBodyInstance(bSpine ? Spine03BoneName : PelvisBoneName))
	{
		Body->AddForce(Force, true, true);
	}
}

FVector UDalRagdollService::GetPelvisLocation() const
{
	const USkeletalMeshComponent* Mesh = GetMesh();
	const FBodyInstance* Body = Mesh != nullptr ? Mesh->GetBodyInstance(PelvisBoneName) : nullptr;
	return Body != nullptr ? Body->GetUnrealWorldTransform().GetLocation() : FVector::ZeroVector;
}

FVector UDalRagdollService::GetPelvisVelocity() const
{
	const USkeletalMeshComponent* Mesh = GetMesh();
	const FBodyInstance* Body = Mesh != nullptr ? Mesh->GetBodyInstance(PelvisBoneName) : nullptr;
	return Body != nullptr ? Body->GetUnrealWorldVelocity() : FVector::ZeroVector;
}

bool UDalRagdollService::IsFacingUp() const
{
	const USkeletalMeshComponent* Mesh = GetMesh();
	if (Mesh == nullptr)
	{
		return false;
	}

	// AlsCharacter_Actions.cpp: bRagdollFacingUpward{FMath::UnwindDegrees(PelvisRotation.Roll) <= 0.0f}, the rotation of the pelvis bone in world space
	const FRotator PelvisRotation = Mesh->GetSocketTransform(PelvisBoneName).Rotator();
	return FMath::UnwindDegrees(PelvisRotation.Roll) <= 0.0f;
}

void UDalRagdollService::TickComponent(const float DeltaTime, const ELevelTick TickType, FActorComponentTickFunction* ThisTickFunction)
{
	Super::TickComponent(DeltaTime, TickType, ThisTickFunction);

	if (bRagdolling && SpeedLimitFramesRemaining > 0)
	{
		SpeedLimitFramesRemaining -= 1;
		LimitSpeed(CurrentSpeedLimit);
	}
}
