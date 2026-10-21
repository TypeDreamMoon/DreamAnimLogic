// DalInterpHost: a thin component that runs a .dal program through the editor-Python interpreter.
//
// It is the twin of the C++ the dal compiler generates for `logic X : actor;` (and, for `logic X;`, of the
// generated UAnimInstance parent): same tick group, same order inside the tick -- bind the host, sample, take
// events, update, execute the commands -- but the program is read from the .dal file by dal_live (Content/Python)
// and swapped as soon as the file changes.  This class owns what Python cannot reach: the delegates that carry
// the montage / notify events, the Enhanced Input bindings, traces, the target list and writes to reflected
// properties.  Everything else (CMC reads, commands) is done by the Python HostV1 with the `unreal` module.
//
// In a game (no Python) the component logs once and does nothing.

#pragma once

#include "CoreMinimal.h"
#include "Animation/AnimNotifies/AnimNotify.h"
#include "Components/ActorComponent.h"
#include "Engine/EngineTypes.h"
#include "DalLiveTypes.h"
#include "DalInterpHost.generated.h"

class ACharacter;
class APawn;
class UAnimInstance;
class UAnimMontage;
class UCameraComponent;
class UCharacterMovementComponent;
class UEnhancedInputComponent;
class UInputAction;
class USkeletalMeshComponent;
class USpringArmComponent;
struct FInputActionValue;

UCLASS(ClassGroup = (Dal), Meta = (BlueprintSpawnableComponent, DisplayName = "DAL Interpreter Host"))
class DALLIVE_API UDalInterpHost : public UActorComponent
{
	GENERATED_BODY()

public:
	UDalInterpHost();

	// ---- configuration ----

	/** The .dal source: absolute, or relative to the project directory. */
	UPROPERTY(EditAnywhere, BlueprintReadWrite, Category = "DAL", Meta = (FilePathFilter = "dal"))
	FFilePath DalFile;

	/** Reload the program when the file changes on disk (and swap it into the running logic). */
	UPROPERTY(EditAnywhere, BlueprintReadWrite, Category = "DAL")
	bool bHotReload = true;

	/** Montage assets by the name used in the .dal (`montage Light1 { ... }`). */
	UPROPERTY(EditAnywhere, BlueprintReadWrite, Category = "DAL|Host")
	TMap<FName, TObjectPtr<UAnimMontage>> Montages;

	/** Input actions by the name used in the .dal (`action Attack: bool;`). */
	UPROPERTY(EditAnywhere, BlueprintReadWrite, Category = "DAL|Host")
	TMap<FName, TObjectPtr<UInputAction>> Actions;

	/** host.target_* sees the actors that carry this tag (collected once per tick, the owner excluded). */
	UPROPERTY(EditAnywhere, BlueprintReadWrite, Category = "DAL|Host")
	FName TargetTag = FName(TEXT("DalTarget"));

	/** Channel of host.trace_line / trace_sphere. */
	UPROPERTY(EditAnywhere, BlueprintReadWrite, Category = "DAL|Host")
	TEnumAsByte<ECollisionChannel> TraceChannel = ECC_Visibility;

	/** Overrides of the program's `param`s as text: "600.0", "true", "(1,2,3)", an enum member name. */
	UPROPERTY(EditAnywhere, BlueprintReadWrite, Category = "DAL|Params")
	TMap<FName, FString> ParamOverrides;

	/**
	 * Write the program's inputs, states and params into properties of the same name on the owner's AnimInstance
	 * after every update (the interpreted twin of the generated UPROPERTYs of a `logic X;` parent class).
	 * Anim logic always does this; for actor logic it is opt-in.
	 */
	UPROPERTY(EditAnywhere, BlueprintReadWrite, Category = "DAL|Anim")
	bool bPublishToAnimInstance = false;

	// ---- status (written by dal_live) ----

	UPROPERTY(VisibleInstanceOnly, BlueprintReadOnly, Transient, Category = "DAL|Status")
	FString Status;

	UPROPERTY(VisibleInstanceOnly, BlueprintReadOnly, Transient, Category = "DAL|Status")
	FString LogicName;

	/** "actor" or "anim". */
	UPROPERTY(VisibleInstanceOnly, BlueprintReadOnly, Transient, Category = "DAL|Status")
	FString LogicKind;

	/** How many programs have been loaded into this host (1 = the first one). */
	UPROPERTY(VisibleInstanceOnly, BlueprintReadOnly, Transient, Category = "DAL|Status")
	int32 Generation = 0;

