// Stand-ins for the engine classes the generated DAL code touches (see CoreMinimal.h in this folder).
// Names and signatures follow UE 5.8: UAnimInstance, APawn, ACharacter, UCharacterMovementComponent,
// UWorld, ULocalPlayer, UEnhancedPlayerInput, FInputActionValue. Only what the generated code calls exists.
#pragma once

#include "CoreMinimal.h"

#include <map>

enum EMovementMode : int
{
	MOVE_None,
	MOVE_Walking,
	MOVE_NavWalking,
	MOVE_Falling,
	MOVE_Swimming,
	MOVE_Flying,
	MOVE_Custom,
};

class UObject
{
public:
	virtual ~UObject() = default;
};

class UWorld : public UObject
{
public:
	double Time = 0.0;
	double GetTimeSeconds() const { return Time; }
};

class AController : public UObject
{
};

class UCharacterMovementComponent : public UObject
{
public:
	FVector Velocity;
	TEnumAsByte<EMovementMode> MovementMode;
	float MaxWalkSpeed = 0.0f;
	// stub state behind the getters
	FVector StubAcceleration;
	float StubMaxAcceleration = 0.0f;
	float StubMaxBrakingDeceleration = 0.0f;
	bool StubGrounded = false;
	bool StubCrouching = false;

	FVector GetCurrentAcceleration() const { return StubAcceleration; }
	virtual float GetMaxAcceleration() const { return StubMaxAcceleration; }
	virtual float GetMaxBrakingDeceleration() const { return StubMaxBrakingDeceleration; }
	virtual bool IsMovingOnGround() const { return StubGrounded; }
	virtual bool IsCrouching() const { return StubCrouching; }
};

class APawn : public UObject
{
public:
	FQuat StubQuat;
	FRotator StubControlRotation;
	AController* StubController = nullptr;

	FQuat GetActorQuat() const { return StubQuat; }
	FRotator GetControlRotation() const { return StubControlRotation; }
	AController* GetController() const { return StubController; }
};

class ACharacter : public APawn
{
public:
	UCharacterMovementComponent* StubMovement = nullptr;
	UCharacterMovementComponent* GetCharacterMovement() const { return StubMovement; }
};

class UInputAction : public UObject
{
};

struct FInputActionValue
{
	double V[3] = {0.0, 0.0, 0.0};
	FInputActionValue() = default;
	FInputActionValue(double X, double Y, double Z) : V{X, Y, Z} {}
	template <class T> T Get() const;
};

template <> inline bool FInputActionValue::Get<bool>() const { return V[0] != 0.0; }
template <> inline float FInputActionValue::Get<float>() const { return static_cast<float>(V[0]); }
template <> inline FVector2D FInputActionValue::Get<FVector2D>() const { return FVector2D(V[0], V[1]); }
template <> inline FVector FInputActionValue::Get<FVector>() const { return FVector(V[0], V[1], V[2]); }

class UEnhancedPlayerInput : public UObject
{
public:
	std::map<const UInputAction*, FInputActionValue> Values;
	FInputActionValue GetActionValue(TObjectPtr<const UInputAction> ForAction) const
	{
		auto It = Values.find(ForAction.Ptr);
		return It != Values.end() ? It->second : FInputActionValue();
	}
};

class UEnhancedInputLocalPlayerSubsystem : public UObject
{
public:
	UEnhancedPlayerInput* StubInput = nullptr;
	UEnhancedPlayerInput* GetPlayerInput() const { return StubInput; }
};

class ULocalPlayer : public UObject
{
public:
	UEnhancedInputLocalPlayerSubsystem* StubSubsystem = nullptr;
	template <class T> T* GetSubsystem() const { return StubSubsystem; }
};

class APlayerController : public AController
{
public:
	ULocalPlayer* StubLocalPlayer = nullptr;
	ULocalPlayer* GetLocalPlayer() const { return StubLocalPlayer; }
};

class UAnimInstance : public UObject
{
public:
	APawn* StubPawn = nullptr;
	UWorld* StubWorld = nullptr;

	virtual APawn* TryGetPawnOwner() const { return StubPawn; }
	UWorld* GetWorld() const { return StubWorld; }
	virtual void NativeInitializeAnimation() {}
	virtual void NativeUpdateAnimation(float) {}
	virtual void NativeThreadSafeUpdateAnimation(float) {}
};

template <class T, class U>
auto Cast(U* Object)
{
	// like UE's Cast: the result keeps the constness of the argument
	using Result = std::conditional_t<std::is_const_v<U>, const T, T>;
	return dynamic_cast<Result*>(Object);
}

template <class T>
bool IsValid(const T* Object)
{
	return Object != nullptr;
}
