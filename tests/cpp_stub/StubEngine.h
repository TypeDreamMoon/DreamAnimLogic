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
#include <memory>
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

enum class ETeleportType : uint8
{
	None,
	TeleportPhysics,
	ResetPhysics,
};

enum ECollisionChannel : int
{
	ECC_WorldStatic, ECC_WorldDynamic, ECC_Pawn, ECC_Visibility, ECC_Camera, ECC_PhysicsBody, ECC_Vehicle, ECC_Destructible,
	ECC_EngineTraceChannel1, ECC_EngineTraceChannel2, ECC_EngineTraceChannel3, ECC_EngineTraceChannel4, ECC_EngineTraceChannel5, ECC_EngineTraceChannel6,
	ECC_GameTraceChannel1, ECC_GameTraceChannel2, ECC_GameTraceChannel3, ECC_GameTraceChannel4, ECC_GameTraceChannel5, ECC_GameTraceChannel6,
	ECC_GameTraceChannel7, ECC_GameTraceChannel8, ECC_GameTraceChannel9, ECC_GameTraceChannel10, ECC_GameTraceChannel11, ECC_GameTraceChannel12,
	ECC_GameTraceChannel13, ECC_GameTraceChannel14, ECC_GameTraceChannel15, ECC_GameTraceChannel16, ECC_GameTraceChannel17, ECC_GameTraceChannel18,
	ECC_MAX,
};

namespace ERootMotionMode
{
	enum Type : int
	{
		NoRootMotionExtraction,
		IgnoreRootMotion,
		RootMotionFromEverything,
		RootMotionFromMontagesOnly,
	};
}

enum ECollisionResponse : int
{
	ECR_Ignore,
	ECR_Overlap,
	ECR_Block,
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

// ---- tick functions -----------------------------------------------------------------------------------------------
// FTickFunction / FActorComponentTickFunction as far as the generated tick-slot code uses them: the setup fields,
// prerequisites, registration state and the ExecuteTick entry point.
namespace ENamedThreads
{
	enum Type : int
	{
		GameThread,
	};
}
using FGraphEventRef = std::shared_ptr<int>;

struct FTickFunction
{
	bool bCanEverTick = false;
	bool bStartWithTickEnabled = true;
	ETickingGroup TickGroup = TG_PrePhysics;
	bool bStubRegistered = false;
	std::vector<std::pair<const UObject*, const FTickFunction*>> StubPrerequisites;

	virtual ~FTickFunction() = default;
	void AddPrerequisite(UObject* TargetObject, FTickFunction& TargetTickFunction) { StubPrerequisites.push_back({TargetObject, &TargetTickFunction}); }
	bool IsTickFunctionRegistered() const { return bStubRegistered; }
	void UnRegisterTickFunction() { bStubRegistered = false; }
	virtual void ExecuteTick(float, ELevelTick, ENamedThreads::Type, const FGraphEventRef&) {}
	virtual FString DiagnosticMessage() { return FString(); }
};

struct FActorComponentTickFunction : public FTickFunction
{
	template <class Fn>
	static void ExecuteTickHelper(UObject* /*Target*/, bool /*bTickInEditor*/, float DeltaTime, ELevelTick /*TickType*/, Fn&& Function)
	{
		Function(DeltaTime);
	}
};

template <class T>
struct TWeakObjectPtr
{
	T* Ptr = nullptr;
	TWeakObjectPtr() = default;
	TWeakObjectPtr(T* In) : Ptr(In) {}
	TWeakObjectPtr& operator=(T* In) { Ptr = In; return *this; }
	T* Get() const { return Ptr; }
	T* operator->() const { return Ptr; }
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

class UPrimitiveComponent;
class APawn;

struct FHitResult
{
	bool bBlockingHit = false;
	bool bStartPenetrating = false;
	FVector Location;
	FVector ImpactPoint;
	FVector Normal;
	FVector ImpactNormal;
	float Distance = 0.0f;
	float Time = 1.0f;
	TWeakObjectPtr<UPrimitiveComponent> Component;
	UPrimitiveComponent* GetComponent() const { return Component.Get(); }
};

struct FCollisionQueryParams
{
	FName TraceTag;
	const AActor* Ignored = nullptr;
	FCollisionQueryParams(FName InTag, int /*StatId*/, bool /*bTraceComplex*/, const AActor* InIgnored) : TraceTag(InTag), Ignored(InIgnored) {}
};
#define SCENE_QUERY_STAT(QueryName) FName(#QueryName), 0

// the responses of a query: stored and accepted, the stub's scene blocks everything
struct FCollisionResponseContainer
{
	ECollisionResponse Responses[ECC_MAX];
	FCollisionResponseContainer(ECollisionResponse All = ECR_Block) { for (ECollisionResponse& R : Responses) { R = All; } }
	void SetResponse(ECollisionChannel Channel, ECollisionResponse NewResponse) { Responses[Channel] = NewResponse; }
};

struct FCollisionResponseParams
{
	FCollisionResponseContainer CollisionResponse;
	FCollisionResponseParams(ECollisionResponse DefaultResponse = ECR_Block) : CollisionResponse(DefaultResponse) {}
};

struct FCollisionShape
{
	float Radius = 0.0f;
	float HalfHeight = 0.0f;
	static FCollisionShape MakeSphere(float InRadius) { FCollisionShape S; S.Radius = InRadius; S.HalfHeight = InRadius; return S; }
	static FCollisionShape MakeCapsule(float InRadius, float InHalfHeight) { FCollisionShape S; S.Radius = InRadius; S.HalfHeight = InHalfHeight; return S; }
};

// a component of the stub scene: the ground plane and the spheres are primitives with scripted properties
class UPrimitiveComponent : public UObject
{
public:
	std::string StubName;
	FVector StubVelocity;
	bool StubCanStepUp = true;
	FTransform StubTransform;
	FVector GetComponentVelocity() const { return StubVelocity; }
	FTransform GetComponentTransform() const { return StubTransform; }
	bool CanCharacterStepUp(APawn*) const { return StubCanStepUp; }
};

class UWorld : public UObject
{
public:
	double Time = 0.0;
	double GetTimeSeconds() const { return Time; }