	UPROPERTY(VisibleInstanceOnly, BlueprintReadOnly, Transient, Category = "DAL|Status")
	int32 SessionId = 0;

	/** Counts the changes of the owner's AnimInstance (the Python host drops its cached AnimInstance when it moves). */
	UPROPERTY(VisibleInstanceOnly, BlueprintReadOnly, Transient, Category = "DAL|Status")
	int32 AnimEpoch = 0;

	virtual void BeginPlay() override;
	virtual void EndPlay(const EEndPlayReason::Type EndPlayReason) override;
	virtual void TickComponent(float DeltaTime, ELevelTick TickType, FActorComponentTickFunction* ThisTickFunction) override;

	// ---- for dal_live (Python) ----

	UFUNCTION(BlueprintCallable, Category = "DAL")
	void SetStatus(const FString& InStatus, const FString& InLogicName, const FString& InLogicKind, int32 InGeneration);

	/**
	 * Tell the host which traffic the loaded program wants (called after every load / swap).
	 * EventNotifies: notify names with `on notify / notify_begin / notify_end` handlers; FlagNotifies: names read with
	 * host.notify("N"); InputWatches: "Action:phase" with phase started / triggered / ongoing / completed / canceled.
	 */
	UFUNCTION(BlueprintCallable, Category = "DAL")
	void ConfigureWatch(const TArray<FName>& EventNotifies, const TArray<FName>& FlagNotifies, bool bMontageEnded, bool bMontageBlendingOut, const TArray<FString>& InputWatches);

	/** Make the owner's skeletal mesh tick after this component, so anim logic updates before the animation graph evaluates. */
	UFUNCTION(BlueprintCallable, Category = "DAL")
	void OrderBeforeMesh();

	/** The events queued since the last call, oldest first. */
	UFUNCTION(BlueprintCallable, Category = "DAL")
	TArray<FDalLiveEvent> TakeEvents();

	/** True once after a watched point notify fired (host.notify("N")). */
	UFUNCTION(BlueprintCallable, Category = "DAL")
	bool ConsumeNotifyFlag(FName NotifyName);

	/** UEnhancedPlayerInput::GetActionValue of the owner's local player, as a vector (bool / float in X, vec2 in XY). */
	UFUNCTION(BlueprintCallable, Category = "DAL")
	FVector GetActionValue(FName ActionName);

	UFUNCTION(BlueprintCallable, Category = "DAL")
	UAnimMontage* GetMontageAsset(FName MontageName) const;

	UFUNCTION(BlueprintCallable, Category = "DAL")
	ACharacter* GetCharacter() const;

	UFUNCTION(BlueprintCallable, Category = "DAL")
	UCharacterMovementComponent* GetMovement() const;

	UFUNCTION(BlueprintCallable, Category = "DAL")
	UAnimInstance* GetAnimInstance() const;

	UFUNCTION(BlueprintCallable, Category = "DAL")
	USpringArmComponent* GetSpringArm() const;

	UFUNCTION(BlueprintCallable, Category = "DAL")
	UCameraComponent* GetCamera() const;

	/** Same as the generated component: LineTraceSingleByChannel(TraceChannel, ignoring the owner). */
	UFUNCTION(BlueprintCallable, Category = "DAL")
	FDalLiveHit TraceLine(FVector Start, FVector End);

	/** SweepSingleByChannel with a sphere. */
	UFUNCTION(BlueprintCallable, Category = "DAL")
	FDalLiveHit TraceSphere(FVector Start, FVector End, float Radius);

	UFUNCTION(BlueprintCallable, Category = "DAL")
	int32 TargetCount();

	UFUNCTION(BlueprintCallable, Category = "DAL")
	int32 TargetId(int32 Index);

	UFUNCTION(BlueprintCallable, Category = "DAL")
	FVector TargetLocation(int32 Index);

	UFUNCTION(BlueprintCallable, Category = "DAL")
	int32 TargetIndex(int32 Id);

	/**
	 * Plan a publish: which of Names are properties of Target's class that PublishValues can write (float, double, int,
	 * bool, byte / enum, FVector, FVector2D, FRotator, FQuat and the float variants of the vector types).  Returns the
	 * accepted names; they are written, in this order, by every PublishValues call.
	 * Kinds[i]: 0 scalar (float, int, bool, enum index), 1 vec2, 2 vec3, 3 rotator (pitch, yaw, roll), 4 quat (x, y, z, w).
	 */
	UFUNCTION(BlueprintCallable, Category = "DAL")
	TArray<FName> SetPublishPlan(UObject* Target, const TArray<FName>& Names, const TArray<uint8>& Kinds);

