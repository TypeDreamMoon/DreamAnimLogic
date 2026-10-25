// UDalHookProbe: a sink of DalHost::FEventRouter that only counts what it is sent.  It stands in for a generated logic that handles the hook
// events of the character, so that PIE tests can see that DalRuntime's movement component posts them and that the router is one object
// shared by the modules (the probe lives in the host module, the poster in DalRuntime).

#pragma once

#include "CoreMinimal.h"
#include "Components/ActorComponent.h"
#include "DalHost.h"
#include "DalHookProbe.generated.h"

UCLASS(ClassGroup = "DAL", Meta = (BlueprintSpawnableComponent))
class UDalHookProbe : public UActorComponent, public DalHost::IEventSink
{
	GENERATED_BODY()

public:
	UPROPERTY(VisibleAnywhere, BlueprintReadOnly, Category = "DAL|Probe")
	int32 ModeChanged = 0;

	UPROPERTY(VisibleAnywhere, BlueprintReadOnly, Category = "DAL|Probe")
	int32 Jumped = 0;

	UPROPERTY(VisibleAnywhere, BlueprintReadOnly, Category = "DAL|Probe")
	int32 Landed = 0;

	UPROPERTY(VisibleAnywhere, BlueprintReadOnly, Category = "DAL|Probe")
	int32 CrouchStarted = 0;

	UPROPERTY(VisibleAnywhere, BlueprintReadOnly, Category = "DAL|Probe")
	int32 CrouchEnded = 0;

	UPROPERTY(VisibleAnywhere, BlueprintReadOnly, Category = "DAL|Probe")
	int32 Teleported = 0;

	UPROPERTY(VisibleAnywhere, BlueprintReadOnly, Category = "DAL|Probe")
	int32 PhysicsRotation = 0;

	UPROPERTY(VisibleAnywhere, BlueprintReadOnly, Category = "DAL|Probe")
	int32 Notifies = 0;

	/** The last modes of MovementModeChanged (previous, new) and the payload of the last Landed. */
	UPROPERTY(VisibleAnywhere, BlueprintReadOnly, Category = "DAL|Probe")
	int32 LastModeFrom = 0;

	UPROPERTY(VisibleAnywhere, BlueprintReadOnly, Category = "DAL|Probe")
	int32 LastModeTo = 0;

	UPROPERTY(VisibleAnywhere, BlueprintReadOnly, Category = "DAL|Probe")
	float LastLandedNormalZ = 0.0f;

	UPROPERTY(VisibleAnywhere, BlueprintReadOnly, Category = "DAL|Probe")
	bool bLastLandedBlocking = false;

	UPROPERTY(VisibleAnywhere, BlueprintReadOnly, Category = "DAL|Probe")
	bool bLastLandedHasPrimitive = false;

	UPROPERTY(VisibleAnywhere, BlueprintReadOnly, Category = "DAL|Probe")
	float LastPhysicsRotationDelta = 0.0f;

	/** One line per event, oldest first (the last 64). */
	UPROPERTY(VisibleAnywhere, BlueprintReadOnly, Category = "DAL|Probe")
	TArray<FString> Log;

	UFUNCTION(BlueprintCallable, Category = "DAL|Probe")
	void ResetCounts();

	/** Posts a notify event through the router, like a generated notify class does. */
	UFUNCTION(BlueprintCallable, Category = "DAL|Probe")
	void PostTestNotify(FName Name);

	virtual void BeginPlay() override;
	virtual void EndPlay(const EEndPlayReason::Type EndPlayReason) override;

	virtual const AActor* DalSinkOwner() const override { return GetOwner(); }
	virtual void DalReceiveEvent(const DalHost::FEvent& Event) override;
};
