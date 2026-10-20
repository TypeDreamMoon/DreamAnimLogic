// AlsLeanLibrary.h -- C++ side of the extern fn declared in als_lean.dal.
// Hand-written (not generated). Keep it in step with als_lean_standins.py.
// Extern functions run on the animation worker thread: they must be pure and thread safe.
#pragma once

#include "CoreMinimal.h"

namespace AlsLeanLibrary
{
	/** Vertical velocity (cm/s) -> in-air lean multiplier: rising leans back, falling leans forward. */
	inline float InAirLeanCurve(const float VerticalVelocity)
	{
		static constexpr float Keys[][2] = {
			{-600.0f, 1.0f}, {-200.0f, 0.6f}, {0.0f, 0.0f}, {200.0f, -0.6f}, {600.0f, -1.0f},
		};
		constexpr int32 Count = UE_ARRAY_COUNT(Keys);

		if (VerticalVelocity <= Keys[0][0])
		{
			return Keys[0][1];
		}
		for (int32 Index = 1; Index < Count; Index++)
		{
			if (VerticalVelocity <= Keys[Index][0])
			{
				const float Alpha = (VerticalVelocity - Keys[Index - 1][0]) / (Keys[Index][0] - Keys[Index - 1][0]);
				return Keys[Index - 1][1] + (Keys[Index][1] - Keys[Index - 1][1]) * Alpha;
			}
		}
		return Keys[Count - 1][1];
	}
}