	// the scene the stub traces against (same analytic scene as dal.mockhost.MockHost): the ground plane is StubPrims[0], the spheres follow
	bool HasGround = false;
	double GroundZ = 0.0;
	std::vector<std::pair<FVector, double>> Spheres;
	std::vector<UPrimitiveComponent*> StubPrims;
	std::vector<AActor*> StubActors;

	bool LineTraceSingleByChannel(FHitResult& OutHit, const FVector& Start, const FVector& End, ECollisionChannel, const FCollisionQueryParams&) const
	{
		return Trace(OutHit, Start, End, 0.0, 0.0);
	}

	bool SweepSingleByChannel(FHitResult& OutHit, const FVector& Start, const FVector& End, const FQuat&, ECollisionChannel, const FCollisionShape& Shape,
	                          const FCollisionQueryParams&, const FCollisionResponseParams& = FCollisionResponseParams()) const
	{
		return Trace(OutHit, Start, End, Shape.Radius, Shape.HalfHeight);
	}

	bool OverlapBlockingTestByChannel(const FVector& Pos, const FQuat&, ECollisionChannel, const FCollisionShape& Shape, const FCollisionQueryParams&,
	                                  const FCollisionResponseParams& = FCollisionResponseParams()) const
	{
		const double Reach = Shape.HalfHeight - Shape.Radius;
		const int Count = Reach > 0.0 ? 3 : 1;
		for (int K = 0; K < Count; ++K)
		{
			const double Off = Count == 1 ? 0.0 : (K == 0 ? -Reach : (K == 1 ? 0.0 : Reach));
			const FVector S(Pos.X, Pos.Y, Pos.Z + Off);
			if (HasGround && std::fabs(S.Z - GroundZ) < Shape.Radius) { return true; }
			for (const auto& Sphere : Spheres)
			{
				const FVector F = S - Sphere.first;
				const double Rr = Sphere.second + Shape.Radius;
				if (F.X * F.X + F.Y * F.Y + F.Z * F.Z < Rr * Rr) { return true; }
			}
		}
		return false;
	}

private:
	struct FCandidate
	{
		bool Valid = false;
		double T = 0.0;
		FVector Point, Normal;
		int Prim = 0;
		bool Penetrating = false;
	};

