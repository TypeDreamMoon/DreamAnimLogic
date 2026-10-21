// Stand-ins for the engine classes the generated DAL code touches (see CoreMinimal.h in this folder).
// Names and signatures follow UE 5.8: UAnimInstance (montage API and delegates), UActorComponent, AActor, APawn,
// ACharacter, UCharacterMovementComponent, USpringArmComponent, UCameraComponent, UWorld (traces), UGameplayStatics,
// ULocalPlayer, UEnhancedPlayerInput, UEnhancedInputComponent, FInputActionValue. Only what the generated code
// calls exists.
//
// Every *effect* on the host (a CMC property write, Jump(), Montage_Play(), SetControlRotation() ...) is
// appended to StubLog::Lines() as "<dal command name> <args...>", so a test can compare the commands the
// generated C++ executes with the commands the Python backend hands to its host.
#pragma once

#include "CoreMinimal.h"

#include <functional>
#include <map>
#include <string>
#include <vector>

// ---- the effect log ------------------------------------------------------------------------------------
namespace StubLog
{
	inline std::vector<std::string>& Lines()
	{
		static std::vector<std::string> L;
		return L;
	}

	struct Tok
	{
		std::string S;
		Tok(double D) { char B[64]; std::snprintf(B, sizeof(B), "%.9g", D); S = B; }
		Tok(float D) : Tok(static_cast<double>(D)) {}
		Tok(int D) : S(std::to_string(D)) {}
		Tok(bool D) : S(D ? "1" : "0") {}
		Tok(const char* D) : S(D) {}
		Tok(const std::string& D) : S(D) {}
		Tok(const FName& D) : S(D.ToString()) {}
	};

	inline void Emit(const char* Name, std::initializer_list<Tok> Toks)
	{
		std::string Line = Name;
		for (const Tok& T : Toks) { Line += " "; Line += T.S; }
		Lines().push_back(Line);
	}
}

struct FLoggedFloat
{
	float V = 0.0f;
	const char* Name;
	explicit FLoggedFloat(const char* InName) : Name(InName) {}
	operator float() const { return V; }
	FLoggedFloat& operator=(float In) { V = In; StubLog::Emit(Name, {In}); return *this; }
};

struct FLoggedBool
{
	bool V = false;
	const char* Name;
	explicit FLoggedBool(const char* InName) : Name(InName) {}
	operator bool() const { return V; }
	FLoggedBool& operator=(bool In) { V = In; StubLog::Emit(Name, {In}); return *this; }
};

struct FLoggedRotator
{
	FRotator V;
	const char* Name;
	explicit FLoggedRotator(const char* InName) : Name(InName) {}
	operator FRotator() const { return V; }
	FLoggedRotator& operator=(const FRotator& In) { V = In; StubLog::Emit(Name, {In.Pitch, In.Yaw, In.Roll}); return *this; }
};

struct FLoggedVector
{
	FVector V;
	const char* Name;
	explicit FLoggedVector(const char* InName) : Name(InName) {}
	operator FVector() const { return V; }
	FLoggedVector& operator=(const FVector& In) { V = In; StubLog::Emit(Name, {In.X, In.Y, In.Z}); return *this; }
};

// ---- enums and basics ------------------------------------------------------------------------------------
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

enum ECollisionChannel : int
{
	ECC_WorldStatic,
	ECC_Visibility,
	ECC_Camera,
};

enum ETickingGroup : int
{
	TG_PrePhysics,
	TG_DuringPhysics,
	TG_PostPhysics,
};

enum ELevelTick : int
{
	LEVELTICK_All,
};

namespace EEndPlayReason
{
	enum Type : int
	{
		Destroyed,
		EndPlayInEditor,
	};
}

enum class ETriggerEvent : uint8
{
	None,
	Triggered,
	Started,
	Ongoing,
	Completed,
	Canceled,
};

class UObject
{
public:
	virtual ~UObject() = default;
	uint32 GetUniqueID() const { return StubId; }
	uint32 StubId = 0;
};

template <class T>
struct TWeakObjectPtr
{
	T* Ptr = nullptr;
	TWeakObjectPtr() = default;
	TWeakObjectPtr(T* In) : Ptr(In) {}
	TWeakObjectPtr& operator=(T* In) { Ptr = In; return *this; }
	T* Get() const { return Ptr; }
	bool IsValid() const { return Ptr != nullptr; }
	void Reset() { Ptr = nullptr; }
};