	/**
	 * Write the values of the planned properties: the components of all accepted names one after the other.
	 * Returns how many properties were written, or -1 when the plan is stale (target gone or its class changed).
	 */
	UFUNCTION(BlueprintCallable, Category = "DAL")
	int32 PublishValues(const TArray<double>& Data);

	/**
	 * The other direction: values the game writes into the target's properties (the free inputs of an anim program, set from
	 * Blueprints or C++ on the AnimInstance).  SetReadPlan accepts the properties it can read (same kinds as the publish plan);
	 * ReadValues returns their components one after the other, or an empty array when the plan is stale.
	 */
	UFUNCTION(BlueprintCallable, Category = "DAL")
	TArray<FName> SetReadPlan(UObject* Target, const TArray<FName>& Names, const TArray<uint8>& Kinds);

	UFUNCTION(BlueprintCallable, Category = "DAL")
	TArray<double> ReadValues();

	/** Cost of the OnHostTick broadcast (the whole Python update) since the last reset: microseconds total / max, calls. */
	UFUNCTION(BlueprintCallable, Category = "DAL")
	void GetTickStats(double& OutTotalMicros, double& OutMaxMicros, int32& OutCalls) const;

	UFUNCTION(BlueprintCallable, Category = "DAL")
	void ResetTickStats();

	/** The individual broadcast costs in microseconds since the last reset (at most 20000), for percentiles. */
	UFUNCTION(BlueprintCallable, Category = "DAL")
	TArray<float> GetTickSamples() const;

private:
	// ---- host bindings (mirror DalBindHost of the generated component) ----
	void DalBindHost();
	void BindAnim(UAnimInstance* Anim);
	void UnbindAnim(UAnimInstance* Anim);
	void RebindInput(UEnhancedInputComponent* Input);
	void UnbindInput();
	USkeletalMeshComponent* FindMesh() const;
	APawn* GetPawn() const;
	FName MontageNameOf(const UAnimMontage* Montage) const;
	void PostEvent(const FDalLiveEvent& Event);
	void PostInput(FName ActionName, FName PhaseName, const FInputActionValue& Value);
	void RefreshTargets();

	UFUNCTION()
	void HandleMontageEnded(UAnimMontage* Montage, bool bInterrupted);

	UFUNCTION()
	void HandleMontageBlendingOut(UAnimMontage* Montage, bool bInterrupted);

	UFUNCTION()
	void HandlePlayMontageNotifyBegin(FName NotifyName, const FBranchingPointNotifyPayload& Payload);

	UFUNCTION()
	void HandlePlayMontageNotifyEnd(FName NotifyName, const FBranchingPointNotifyPayload& Payload);

	mutable FCriticalSection QueueLock;
	TArray<FDalLiveEvent> EventQueue;
	TSet<FName> NotifyFlags;
	TSet<FName> WatchedNotifies;
	TSet<FName> FlagNotifies;
	TArray<TPair<FName, FName>> WatchedInputs;      // (action, phase)
	bool bWatchMontageEnded = false;
	bool bWatchBlendingOut = false;
	bool bInputWatchDirty = false;

	TWeakObjectPtr<UAnimInstance> BoundAnim;
	TWeakObjectPtr<UEnhancedInputComponent> BoundInput;
	TArray<uint32> InputBindingHandles;

	TArray<TWeakObjectPtr<AActor>> Targets;
	bool bTargetsFresh = false;

	struct FPublishEntry
	{
		FProperty* Property = nullptr;
		uint8 Kind = 0;
	};
	TArray<FPublishEntry> PublishPlan;
	TArray<FPublishEntry> ReadPlan;
	TWeakObjectPtr<UObject> ReadTarget;
	TWeakObjectPtr<const UClass> ReadClass;
	TWeakObjectPtr<UObject> PublishTarget;
	TWeakObjectPtr<const UClass> PublishClass;

	TArray<float> TickSamples;
	double TickMicrosTotal = 0.0;
	double TickMicrosMax = 0.0;
	int32 TickCalls = 0;
	TSet<FName> WarnedActions;
	bool bWarnedNoPython = false;
};