	// one sphere of the swept shape against the ground plane and the spheres (the order and the strictness of dal.mockhost)
	FCandidate TraceOne(const FVector& S, const FVector& D, double Radius) const
	{
		FCandidate Best;
		if (HasGround)
		{
			const bool Above = S.Z >= GroundZ;
			if (std::fabs(S.Z - GroundZ) < Radius)
			{
				Best = FCandidate{true, 0.0, FVector(S.X, S.Y, GroundZ), FVector(0.0, 0.0, Above ? 1.0 : -1.0), 0, true};
			}
			else if (D.Z != 0.0)
			{
				const double Gz = GroundZ + (Above ? Radius : -Radius);
				const double T = (Gz - S.Z) / D.Z;
				if (T >= 0.0 && T <= 1.0)
				{
					Best = FCandidate{true, T, FVector(S.X + D.X * T, S.Y + D.Y * T, GroundZ), FVector(0.0, 0.0, Above ? 1.0 : -1.0), 0, false};
				}
			}
		}
		for (size_t I = 0; I < Spheres.size(); ++I)
		{
			const FVector& C = Spheres[I].first;
			const double R = Spheres[I].second;
			const double Rr = R + Radius;
			const double Fx = S.X - C.X, Fy = S.Y - C.Y, Fz = S.Z - C.Z;
			if (Fx * Fx + Fy * Fy + Fz * Fz < Rr * Rr)
			{
				const double Len = std::sqrt(Fx * Fx + Fy * Fy + Fz * Fz);
				FVector N(0.0, 0.0, 1.0);
				if (Len > 0.0) { N = FVector(Fx / Len, Fy / Len, Fz / Len); }
				if (!Best.Valid || 0.0 < Best.T)
				{
					Best = FCandidate{true, 0.0, FVector(C.X + N.X * R, C.Y + N.Y * R, C.Z + N.Z * R), N, static_cast<int>(I) + 1, true};
				}
				continue;
			}
			const double Qa = D.X * D.X + D.Y * D.Y + D.Z * D.Z;
			if (Qa <= 0.0) { continue; }
			const double Qb = 2.0 * (Fx * D.X + Fy * D.Y + Fz * D.Z);
			const double Qc = Fx * Fx + Fy * Fy + Fz * Fz - Rr * Rr;
			const double Disc = Qb * Qb - 4.0 * Qa * Qc;
			if (Disc < 0.0) { continue; }
			const double T = (-Qb - std::sqrt(Disc)) / (2.0 * Qa);
			if (T >= 0.0 && T <= 1.0 && (!Best.Valid || T < Best.T))
			{
				const double Px = S.X + D.X * T, Py = S.Y + D.Y * T, Pz = S.Z + D.Z * T;
				double Nx = Px - C.X, Ny = Py - C.Y, Nz = Pz - C.Z;
				const double N = std::sqrt(Nx * Nx + Ny * Ny + Nz * Nz);
				if (N > 0.0) { Nx /= N; Ny /= N; Nz /= N; }
				Best = FCandidate{true, T, FVector(C.X + Nx * R, C.Y + Ny * R, C.Z + Nz * R), FVector(Nx, Ny, Nz), static_cast<int>(I) + 1, false};
			}
		}
		return Best;
	}

	bool Trace(FHitResult& Out, const FVector& A, const FVector& B, double Radius, double HalfHeight) const
	{
		const FVector D(B.X - A.X, B.Y - A.Y, B.Z - A.Z);
		const double Length = std::sqrt(D.X * D.X + D.Y * D.Y + D.Z * D.Z);
		const double Reach = HalfHeight - Radius;
		const int Count = Reach > 0.0 ? 3 : 1;
		FCandidate Best;
		for (int K = 0; K < Count; ++K)
		{
			const double Off = Count == 1 ? 0.0 : (K == 0 ? -Reach : (K == 1 ? 0.0 : Reach));
			const FCandidate Hit = TraceOne(FVector(A.X, A.Y, A.Z + Off), D, Radius);
			if (Hit.Valid && (!Best.Valid || Hit.T < Best.T)) { Best = Hit; }
		}
		if (!Best.Valid) { return false; }
		Out.bBlockingHit = true;
		Out.bStartPenetrating = Best.Penetrating;
		Out.Location = FVector(A.X + D.X * Best.T, A.Y + D.Y * Best.T, A.Z + D.Z * Best.T);
		Out.ImpactPoint = Best.Point;
		Out.ImpactNormal = Best.Normal;
		Out.Normal = Best.Normal;
		Out.Distance = static_cast<float>(Best.T * Length);
		Out.Time = static_cast<float>(Best.T);
		Out.Component = Best.Prim < static_cast<int>(StubPrims.size()) ? StubPrims[static_cast<size_t>(Best.Prim)] : nullptr;
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
	FTickFunction PrimaryActorTick;
	virtual void SetControlRotation(const FRotator& NewRotation)
	{
		StubLog::Emit("set_control_rotation", {NewRotation.Pitch, NewRotation.Yaw, NewRotation.Roll});
	}
};

class AActor : public UObject
{
public:
	virtual void SetActorRotation(FRotator NewRotation, ETeleportType Teleport = ETeleportType::None)
	{
		StubLog::Emit("set_actor_rotation", {NewRotation.Pitch, NewRotation.Yaw, NewRotation.Roll, Teleport != ETeleportType::None});
	}
	FVector StubLocation;
	std::vector<FName> Tags;
	std::vector<UObject*> StubComponents;
	UInputComponent* InputComponent = nullptr;

