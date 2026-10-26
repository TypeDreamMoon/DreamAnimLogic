#include "DalTestMover.h"

#include "Components/StaticMeshComponent.h"
#include "Components/InterpToMovementComponent.h"
#include "GameFramework/RotatingMovementComponent.h"
#include "PhysicsEngine/BodyInstance.h"

#include UE_INLINE_GENERATED_CPP_BY_NAME(DalTestMover)

ADalTestMover::ADalTestMover()
{
	PrimaryActorTick.bCanEverTick = false;

	GetStaticMeshComponent()->SetMobility(EComponentMobility::Movable);

	// Both components exist; BeginPlay keeps the one(s) the script asked for and destroys the others (a component cannot be added to an editor world actor from Python).
	// Neither starts by itself: the script activates the one it wants when the recording starts (a movement component that began moving when play began
	// would be somewhere on its path by then).
	InterpTo = CreateDefaultSubobject<UInterpToMovementComponent>(TEXT("InterpTo"));
	InterpTo->bAutoActivate = false;
	RotatingMovement = CreateDefaultSubobject<URotatingMovementComponent>(TEXT("RotatingMovement"));
	RotatingMovement->bAutoActivate = false;
}

void ADalTestMover::BeginPlay()
{
	if (bMoveByInterpTo && InterpTo != nullptr)
	{
		InterpTo->ResetControlPoints();
		InterpTo->AddControlPointPosition(FVector::ZeroVector, true);
		InterpTo->AddControlPointPosition(InterpOffset, true);
		InterpTo->Duration = InterpSeconds;
		InterpTo->BehaviourType = EInterpToBehaviourType::PingPong;
		InterpTo->bForceSubStepping = false;
		InterpTo->bSweep = false;                              // a platform is moved, whatever is in its way (the character on it, the floor it skims)
		InterpTo->FinaliseControlPoints();
		InterpTo->RestartMovement(1.0f);                       // (ResetControlPoints stopped it)
	}
	else if (InterpTo != nullptr)
	{
		InterpTo->DestroyComponent();
		InterpTo = nullptr;
	}

	if (!RotationRate.IsNearlyZero() && RotatingMovement != nullptr)
	{
		RotatingMovement->RotationRate = RotationRate;
	}
	else if (RotatingMovement != nullptr)
	{
		RotatingMovement->DestroyComponent();
		RotatingMovement = nullptr;
	}

	Super::BeginPlay();
}

FVector ADalTestMover::GetMeshComponentVelocity() const
{
	return GetStaticMeshComponent()->GetComponentVelocity();
}

FVector ADalTestMover::GetBodyVelocity() const
{
	const FBodyInstance* Body = GetStaticMeshComponent()->GetBodyInstance();
	return Body != nullptr ? Body->GetUnrealWorldVelocity() : FVector::ZeroVector;
}
