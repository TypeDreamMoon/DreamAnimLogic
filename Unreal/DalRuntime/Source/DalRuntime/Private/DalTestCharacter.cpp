#include "DalTestCharacter.h"

#include "DalCharacterMovementComponent.h"

#include UE_INLINE_GENERATED_CPP_BY_NAME(DalTestCharacter)

ADalTestCharacter::ADalTestCharacter(const FObjectInitializer& ObjectInitializer)
	: Super(ObjectInitializer.SetDefaultSubobjectClass<UDalCharacterMovementComponent>(CharacterMovementComponentName))
{
}

UDalCharacterMovementComponent* ADalTestCharacter::GetDalMovement() const
{
	return Cast<UDalCharacterMovementComponent>(GetCharacterMovement());
}

void ADalTestCharacter::SetMovementModeLocked(const bool bLocked)
{
	if (UDalCharacterMovementComponent* Movement = GetDalMovement())
	{
		Movement->SetMovementModeLocked(bLocked);
	}
}

void ADalTestCharacter::SetInputBlocked(const bool bBlocked)
{
	if (UDalCharacterMovementComponent* Movement = GetDalMovement())
	{
		Movement->SetInputBlocked(bBlocked);
	}
}

void ADalTestCharacter::RootMotionOverride(const FVector NewVelocity, const FQuat NewRotationDelta)
{
	if (UDalCharacterMovementComponent* Movement = GetDalMovement())
	{
		Movement->SetRootMotionOverride(NewVelocity, NewRotationDelta);
	}
}

void ADalTestCharacter::RootMotionClear()
{
	if (UDalCharacterMovementComponent* Movement = GetDalMovement())
	{
		Movement->ClearRootMotionOverride();
	}
}