	bool StubRegistered = true;

	FVector GetActorLocation() const { return StubLocation; }
	bool HasActorRegisteredAllComponents() const { return StubRegistered; }
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
	FActorComponentTickFunction PrimaryComponentTick;
	FVector Velocity;
	TEnumAsByte<EMovementMode> MovementMode;
	FLoggedFloat MaxWalkSpeed{"set_max_walk_speed"};
	FLoggedFloat MaxAcceleration{"set_max_acceleration"};
	FLoggedFloat BrakingDecelerationWalking{"set_braking_deceleration_walking"};
	FLoggedFloat GravityScale{"set_gravity_scale"};
	FLoggedFloat JumpZVelocity{"set_jump_z_velocity"};
	FLoggedFloat AirControl{"set_air_control"};
	FLoggedFloat MaxWalkSpeedCrouched{"set_max_walk_speed_crouched"};
	FLoggedFloat GroundFriction{"set_ground_friction"};
	FLoggedFloat BrakingFrictionFactor{"set_braking_friction_factor"};
	FLoggedRotator RotationRate{"set_rotation_rate"};
	FLoggedBool bOrientRotationToMovement{"set_orient_rotation_to_movement"};
	FLoggedBool bUseControllerDesiredRotation{"set_use_controller_desired_rotation"};
	// stub state behind the getters
	FVector StubAcceleration;
	float StubMaxAcceleration = 0.0f;
	float StubMaxBrakingDeceleration = 0.0f;
	bool StubGrounded = false;
	bool StubCrouching = false;
	float StubWalkableFloorZ = 0.71f;
	uint8 CustomMovementMode = 0;