// ---- gameplay tags -----------------------------------------------------------------------------------------------
// FGameplayTag compares by exact name (operator== is an exact match in UE); UE_DECLARE/DEFINE_GAMEPLAY_TAG declare and
// define a global FGameplayTag as NativeGameplayTags.h does.
struct FGameplayTag
{
	FName TagName;
	FGameplayTag() = default;
	explicit FGameplayTag(const char* InName) : TagName(InName) {}
	bool IsValid() const { return !TagName.Text.empty(); }
	bool operator==(const FGameplayTag& O) const { return TagName == O.TagName; }
	bool operator!=(const FGameplayTag& O) const { return TagName != O.TagName; }
	bool operator<(const FGameplayTag& O) const { return TagName.Text < O.TagName.Text; }
	std::string ToString() const { return TagName.Text; }
	static FGameplayTag RequestGameplayTag(const FName& InName) { return FGameplayTag(InName.Text.c_str()); }
	static const FGameplayTag EmptyTag;
};
inline const FGameplayTag FGameplayTag::EmptyTag;
#define UE_DECLARE_GAMEPLAY_TAG_EXTERN(TagName) extern FGameplayTag TagName;
#define UE_DEFINE_GAMEPLAY_TAG(TagName, Tag) FGameplayTag TagName(Tag);

// ---- the world and traces --------------------------------------------------------------------------------------
class AActor;

struct FHitResult
{
	bool bBlockingHit = false;
	FVector ImpactPoint;
	FVector ImpactNormal;
	float Distance = 0.0f;
	float Time = 1.0f;
};

struct FCollisionQueryParams
{
	FName TraceTag;
	const AActor* Ignored = nullptr;
	FCollisionQueryParams(FName InTag, int /*StatId*/, bool /*bTraceComplex*/, const AActor* InIgnored) : TraceTag(InTag), Ignored(InIgnored) {}
};
#define SCENE_QUERY_STAT(QueryName) FName(#QueryName), 0

struct FCollisionShape
{
	float Radius = 0.0f;
	static FCollisionShape MakeSphere(float InRadius) { FCollisionShape S; S.Radius = InRadius; return S; }
};

class UWorld : public UObject
{
public:
	double Time = 0.0;
	double GetTimeSeconds() const { return Time; }

	// the scene the stub traces against (same analytic scene as dal.mockhost.MockHost)
	bool HasGround = false;
	double GroundZ = 0.0;
	std::vector<std::pair<FVector, double>> Spheres;
	std::vector<AActor*> StubActors;

	bool LineTraceSingleByChannel(FHitResult& OutHit, const FVector& Start, const FVector& End, ECollisionChannel, const FCollisionQueryParams&) const
	{
		return Trace(OutHit, Start, End, 0.0);
	}

	bool SweepSingleByChannel(FHitResult& OutHit, const FVector& Start, const FVector& End, const FQuat&, ECollisionChannel, const FCollisionShape& Shape, const FCollisionQueryParams&) const
	{
		return Trace(OutHit, Start, End, Shape.Radius);
	}

private:
	bool Trace(FHitResult& Out, const FVector& A, const FVector& B, double Radius) const
	{
		const double Dx = B.X - A.X, Dy = B.Y - A.Y, Dz = B.Z - A.Z;
		const double Length = std::sqrt(Dx * Dx + Dy * Dy + Dz * Dz);
		bool Found = false;
		double BestT = 0.0;
		FVector BestPoint, BestNormal;
		if (HasGround && Dz != 0.0)
		{
			const bool Above = A.Z >= GroundZ;
			const double Gz = GroundZ + (Above ? Radius : -Radius);
			const double T = (Gz - A.Z) / Dz;
			if (T >= 0.0 && T <= 1.0)
			{
				Found = true;
				BestT = T;
				BestPoint = FVector(A.X + Dx * T, A.Y + Dy * T, GroundZ);
				BestNormal = FVector(0.0, 0.0, Above ? 1.0 : -1.0);
			}
		}
		for (const auto& S : Spheres)
		{
			const FVector& C = S.first;
			const double Rr = S.second + Radius;
			const double Fx = A.X - C.X, Fy = A.Y - C.Y, Fz = A.Z - C.Z;
			const double Qa = Dx * Dx + Dy * Dy + Dz * Dz;
			if (Qa <= 0.0) { continue; }
			const double Qb = 2.0 * (Fx * Dx + Fy * Dy + Fz * Dz);
			const double Qc = Fx * Fx + Fy * Fy + Fz * Fz - Rr * Rr;
			const double Disc = Qb * Qb - 4.0 * Qa * Qc;
			if (Disc < 0.0) { continue; }
			const double T = (-Qb - std::sqrt(Disc)) / (2.0 * Qa);
			if (T >= 0.0 && T <= 1.0 && (!Found || T < BestT))
			{
				const double Px = A.X + Dx * T, Py = A.Y + Dy * T, Pz = A.Z + Dz * T;
				double Nx = Px - C.X, Ny = Py - C.Y, Nz = Pz - C.Z;
				const double N = std::sqrt(Nx * Nx + Ny * Ny + Nz * Nz);
				if (N > 0.0) { Nx /= N; Ny /= N; Nz /= N; }
				Found = true;
				BestT = T;
				BestPoint = FVector(C.X + Nx * S.second, C.Y + Ny * S.second, C.Z + Nz * S.second);
				BestNormal = FVector(Nx, Ny, Nz);
			}
		}
		if (!Found) { return false; }
		Out.bBlockingHit = true;
		Out.ImpactPoint = BestPoint;
		Out.ImpactNormal = BestNormal;
		Out.Distance = static_cast<float>(BestT * Length);
		Out.Time = static_cast<float>(BestT);
		return true;
	}
};

