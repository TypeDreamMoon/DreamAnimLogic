#include "DalTestCharacter.h"

#include "Animation/AnimInstance.h"
#include "Components/CapsuleComponent.h"
#include "Components/SkeletalMeshComponent.h"
#include "Engine/SkeletalMesh.h"
#include "UObject/ConstructorHelpers.h"

ADalTestCharacter::ADalTestCharacter()
{
	static ConstructorHelpers::FObjectFinder<USkeletalMesh> MeshFinder(TEXT("/Game/Characters/Mannequins/Meshes/SKM_Manny_Simple.SKM_Manny_Simple"));
	static ConstructorHelpers::FClassFinder<UAnimInstance> AnimFinder(TEXT("/Game/Characters/Mannequins/Anims/Unarmed/ABP_Unarmed"));

	USkeletalMeshComponent* MeshComponent = GetMesh();
	MeshComponent->SetRelativeLocation(FVector(0.0, 0.0, -96.0));
	MeshComponent->SetRelativeRotation(FRotator(0.0, -90.0, 0.0));
	if (MeshFinder.Succeeded())
	{
		MeshComponent->SetSkeletalMesh(MeshFinder.Object);
	}
	if (AnimFinder.Succeeded())
	{
		MeshComponent->SetAnimationMode(EAnimationMode::AnimationBlueprint);
		MeshComponent->SetAnimInstanceClass(AnimFinder.Class);
	}
	// Anim updates must not depend on rendering: the harness runs without a renderer.
	MeshComponent->VisibilityBasedAnimTickOption = EVisibilityBasedAnimTickOption::AlwaysTickPoseAndRefreshBones;
	MeshComponent->bEnableUpdateRateOptimizations = false;
}

void ADalTestCharacter::SetupPlayerInputComponent(UInputComponent* PlayerInputComponent)
{
}
