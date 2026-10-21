// The meeting point of the C++ host components and the editor Python side (dal_live).
//
// Python binds the three delegates once at editor start (Content/Python/init_unreal.py); a UDalInterpHost
// broadcasts them from its BeginPlay / EndPlay / TickComponent.  Nothing here knows the .dal language.

#pragma once

#include "CoreMinimal.h"
#include "Subsystems/EngineSubsystem.h"
#include "DalLiveTypes.h"
#include "DalLiveSubsystem.generated.h"

class UDalInterpHost;

DECLARE_DYNAMIC_MULTICAST_DELEGATE_OneParam(FDalHostLifecycle, UDalInterpHost*, Host);
/** PendingEvents / PendingFlags: how many events and notify flags are queued, so Python can skip the calls that fetch them. */
DECLARE_DYNAMIC_MULTICAST_DELEGATE_FourParams(FDalHostTick, UDalInterpHost*, Host, float, DeltaSeconds, int32, PendingEvents, int32, PendingFlags);

UCLASS()
class DALLIVE_API UDalLiveSubsystem : public UEngineSubsystem
{
	GENERATED_BODY()

public:
	static UDalLiveSubsystem* Get();

	/** A host component began play (game thread). Python loads the program and keeps a session for it. */
	UPROPERTY(BlueprintAssignable, Category = "DAL")
	FDalHostLifecycle OnHostBegin;

	/** A host component ended play. */
	UPROPERTY(BlueprintAssignable, Category = "DAL")
	FDalHostLifecycle OnHostEnd;

	/** Once per tick of a host component, in its tick group: Python samples, updates and executes the commands. */
	UPROPERTY(BlueprintAssignable, Category = "DAL")
	FDalHostTick OnHostTick;

	/** Python sets this after it has bound the delegates; hosts that began earlier are found with GetActiveHosts. */
	UFUNCTION(BlueprintCallable, Category = "DAL")
	void SetPythonReady(bool bReady);

	UFUNCTION(BlueprintPure, Category = "DAL")
	bool IsPythonReady() const { return bPythonReady; }

	/** Host components that are playing now (a session may have to be created for them when Python connects late). */
	UFUNCTION(BlueprintCallable, Category = "DAL")
	TArray<UDalInterpHost*> GetActiveHosts() const;

	/** Editor only: a toast in the corner of the editor.  No-op in a game and in commandlets. */
	UFUNCTION(BlueprintCallable, Category = "DAL")
	void ShowToast(const FString& Text, EDalLiveMessageLevel Level, float Seconds);

	/** Editor only: a line in the Message Log page "DAL Live". */
	UFUNCTION(BlueprintCallable, Category = "DAL")
	void AddMessageLogLine(const FString& Text, EDalLiveMessageLevel Level);

	// ---- called by UDalInterpHost ----
	void RegisterHost(UDalInterpHost* Host);
	void UnregisterHost(UDalInterpHost* Host);

	virtual void Deinitialize() override;

private:
	TArray<TWeakObjectPtr<UDalInterpHost>> Hosts;
	bool bPythonReady = false;
};