// ---- actors, components -------------------------------------------------------------------------------------------
class UInputComponent : public UObject
{
};

class AController : public UObject
{
public:
	virtual void SetControlRotation(const FRotator& NewRotation)
	{
		StubLog::Emit("set_control_rotation", {NewRotation.Pitch, NewRotation.Yaw, NewRotation.Roll});
	}
};

class AActor : public UObject
{
public:
	FVector StubLocation;
	std::vector<FName> Tags;
	std::vector<UObject*> StubComponents;
	UInputComponent* InputComponent = nullptr;

	FVector GetActorLocation() const { return StubLocation; }
	bool ActorHasTag(FName Tag) const { return std::find(Tags.begin(), Tags.end(), Tag) != Tags.end(); }

	template <class T>
	T* FindComponentByClass() const
	{
		for (UObject* C : StubComponents)
		{
			if (T* Found = dynamic_cast<T*>(C)) { return Found; }
		}
		return nullptr;
	}
};

class UCharacterMovementComponent : public UObject
{
public:
	FVector Velocity;
	TEnumAsByte<EMovementMode> MovementMode;
	FLoggedFloat MaxWalkSpeed{"set_max_walk_speed"};
	FLoggedFloat MaxAcceleration{"set_max_acceleration"};
	FLoggedFloat BrakingDecelerationWalking{"set_braking_deceleration_walking"};
	FLoggedFloat GravityScale{"set_gravity_scale"};
	FLoggedFloat JumpZVelocity{"set_jump_z_velocity"};
	FLoggedFloat AirControl{"set_air_control"};
	FLoggedRotator RotationRate{"set_rotation_rate"};
	FLoggedBool bOrientRotationToMovement{"set_orient_rotation_to_movement"};
	FLoggedBool bUseControllerDesiredRotation{"set_use_controller_desired_rotation"};
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
	virtual void SetMovementMode(EMovementMode NewMovementMode, uint8 /*NewCustomMode*/ = 0)
	{
		MovementMode = NewMovementMode;
		StubLog::Emit("set_movement_mode", {static_cast<int>(NewMovementMode)});
	}
};

class USpringArmComponent : public UObject
{
public:
	FLoggedFloat TargetArmLength{"camera_set_arm_length"};
	FLoggedVector SocketOffset{"camera_set_socket_offset"};
};

class UCameraComponent : public UObject
{
public:
	virtual void SetFieldOfView(float InFieldOfView) { StubLog::Emit("camera_set_fov", {InFieldOfView}); }
};

class APawn : public AActor
{
public:
	FQuat StubQuat;
	FRotator StubControlRotation;
	AController* StubController = nullptr;

	FQuat GetActorQuat() const { return StubQuat; }
	FRotator GetControlRotation() const { return StubControlRotation; }
	AController* GetController() const { return StubController; }
	virtual void AddMovementInput(FVector WorldDirection, float ScaleValue = 1.0f, bool /*bForce*/ = false)
	{
		StubLog::Emit("add_movement_input", {WorldDirection.X, WorldDirection.Y, WorldDirection.Z, ScaleValue});
	}
};