	float GetWalkableFloorZ() const { return StubWalkableFloorZ; }
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
	virtual void AddControllerYawInput(float Val) { StubLog::Emit("add_controller_yaw_input", {Val}); }
	virtual void AddControllerPitchInput(float Val) { StubLog::Emit("add_controller_pitch_input", {Val}); }
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

// the capsule of the character (UCapsuleComponent: scaled radius / half height, component scale)
class UCapsuleComponent : public UObject
{
public:
	float StubRadius = 0.0f;
	float StubHalfHeight = 0.0f;
	FVector StubScale{1.0, 1.0, 1.0};
	float GetScaledCapsuleRadius() const { return StubRadius; }               // the stub holds the scaled values
	float GetScaledCapsuleHalfHeight() const { return StubHalfHeight; }
	FVector GetComponentScale() const { return StubScale; }
};

// DalRuntime's movement component: keeps the velocity before depenetration until somebody consumes it
class UDalCharacterMovementComponent : public UCharacterMovementComponent
{
public:
	FLoggedFloat MaxAccelerationWalking{"set_max_acceleration_walking"};
	FLoggedBool bInputBlocked{"set_input_blocked"};
	FVector StubPrePenetration;
	bool StubPrePenetrationValid = false;
	bool TryConsumePrePenetrationAdjustmentVelocity(FVector& OutVelocity)
	{
		if (!StubPrePenetrationValid) { return false; }
		OutVelocity = StubPrePenetration;
		StubPrePenetrationValid = false;
		StubPrePenetration = FVector();
		return true;
	}
};

class ACharacter : public APawn
{
public:
	UCharacterMovementComponent* StubMovement = nullptr;
	UCapsuleComponent* StubCapsule = nullptr;
	bool StubRootMotion = false;
	UCharacterMovementComponent* GetCharacterMovement() const { return StubMovement; }
	UCapsuleComponent* GetCapsuleComponent() const { return StubCapsule; }
	bool HasAnyRootMotion() const { return StubRootMotion; }
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
class USkeletalMeshComponent;
class UAnimSequenceBase;
struct FBranchingPointNotifyPayload;
struct FAnimNotifyEventReference
{
};

// the base classes of the notify classes a program declares: only the entry points the generated classes override
class UAnimNotify : public UObject
{
public:
	bool bIsNativeBranchingPoint = false;
	virtual FString GetNotifyName_Implementation() const { return FString(); }
	virtual void Notify(USkeletalMeshComponent*, UAnimSequenceBase*, const FAnimNotifyEventReference&) {}
	virtual void BranchingPointNotify(FBranchingPointNotifyPayload&) {}
};

class UAnimNotifyState : public UObject
{
public:
	bool bIsNativeBranchingPoint = false;
	virtual FString GetNotifyName_Implementation() const { return FString(); }
	virtual void NotifyBegin(USkeletalMeshComponent*, UAnimSequenceBase*, float, const FAnimNotifyEventReference&) {}
	virtual void NotifyTick(USkeletalMeshComponent*, UAnimSequenceBase*, float, const FAnimNotifyEventReference&) {}
	virtual void NotifyEnd(USkeletalMeshComponent*, UAnimSequenceBase*, const FAnimNotifyEventReference&) {}
	virtual void BranchingPointNotifyBegin(FBranchingPointNotifyPayload&) {}
	virtual void BranchingPointNotifyTick(FBranchingPointNotifyPayload&, float) {}
	virtual void BranchingPointNotifyEnd(FBranchingPointNotifyPayload&) {}
};

struct FAnimNotifyEvent
{
	TObjectPtr<UAnimNotifyState> NotifyStateClass;
};

class UAnimSequenceBase : public UObject
{
public:
	std::string StubName;
};

struct FBranchingPointNotifyPayload
{
	USkeletalMeshComponent* SkelMeshComponent = nullptr;
	UAnimSequenceBase* SequenceAsset = nullptr;
	FAnimNotifyEvent* NotifyEvent = nullptr;
	int32 MontageInstanceID = INDEX_NONE;
	bool bReachedEnd = false;
};

enum class EAlphaBlendOption : uint8;          // (defined with FAlphaBlend below)
enum class EMontageBlendMode : uint8
{
	Standard,
	Inertialization,
};

// the blend of a montage (UE: FAlphaBlend): its time and easing
struct FStubBlend
{
	float BlendTime = 0.25f;
	EAlphaBlendOption Option = static_cast<EAlphaBlendOption>(0);
	float GetBlendTime() const { return BlendTime; }
	EAlphaBlendOption GetBlendOption() const { return Option; }
};

struct FMontageBlendSettings
{
	FStubBlend Blend;
	EMontageBlendMode BlendMode = EMontageBlendMode::Standard;
	void* BlendProfile = nullptr;
	FMontageBlendSettings() = default;
	explicit FMontageBlendSettings(float InBlendTime) { Blend.BlendTime = InBlendTime; }
	explicit FMontageBlendSettings(const FStubBlend& In) : Blend(In) {}
};

struct FSlotAnimationTrack
{
	FName SlotName;
};

class UAnimMontage : public UAnimSequenceBase
{
public:
	float GetDefaultBlendOutTime() const { return 0.25f; }
	FStubBlend BlendIn;
	FStubBlend BlendOut;
	EMontageBlendMode BlendModeOut = EMontageBlendMode::Standard;
	void* BlendProfileOut = nullptr;
	TArray<FSlotAnimationTrack> SlotAnimTracks;
};

// a playing montage: the stub only logs its Stop
struct FAnimMontageInstance
{
	int32 StubId = 0;
	TObjectPtr<UAnimMontage> Montage;
	bool bStubActive = true;
	bool IsActive() const { return bStubActive; }
	void Stop(const FMontageBlendSettings& BlendOut, bool /*bInterrupt*/ = true)
	{
		StubLog::Emit("montage_instance_stop", {Montage.Ptr->StubName, BlendOut.Blend.BlendTime, static_cast<int>(BlendOut.BlendMode)});
	}
};

struct FPoseSnapshot
{
	std::string StubName;                 // set by the test driver: what the engine would have taken the pose for
	bool bIsValid = false;
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

class UAnimInstance;

// the proxy of an animation instance: PostUpdate is the hook that runs on the game thread after the graph updated
struct FAnimInstanceProxy
{
	FAnimInstanceProxy() = default;
	explicit FAnimInstanceProxy(UAnimInstance*) {}
	virtual ~FAnimInstanceProxy() = default;

protected:
	virtual void PostUpdate(UAnimInstance*) const {}
	friend class UAnimInstance;
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

