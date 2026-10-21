// The generated C++ components with a stopwatch around TickComponent: the compiled side of the per-tick cost comparison.

#pragma once

#include "CoreMinimal.h"
#include "Dal/LockOnCamera.h"
#include "Dal/MeleeCombo.h"
#include "DalTimedComponents.generated.h"

UCLASS(ClassGroup = (Dal), Meta = (BlueprintSpawnableComponent))
class DALLIVEHOST_API UDalTimedMelee : public UMeleeCombo
{
	GENERATED_BODY()

public:
	virtual void TickComponent(float DeltaTime, ELevelTick TickType, FActorComponentTickFunction* ThisTickFunction) override;

	UFUNCTION(BlueprintCallable, Category = "DalTest")
	void GetTickStats(double& OutTotalMicros, double& OutMaxMicros, int32& OutCalls) const;

	UFUNCTION(BlueprintCallable, Category = "DalTest")
	void ResetTickStats();

	UFUNCTION(BlueprintCallable, Category = "DalTest")
	TArray<float> GetTickSamples() const { return Samples; }

private:
	TArray<float> Samples;
	double TotalMicros = 0.0;
	double MaxMicros = 0.0;
	int32 Calls = 0;
};

UCLASS(ClassGroup = (Dal), Meta = (BlueprintSpawnableComponent))
class DALLIVEHOST_API UDalTimedLockOn : public ULockOnCamera
{
	GENERATED_BODY()

public:
	virtual void TickComponent(float DeltaTime, ELevelTick TickType, FActorComponentTickFunction* ThisTickFunction) override;

	UFUNCTION(BlueprintCallable, Category = "DalTest")
	void GetTickStats(double& OutTotalMicros, double& OutMaxMicros, int32& OutCalls) const;

	UFUNCTION(BlueprintCallable, Category = "DalTest")
	void ResetTickStats();

	UFUNCTION(BlueprintCallable, Category = "DalTest")
	TArray<float> GetTickSamples() const { return Samples; }

private:
	TArray<float> Samples;
	double TotalMicros = 0.0;
	double MaxMicros = 0.0;
	int32 Calls = 0;
};