class ACharacter : public APawn
{
public:
	UCharacterMovementComponent* StubMovement = nullptr;
	UCharacterMovementComponent* GetCharacterMovement() const { return StubMovement; }
	virtual void Jump() { StubLog::Emit("jump", {}); }
	virtual void StopJumping() { StubLog::Emit("stop_jumping", {}); }
	virtual void Crouch(bool /*bClientSimulation*/ = false) { StubLog::Emit("crouch", {}); }
	virtual void UnCrouch(bool /*bClientSimulation*/ = false) { StubLog::Emit("uncrouch", {}); }
	virtual void LaunchCharacter(FVector LaunchVelocity, bool bXYOverride, bool bZOverride)
	{
		StubLog::Emit("launch", {LaunchVelocity.X, LaunchVelocity.Y, LaunchVelocity.Z, bXYOverride, bZOverride});
	}
};

// ---- enhanced input ------------------------------------------------------------------------------------------------------
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

template <> inline bool FInputActionValue::Get<bool>() const { return V[0] * V[0] + V[1] * V[1] + V[2] * V[2] > 0.0; }
template <> inline float FInputActionValue::Get<float>() const { return static_cast<float>(V[0]); }
template <> inline FVector2D FInputActionValue::Get<FVector2D>() const { return FVector2D(V[0], V[1]); }
template <> inline FVector FInputActionValue::Get<FVector>() const { return FVector(V[0], V[1], V[2]); }

class UEnhancedInputComponent : public UInputComponent
{
public:
	struct FBinding
	{
		const UInputAction* Action;
		ETriggerEvent Event;
		std::function<void(const FInputActionValue&)> Fn;
	};
	std::vector<FBinding> Bindings;

	template <class UserClass>
	void BindAction(const UInputAction* Action, ETriggerEvent TriggerEvent, UserClass* Object, void (UserClass::*Func)(const FInputActionValue&))
	{
		Bindings.push_back({Action, TriggerEvent, [Object, Func](const FInputActionValue& Value) { (Object->*Func)(Value); }});
	}

	// test driver: the input system fires an action event
	void Fire(const UInputAction* Action, ETriggerEvent TriggerEvent, const FInputActionValue& Value) const
	{
		for (const FBinding& B : Bindings)
		{
			if (B.Action == Action && B.Event == TriggerEvent) { B.Fn(Value); }
		}
	}
};

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

// ---- animation: montages, notifies, delegates ------------------------------------------------------------------------------------
class UAnimNotifyState : public UObject
{
};

struct FAnimNotifyEvent
{
	TObjectPtr<UAnimNotifyState> NotifyStateClass;
};

class USkeletalMeshComponent;
class UAnimSequenceBase;

struct FBranchingPointNotifyPayload
{
	USkeletalMeshComponent* SkelMeshComponent = nullptr;
	UAnimSequenceBase* SequenceAsset = nullptr;
	FAnimNotifyEvent* NotifyEvent = nullptr;
	int32 MontageInstanceID = INDEX_NONE;
	bool bReachedEnd = false;
};

class UAnimMontage : public UObject
{
public:
	std::string StubName;
	float GetDefaultBlendOutTime() const { return 0.25f; }
};

// DECLARE_DYNAMIC_MULTICAST_DELEGATE_*: AddUniqueDynamic / RemoveDynamic are macros in UE with this call shape.
template <class... Args>
class TStubMulticast
{
public:
	struct FEntry
	{
		const void* Object;
		std::string Key;
		std::function<void(Args...)> Fn;
	};
	std::vector<FEntry> Entries;

	template <class U>
	void AddUniqueDynamic(U* Object, void (U::*Func)(Args...))
	{
		const std::string Key = KeyOf(Func);
		for (const FEntry& E : Entries)
		{
			if (E.Object == Object && E.Key == Key) { return; }
		}
		Entries.push_back({Object, Key, [Object, Func](Args... A) { (Object->*Func)(A...); }});
	}

	template <class U>
	void RemoveDynamic(U* Object, void (U::*Func)(Args...))
	{
		const std::string Key = KeyOf(Func);
		for (size_t I = 0; I < Entries.size(); ++I)
		{
			if (Entries[I].Object == Object && Entries[I].Key == Key) { Entries.erase(Entries.begin() + static_cast<std::ptrdiff_t>(I)); return; }
		}
	}

	void Broadcast(Args... A) const
	{
		const std::vector<FEntry> Copy = Entries;
		for (const FEntry& E : Copy) { E.Fn(A...); }
	}

private:
	template <class F>
	static std::string KeyOf(const F& Func) { return std::string(reinterpret_cast<const char*>(&Func), sizeof(F)); }
};

class UAnimInstance : public UObject
{
public:
	APawn* StubPawn = nullptr;
	UWorld* StubWorld = nullptr;