	TArray<FAnimMontageInstance*> MontageInstances;
	FAnimMontageInstance* GetMontageInstanceForID(int32 MontageInstanceID)
	{
		for (FAnimMontageInstance* Instance : MontageInstances)
		{
			if (Instance != nullptr && Instance->StubId == MontageInstanceID) { return Instance; }
		}
		return nullptr;
	}

	// scripted montage state behind Montage_IsPlaying & co (the stub plays nothing)
	struct FStubMontage
	{
		bool Playing = false;
		bool Active = false;
		float Position = 0.0f;
		FName Section;
	};
	std::map<const UAnimMontage*, FStubMontage> StubMontages;
	std::map<std::string, float> StubCurves;
	float GetCurveValue(FName CurveName) const
	{
		auto It = StubCurves.find(CurveName.Text);
		return It != StubCurves.end() ? It->second : 0.0f;
	}

	USkeletalMeshComponent* StubSkelMesh = nullptr;
	bool bUseMainInstanceMontageEvaluationData = false;
	TEnumAsByte<ERootMotionMode::Type> RootMotionMode = ERootMotionMode::RootMotionFromMontagesOnly;

	virtual APawn* TryGetPawnOwner() const { return StubPawn; }
	AActor* GetOwningActor() const { return StubPawn; }
	USkeletalMeshComponent* GetSkelMeshComponent() const { return StubSkelMesh; }
	UWorld* GetWorld() const { return StubWorld; }
	virtual void NativeInitializeAnimation() {}
	virtual void NativeBeginPlay() {}
	virtual void NativeUpdateAnimation(float) {}
	virtual void NativeThreadSafeUpdateAnimation(float) {}
	virtual void NativeUninitializeAnimation() {}

