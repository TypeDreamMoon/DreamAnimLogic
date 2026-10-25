// ADalTestCharacter: a bare ACharacter whose movement component is UDalCharacterMovementComponent and which has none of the logic of ALS's
// AAlsCharacter (the Blender side's 'als_cmc' character of the c3 comparison scenarios, anim-3c-design.md section 11.5).
//
// Editor Python cannot reach the plain C++ setters of the movement component or add a root motion source, so the pawn wraps them in
// UFUNCTIONs (moon/tools/c3_ue_compare.py calls them by name with call_method).

#pragma once

#include "CoreMinimal.h"
#include "GameFramework/Character.h"
#include "DalTestCharacter.generated.h"

class UDalCharacterMovementComponent;

UCLASS(ClassGroup = "DAL")
class DALRUNTIME_API ADalTestCharacter : public ACharacter
{
	GENERATED_BODY()

public:
	explicit ADalTestCharacter(const FObjectInitializer& ObjectInitializer);

	/** The movement component as the DAL one. */
	UFUNCTION(BlueprintPure, Category = "DAL|Test")
	UDalCharacterMovementComponent* GetDalMovement() const;

	UFUNCTION(BlueprintCallable, Category = "DAL|Test")
	void SetMovementModeLocked(bool bLocked);

	UFUNCTION(BlueprintCallable, Category = "DAL|Test")
	void SetInputBlocked(bool bBlocked);

	/** Applies (or changes) the root motion override: velocity in cm/s, the rotation delta of every update. */
	UFUNCTION(BlueprintCallable, Category = "DAL|Test")
	void RootMotionOverride(FVector NewVelocity, FQuat NewRotationDelta);

	UFUNCTION(BlueprintCallable, Category = "DAL|Test")
	void RootMotionClear();
};
