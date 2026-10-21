#include "DalTimedComponents.h"

#include "HAL/PlatformTime.h"

namespace
{
	void Record(double& Total, double& Max, int32& Calls, TArray<float>& Samples, const uint64 Start)
	{
		const double Micros = FPlatformTime::ToMilliseconds64(FPlatformTime::Cycles64() - Start) * 1000.0;
		Total += Micros;
		Max = FMath::Max(Max, Micros);
		++Calls;
		if (Samples.Num() < 20000)
		{
			Samples.Add(static_cast<float>(Micros));
		}
	}
}

void UDalTimedMelee::TickComponent(const float DeltaTime, const ELevelTick TickType, FActorComponentTickFunction* ThisTickFunction)
{
	const uint64 Start = FPlatformTime::Cycles64();
	Super::TickComponent(DeltaTime, TickType, ThisTickFunction);
	Record(TotalMicros, MaxMicros, Calls, Samples, Start);
}

void UDalTimedMelee::GetTickStats(double& OutTotalMicros, double& OutMaxMicros, int32& OutCalls) const
{
	OutTotalMicros = TotalMicros;
	OutMaxMicros = MaxMicros;
	OutCalls = Calls;
}

void UDalTimedMelee::ResetTickStats()
{
	Samples.Reset();
	TotalMicros = 0.0;
	MaxMicros = 0.0;
	Calls = 0;
}

void UDalTimedLockOn::TickComponent(const float DeltaTime, const ELevelTick TickType, FActorComponentTickFunction* ThisTickFunction)
{
	const uint64 Start = FPlatformTime::Cycles64();
	Super::TickComponent(DeltaTime, TickType, ThisTickFunction);
	Record(TotalMicros, MaxMicros, Calls, Samples, Start);
}

void UDalTimedLockOn::GetTickStats(double& OutTotalMicros, double& OutMaxMicros, int32& OutCalls) const
{
	OutTotalMicros = TotalMicros;
	OutMaxMicros = MaxMicros;
	OutCalls = Calls;
}

void UDalTimedLockOn::ResetTickStats()
{
	Samples.Reset();
	TotalMicros = 0.0;
	MaxMicros = 0.0;
	Calls = 0;
}