	// what the engine does after the animation graph evaluated: the proxy's PostUpdate runs on the game thread
	virtual FAnimInstanceProxy* CreateAnimInstanceProxy() { return new FAnimInstanceProxy(this); }
	void StubPostUpdate()
	{
		std::unique_ptr<FAnimInstanceProxy> Proxy(CreateAnimInstanceProxy());
		Proxy->PostUpdate(this);
	}

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
	void Montage_SetPosition(const UAnimMontage* Montage, float NewPosition)
	{
		StubLog::Emit("montage_set_position", {Montage->StubName, NewPosition});
	}
	UAnimMontage* PlaySlotAnimationAsDynamicMontage(UAnimSequenceBase* Asset, FName SlotNodeName, float BlendInTime = 0.25f, float BlendOutTime = 0.25f,
	                                                float InPlayRate = 1.f, int32 LoopCount = 1, float BlendOutTriggerTime = -1.f, float InTimeToStartMontageAt = 0.f)
	{
		StubLog::Emit("play_slot_animation", {Asset->StubName, SlotNodeName, BlendInTime, BlendOutTime, InPlayRate, LoopCount, BlendOutTriggerTime, InTimeToStartMontageAt, 0});
		return nullptr;
	}
	UAnimMontage* PlaySlotAnimationAsDynamicMontage_WithBlendSettings(UAnimSequenceBase* Asset, FName SlotNodeName, const FMontageBlendSettings& BlendInSettings,
	                                                                  const FMontageBlendSettings& BlendOutSettings, float InPlayRate = 1.f, int32 LoopCount = 1,
	                                                                  float BlendOutTriggerTime = -1.f, float InTimeToStartMontageAt = 0.f)
	{
		StubLog::Emit("play_slot_animation", {Asset->StubName, SlotNodeName, BlendInSettings.Blend.BlendTime, BlendOutSettings.Blend.BlendTime, InPlayRate, LoopCount,
		                                      BlendOutTriggerTime, InTimeToStartMontageAt, static_cast<int>(BlendOutSettings.BlendMode)});
		return nullptr;
	}
	// the test driver names the snapshot places (their addresses): what the engine would have taken the pose for
	std::map<const FPoseSnapshot*, std::string> StubSnapshotNames;
	void SnapshotPose(FPoseSnapshot& Snapshot)
	{
		Snapshot.bIsValid = true;
		Snapshot.StubName = StubSnapshotNames[&Snapshot];
		StubLog::Emit("snapshot_pose", {Snapshot.StubName});
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

enum ERelativeTransformSpace : int
{
	RTS_World,
	RTS_Actor,
	RTS_Component,
	RTS_ParentBoneSpace,
};

// the reference skeleton and the physics asset of the mesh, as far as the generated reads use them
struct FReferenceSkeleton
{
	std::vector<std::string> StubNames;
	std::vector<int32> StubParents;
	TArray<FTransform> StubPose;
	int32 FindBoneIndex(FName Name) const
	{
		for (size_t I = 0; I < StubNames.size(); ++I)
		{
			if (StubNames[I] == Name.Text) { return static_cast<int32>(I); }
		}
		return INDEX_NONE;
	}
	int32 GetParentIndex(int32 Index) const { return StubParents[static_cast<size_t>(Index)]; }
	const TArray<FTransform>& GetRefBonePose() const { return StubPose; }
};

class USkeletalMesh : public UObject
{
public:
	FReferenceSkeleton StubRef;
	const FReferenceSkeleton& GetRefSkeleton() const { return StubRef; }
};

class UPhysicsAsset : public UObject
{
public:
	std::vector<std::string> StubBodies;
	int32 FindBodyIndex(FName Name) const
	{
		for (size_t I = 0; I < StubBodies.size(); ++I)
		{
			if (StubBodies[I] == Name.Text) { return static_cast<int32>(I); }
		}
		return INDEX_NONE;
	}
};

class USkeletalMeshComponent : public UObject
{
public:
	FActorComponentTickFunction PrimaryComponentTick;
	AActor* StubOwner = nullptr;
	AActor* GetOwner() const { return StubOwner; }
	UAnimInstance* StubAnim = nullptr;
	FTransform StubTransform;
	std::map<std::string, std::pair<FTransform, FTransform>> StubBones;       // bone -> (component space, world space)
	UPhysicsAsset* StubPhysics = nullptr;
	USkeletalMesh* StubAsset = nullptr;
	UAnimInstance* GetAnimInstance() const { return StubAnim; }
	FTransform GetComponentTransform() const { return StubTransform; }
	UPhysicsAsset* GetPhysicsAsset() const { return StubPhysics; }
	USkeletalMesh* GetSkeletalMeshAsset() const { return StubAsset; }
	FTransform GetSocketTransform(FName Name, ERelativeTransformSpace Space = RTS_World) const
	{
		auto It = StubBones.find(Name.Text);
		if (It == StubBones.end()) { return Space == RTS_World ? StubTransform : FTransform(); }
		return Space == RTS_Component ? It->second.first : It->second.second;
	}
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
	virtual void RegisterComponentTickFunctions(bool) {}
	bool SetupActorComponentTickFunction(FTickFunction* TickFunction) { TickFunction->bStubRegistered = true; return true; }
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

template <class T>
bool IsValid(const TObjectPtr<T>& Object)
{
	return Object.Ptr != nullptr;
}

// ---- data assets and curves ----------------------------------------------------------------------------------------
// UDataAsset with the two hooks generated settings classes override; UCurveFloat / UCurveVector with the evaluation calls
// DalData.h makes.  The stand-in curve is a polyline: linear or constant keys, constant extrapolation (enough to check
// the generated code; FRichCurve's cubic and cycle modes are evaluated by dal/curve.py and by the engine).
struct FPropertyChangedEvent
{
};

class UDataAsset : public UObject
{
public:
	virtual void PostLoad() {}
	virtual void PostEditChangeProperty(FPropertyChangedEvent&) {}
};

template <class T>
const T* GetDefault()
{
	static const T ClassDefaultObject{};
	return &ClassDefaultObject;
}

struct FStubCurveKey
{
	float Time = 0.0f;
	float Value = 0.0f;
	bool bConstant = false;       // the value holds until the next key
};

class UCurveFloat : public UObject
{
public:
	std::vector<FStubCurveKey> StubKeys;      // sorted by time
	void GetTimeRange(float& MinTime, float& MaxTime) const
	{
		MinTime = StubKeys.empty() ? 0.f : StubKeys.front().Time;
		MaxTime = StubKeys.empty() ? 0.f : StubKeys.back().Time;
	}
	float GetFloatValue(float InTime) const
	{
		if (StubKeys.empty()) { return 0.0f; }
		if (InTime <= StubKeys.front().Time) { return StubKeys.front().Value; }
		if (InTime >= StubKeys.back().Time) { return StubKeys.back().Value; }
		for (size_t I = 0; I + 1 < StubKeys.size(); ++I)
		{
			const FStubCurveKey& A = StubKeys[I];
			const FStubCurveKey& B = StubKeys[I + 1];
			if (InTime < B.Time)
			{
				if (A.bConstant) { return A.Value; }
				return A.Value + (B.Value - A.Value) * ((InTime - A.Time) / (B.Time - A.Time));
			}
		}
		return StubKeys.back().Value;
	}
};

// FAlphaBlend::AlphaToBlendOption (AlphaBlend.h): the easing functions, written from their documented formulas
enum class EAlphaBlendOption : uint8
{
	Linear, Cubic, HermiteCubic, Sinusoidal, QuadraticInOut, CubicInOut, QuarticInOut, QuinticInOut,
	CircularIn, CircularOut, CircularInOut, ExpIn, ExpOut, ExpInOut, Custom,
};

struct FAlphaBlend
{
	static float EaseInOut(float A, float Exp)
	{
		return A < 0.5f ? FMath::Pow(A * 2.f, Exp) * 0.5f : (1.f - FMath::Pow(1.f - (A * 2.f - 1.f), Exp)) * 0.5f + 0.5f;
	}
	static float CircularIn(float A) { return -1.f * (FMath::Sqrt(1.f - A * A) - 1.f); }
	static float CircularOut(float A) { A -= 1.f; return FMath::Sqrt(1.f - A * A); }
	static float ExpoIn(float A) { return A == 0.f ? 0.f : FMath::Pow(2.f, 10.f * (A - 1.f)); }
	static float ExpoOut(float A) { return A == 1.f ? 1.f : -FMath::Pow(2.f, -10.f * A) + 1.f; }

	static float AlphaToBlendOption(float A, EAlphaBlendOption Option, UCurveFloat* Curve)
	{
		float R = A;
		switch (Option)
		{
		case EAlphaBlendOption::Sinusoidal: R = (FMath::Sin(A * UE_PI - UE_HALF_PI) + 1.f) / 2.f; break;
		case EAlphaBlendOption::Cubic: R = (-2.f * A * A * A) + (3.f * A * A); break;
		case EAlphaBlendOption::HermiteCubic: R = A < 0.f ? 0.f : (A >= 1.f ? 1.f : A * A * (3.0f - 2.0f * A)); break;
		case EAlphaBlendOption::QuadraticInOut: R = EaseInOut(A, 2.f); break;
		case EAlphaBlendOption::CubicInOut: R = EaseInOut(A, 3.f); break;
		case EAlphaBlendOption::QuarticInOut: R = EaseInOut(A, 4.f); break;
		case EAlphaBlendOption::QuinticInOut: R = EaseInOut(A, 5.f); break;
		case EAlphaBlendOption::CircularIn: R = CircularIn(A); break;
		case EAlphaBlendOption::CircularOut: R = CircularOut(A); break;
		case EAlphaBlendOption::CircularInOut: R = A < 0.5f ? CircularIn(A * 2.f) * 0.5f : CircularOut(A * 2.f - 1.f) * 0.5f + 0.5f; break;
		case EAlphaBlendOption::ExpIn: R = ExpoIn(A); break;
		case EAlphaBlendOption::ExpOut: R = ExpoOut(A); break;
		case EAlphaBlendOption::ExpInOut: R = A < 0.5f ? ExpoIn(A * 2.f) * 0.5f : ExpoOut(A * 2.f - 1.f) * 0.5f + 0.5f; break;
		case EAlphaBlendOption::Custom:
			if (Curve != nullptr)
			{
				float Min = 0.f, Max = 0.f;
				Curve->GetTimeRange(Min, Max);
				R = Curve->GetFloatValue(Min + (Max - Min) * A);
			}
			break;
		default: break;
		}
		return FMath::Clamp<float>(R, 0.f, 1.f);
	}
};

class UCurveVector : public UObject
{
public:
	UCurveFloat StubCurves[3];
	FVector GetVectorValue(float InTime) const
	{
		return FVector(StubCurves[0].GetFloatValue(InTime), StubCurves[1].GetFloatValue(InTime), StubCurves[2].GetFloatValue(InTime));
	}
};
