// ADalTestMover: a platform for the moving-base takes (an elevator, a sliding platform, a turning disc, a box that drifts), spawned at run time by the
// recording script (Unreal/AlsHost/scripts/base_takes.py); nothing of it is ever saved in a level.
//
// It is a static mesh actor whose mesh component is Movable (a character that stands on it follows it: MovementBaseUtility::IsDynamicBase) and
// which carries both movement components a platform can be moved by.  Which of them runs - or none, when the script moves the actor itself with
// SetActorLocation every frame - is a property the script sets before play begins:
//   * bMoveByInterpTo: a UInterpToMovementComponent moves it back and forth by InterpOffset in InterpSeconds per leg.  A movement component
//     reports its velocity (UpdateComponentVelocity), so GetComponentVelocity() of the mesh is the speed it moves at: what ALS's mantling target
//     speed threshold (TargetPrimitiveSpeedThreshold) and the movement base velocity read.
//   * RotationRate: a URotatingMovementComponent turns it (degrees per second per axis).
// An actor moved by SetActorLocation has a component velocity of zero (nothing writes it).  The take that compares the two is the point of the class.

#pragma once

#include "CoreMinimal.h"
#include "Engine/StaticMeshActor.h"
#include "DalTestMover.generated.h"

class UInterpToMovementComponent;
class URotatingMovementComponent;

UCLASS(ClassGroup = "DAL")
class DALRUNTIME_API ADalTestMover : public AStaticMeshActor
{
	GENERATED_BODY()

public:
	ADalTestMover();

	/** Move by a UInterpToMovementComponent (ping pong between the spawn location and spawn location + InterpOffset). */
	UPROPERTY(EditAnywhere, BlueprintReadWrite, Category = "DAL|Test")
	bool bMoveByInterpTo = false;

	/** The far end of the path, relative to where the actor is when play begins (cm). */
	UPROPERTY(EditAnywhere, BlueprintReadWrite, Category = "DAL|Test")
	FVector InterpOffset = FVector::ZeroVector;

	/** Seconds one leg of the path takes. */
	UPROPERTY(EditAnywhere, BlueprintReadWrite, Category = "DAL|Test")
	float InterpSeconds = 6.0f;

	/** Degrees per second about each axis; zero for a mover that does not turn by itself. */
	UPROPERTY(EditAnywhere, BlueprintReadWrite, Category = "DAL|Test")
	FRotator RotationRate = FRotator::ZeroRotator;

	/** The component velocity of the mesh now (what GetComponentVelocity() answers). */
	UFUNCTION(BlueprintPure, Category = "DAL|Test")
	FVector GetMeshComponentVelocity() const;

	/** The velocity of the body of the mesh, if it has one (what MovementBaseUtility::GetMovementBaseVelocity falls back to). */
	UFUNCTION(BlueprintPure, Category = "DAL|Test")
	FVector GetBodyVelocity() const;

protected:
	virtual void BeginPlay() override;

private:
	UPROPERTY()
	TObjectPtr<UInterpToMovementComponent> InterpTo;

	UPROPERTY()
	TObjectPtr<URotatingMovementComponent> RotatingMovement;
};