	TStubMulticast<UAnimMontage*, bool> OnMontageEnded;
	TStubMulticast<UAnimMontage*, bool> OnMontageBlendingOut;
	TStubMulticast<FName, const FBranchingPointNotifyPayload&> OnPlayMontageNotifyBegin;
	TStubMulticast<FName, const FBranchingPointNotifyPayload&> OnPlayMontageNotifyEnd;

	// scripted montage state behind Montage_IsPlaying & co (the stub plays nothing)
	struct FStubMontage
	{
		bool Playing = false;
		bool Active = false;
		float Position = 0.0f;
		FName Section;
	};
	std::map<const UAnimMontage*, FStubMontage> StubMontages;

	virtual APawn* TryGetPawnOwner() const { return StubPawn; }
	UWorld* GetWorld() const { return StubWorld; }
	virtual void NativeInitializeAnimation() {}
	virtual void NativeUpdateAnimation(float) {}
	virtual void NativeThreadSafeUpdateAnimation(float) {}

	float Montage_Play(UAnimMontage* MontageToPlay, float InPlayRate = 1.0f)
	{
		StubLog::Emit("montage_play", {MontageToPlay->StubName, InPlayRate});
		return 1.0f;
	}
	void Montage_Stop(float InBlendOutTime, const UAnimMontage* Montage = nullptr)
	{
		if (Montage != nullptr) { StubLog::Emit("montage_stop", {Montage->StubName, InBlendOutTime}); }
		else { StubLog::Emit("montage_stop_all", {InBlendOutTime}); }
	}
	void Montage_JumpToSection(FName SectionName, const UAnimMontage* Montage = nullptr)
	{
		StubLog::Emit("montage_jump_to_section", {Montage->StubName, SectionName});
	}
	void Montage_SetNextSection(FName SectionNameToChange, FName NextSection, const UAnimMontage* Montage = nullptr)
	{
		StubLog::Emit("montage_set_next_section", {Montage->StubName, SectionNameToChange, NextSection});
	}
	void Montage_SetPlayRate(const UAnimMontage* Montage, float NewPlayRate = 1.0f)
	{
		StubLog::Emit("montage_set_play_rate", {Montage->StubName, NewPlayRate});
	}
	bool Montage_IsActive(const UAnimMontage* Montage) const { return State(Montage).Active; }
	bool Montage_IsPlaying(const UAnimMontage* Montage) const { return State(Montage).Playing; }
	float Montage_GetPosition(const UAnimMontage* Montage) const { return State(Montage).Position; }
	FName Montage_GetCurrentSection(const UAnimMontage* Montage = nullptr) const { return State(Montage).Section; }
	UAnimMontage* GetCurrentActiveMontage() const { return nullptr; }

private:
	FStubMontage State(const UAnimMontage* Montage) const
	{
		auto It = StubMontages.find(Montage);
		return It != StubMontages.end() ? It->second : FStubMontage();
	}
};

class USkeletalMeshComponent : public UObject
{
public:
	UAnimInstance* StubAnim = nullptr;
	UAnimInstance* GetAnimInstance() const { return StubAnim; }
};

struct FActorComponentTickFunction
{
	bool bCanEverTick = false;
	ETickingGroup TickGroup = TG_DuringPhysics;
};

class UActorComponent : public UObject
{
public:
	FActorComponentTickFunction PrimaryComponentTick;
	AActor* StubOwner = nullptr;
	UWorld* StubWorld = nullptr;

	AActor* GetOwner() const { return StubOwner; }
	UWorld* GetWorld() const { return StubWorld; }
	virtual void BeginPlay() {}
	virtual void EndPlay(const EEndPlayReason::Type) {}
	virtual void TickComponent(float, ELevelTick, FActorComponentTickFunction*) {}
};

struct UGameplayStatics
{
	static void GetAllActorsWithTag(const UObject* WorldContextObject, FName Tag, TArray<AActor*>& OutActors)
	{
		OutActors.Reset();
		const UWorld* World = dynamic_cast<const UWorld*>(WorldContextObject);
		if (World == nullptr) { return; }
		for (AActor* Actor : World->StubActors)
		{
			if (Actor->ActorHasTag(Tag)) { OutActors.Add(Actor); }
		}
	}
};

template <class T, class U>
auto Cast(U* Object)
{
	// like UE's Cast: the result keeps the constness of the argument
	using Result = std::conditional_t<std::is_const_v<U>, const T, T>;
	return dynamic_cast<Result*>(Object);
}

template <class T, class U>
auto Cast(const TObjectPtr<U>& Object)
{
	return Cast<T>(Object.Ptr);
}

template <class T>
bool IsValid(const T* Object)
{
	return Object != nullptr;
}
