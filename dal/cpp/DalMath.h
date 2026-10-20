// DalMath.h -- math library of the DAL (Dream Anim Logic) C++ backend.
//
// Part of the dal compiler package; copied next to the generated sources by `python -m dal compile --cpp`.
// Every function maps a DAL built-in to Unreal Engine math (FMath, FRotator, FQuat, TVector) with the
// behaviour the Python runtime (dal/runtime.py) reproduces.  Where UE has no float overload the
// function is written out here following the documented UE semantics.  Target: UE 5.8, float precision.
//
// Do not edit; regenerate with the compiler.
#pragma once

#include "CoreMinimal.h"

#define DAL_MATH_VERSION 1

namespace DalMath
{
	// ---- integer helpers --------------------------------------------------------------------

	/** x / 0 is 0 (DAL never traps); INT_MIN / -1 wraps instead of trapping. */
	[[nodiscard]] inline int32 Div(int32 A, int32 B)
	{
		if (B == 0)
		{
			return 0;
		}
		if (B == -1)
		{
			return static_cast<int32>(0u - static_cast<uint32>(A));
		}
		return A / B;
	}

	/** x % 0 is 0; the result has the sign of the dividend, as in C++. */
	[[nodiscard]] inline int32 Mod(int32 A, int32 B)
	{
		if (B == 0 || B == -1)
		{
			return 0;
		}
		return A % B;
	}

	/** float division: x / 0 is 0 (never inf or NaN from the operator). */
	[[nodiscard]] inline float Div(float A, float B)
	{
		return B != 0.0f ? A / B : 0.0f;
	}

	[[nodiscard]] inline FVector2f Div(const FVector2f& V, float S)
	{
		return S != 0.0f ? V / S : FVector2f(0.0f, 0.0f);
	}

	[[nodiscard]] inline FVector3f Div(const FVector3f& V, float S)
	{
		return S != 0.0f ? V / S : FVector3f(0.0f, 0.0f, 0.0f);
	}

	// ---- scalar -----------------------------------------------------------------------------

	[[nodiscard]] inline int32 Abs(int32 X) { return X < 0 ? static_cast<int32>(0u - static_cast<uint32>(X)) : X; }
	[[nodiscard]] inline float Abs(float X) { return FMath::Abs(X); }
	[[nodiscard]] inline int32 Sign(int32 X) { return FMath::Sign(X); }
	[[nodiscard]] inline float Sign(float X) { return FMath::Sign(X); }
	[[nodiscard]] inline int32 Min(int32 A, int32 B) { return FMath::Min(A, B); }
	[[nodiscard]] inline float Min(float A, float B) { return FMath::Min(A, B); }
	[[nodiscard]] inline int32 Max(int32 A, int32 B) { return FMath::Max(A, B); }
	[[nodiscard]] inline float Max(float A, float B) { return FMath::Max(A, B); }
	[[nodiscard]] inline int32 Clamp(int32 X, int32 Lo, int32 Hi) { return FMath::Clamp(X, Lo, Hi); }
	[[nodiscard]] inline float Clamp(float X, float Lo, float Hi) { return FMath::Clamp(X, Lo, Hi); }
	[[nodiscard]] inline float Saturate(float X) { return FMath::Clamp(X, 0.0f, 1.0f); }
	[[nodiscard]] inline int32 Square(int32 X) { return static_cast<int32>(static_cast<uint32>(X) * static_cast<uint32>(X)); }
	[[nodiscard]] inline float Square(float X) { return FMath::Square(X); }

	[[nodiscard]] inline float Lerp(float A, float B, float T) { return FMath::Lerp(A, B, T); }
	[[nodiscard]] inline FVector2f Lerp(const FVector2f& A, const FVector2f& B, float T) { return A + (B - A) * T; }
	[[nodiscard]] inline FVector3f Lerp(const FVector3f& A, const FVector3f& B, float T) { return A + (B - A) * T; }

	[[nodiscard]] inline float Floor(float X) { return FMath::FloorToFloat(X); }
	[[nodiscard]] inline float Ceil(float X) { return FMath::CeilToFloat(X); }
	[[nodiscard]] inline float Round(float X) { return FMath::RoundToFloat(X); }
	[[nodiscard]] inline float Trunc(float X) { return FMath::TruncToFloat(X); }
	[[nodiscard]] inline float Frac(float X) { return FMath::Frac(X); }

	/** float -> int32 conversions saturate and map NaN to 0 (a plain C++ cast would be undefined). */
	[[nodiscard]] inline int32 SaturateToInt(float X)
	{
		if (X != X)
		{
			return 0;
		}
		if (X >= 2147483648.0f)
		{
			return MAX_int32;
		}
		if (X <= -2147483648.0f)
		{
			return MIN_int32;
		}
		return static_cast<int32>(X);
	}

	[[nodiscard]] inline int32 ToInt(float X) { return SaturateToInt(FMath::TruncToFloat(X)); }
	[[nodiscard]] inline int32 FloorToInt(float X) { return SaturateToInt(FMath::FloorToFloat(X)); }
	[[nodiscard]] inline int32 CeilToInt(float X) { return SaturateToInt(FMath::CeilToFloat(X)); }
	[[nodiscard]] inline int32 RoundToInt(float X) { return SaturateToInt(FMath::FloorToFloat(X + 0.5f)); }

	[[nodiscard]] inline float Fmod(float X, float Y) { return FMath::Fmod(X, Y); }
	[[nodiscard]] inline float Sqrt(float X) { return FMath::Sqrt(X); }
	[[nodiscard]] inline float InvSqrt(float X) { return FMath::InvSqrt(X); }
	[[nodiscard]] inline float Pow(float A, float B) { return FMath::Pow(A, B); }
	[[nodiscard]] inline float Exp(float X) { return FMath::Exp(X); }
	[[nodiscard]] inline float Log(float X) { return FMath::Loge(X); }
	[[nodiscard]] inline float Sin(float X) { return FMath::Sin(X); }
	[[nodiscard]] inline float Cos(float X) { return FMath::Cos(X); }
	[[nodiscard]] inline float Tan(float X) { return FMath::Tan(X); }
	[[nodiscard]] inline float Asin(float X) { return FMath::Asin(X); }
	[[nodiscard]] inline float Acos(float X) { return FMath::Acos(X); }
	[[nodiscard]] inline float Atan(float X) { return FMath::Atan(X); }
	[[nodiscard]] inline float Atan2(float Y, float X) { return FMath::Atan2(Y, X); }
	[[nodiscard]] inline float DegToRad(float X) { return FMath::DegreesToRadians(X); }
	[[nodiscard]] inline float RadToDeg(float X) { return FMath::RadiansToDegrees(X); }
	[[nodiscard]] inline float InvExpApprox(float X) { return FMath::InvExpApprox(X); }

	[[nodiscard]] inline bool IsNearlyZero(float X) { return FMath::IsNearlyZero(X); }
	[[nodiscard]] inline bool IsNearlyZero(float X, float Tolerance) { return FMath::IsNearlyZero(X, Tolerance); }
	[[nodiscard]] inline bool IsNearlyEqual(float A, float B) { return FMath::IsNearlyEqual(A, B); }
	[[nodiscard]] inline bool IsNearlyEqual(float A, float B, float Tolerance) { return FMath::IsNearlyEqual(A, B, Tolerance); }

	// ---- angles and ranges -------------------------------------------------------------------

	[[nodiscard]] inline float UnwindDegrees(float A) { return FMath::UnwindDegrees(A); }
	[[nodiscard]] inline float NormalizeAxis(float A) { return FRotator3f::NormalizeAxis(A); }
	[[nodiscard]] inline float ClampAxis(float A) { return FRotator3f::ClampAxis(A); }
	[[nodiscard]] inline float DeltaAngle(float From, float To) { return FMath::FindDeltaAngleDegrees(From, To); }
	[[nodiscard]] inline float ClampAngle(float Angle, float Lo, float Hi) { return FMath::ClampAngle(Angle, Lo, Hi); }
	[[nodiscard]] inline float RangePct(float Lo, float Hi, float X) { return FMath::GetRangePct(Lo, Hi, X); }

	[[nodiscard]] inline float MapRangeClamped(float InLo, float InHi, float OutLo, float OutHi, float X)
	{
		const float Pct = FMath::Clamp(FMath::GetRangePct(InLo, InHi, X), 0.0f, 1.0f);
		return FMath::Lerp(OutLo, OutHi, Pct);
	}

	[[nodiscard]] inline float MapRange(float InLo, float InHi, float OutLo, float OutHi, float X)
	{
		return FMath::Lerp(OutLo, OutHi, FMath::GetRangePct(InLo, InHi, X));
	}

	// ---- vectors -----------------------------------------------------------------------------

	[[nodiscard]] inline float Length(const FVector2f& V) { return V.Size(); }
	[[nodiscard]] inline float Length(const FVector3f& V) { return V.Size(); }
	[[nodiscard]] inline float LengthSq(const FVector2f& V) { return V.SizeSquared(); }
	[[nodiscard]] inline float LengthSq(const FVector3f& V) { return V.SizeSquared(); }
	[[nodiscard]] inline FVector2f Normalize(const FVector2f& V) { return V.GetSafeNormal(); }
	[[nodiscard]] inline FVector3f Normalize(const FVector3f& V) { return V.GetSafeNormal(); }
	[[nodiscard]] inline FQuat4f Normalize(const FQuat4f& Q) { return Q.GetNormalized(); }
	[[nodiscard]] inline float Dot(const FVector2f& A, const FVector2f& B) { return FVector2f::DotProduct(A, B); }
	[[nodiscard]] inline float Dot(const FVector3f& A, const FVector3f& B) { return FVector3f::DotProduct(A, B); }
	[[nodiscard]] inline FVector3f Cross(const FVector3f& A, const FVector3f& B) { return FVector3f::CrossProduct(A, B); }
	[[nodiscard]] inline float Distance(const FVector2f& A, const FVector2f& B) { return (A - B).Size(); }
	[[nodiscard]] inline float Distance(const FVector3f& A, const FVector3f& B) { return FVector3f::Dist(A, B); }
	[[nodiscard]] inline FVector2f ClampLength(const FVector2f& V, float MaxSize) { return V.GetClampedToMaxSize(MaxSize); }
	[[nodiscard]] inline FVector3f ClampLength(const FVector3f& V, float MaxSize) { return V.GetClampedToMaxSize(MaxSize); }

	[[nodiscard]] inline FVector3f MakeVec3(const FVector2f& XY, float Z) { return FVector3f(XY.X, XY.Y, Z); }

	/** v.xy, v.zx ... : component picks by index (0 = x, 1 = y, 2 = z). */
	template <int A, int B>
	[[nodiscard]] inline FVector2f Swizzle2(const FVector2f& V) { return FVector2f(V[A], V[B]); }
	template <int A, int B>
	[[nodiscard]] inline FVector2f Swizzle2(const FVector3f& V) { return FVector2f(V[A], V[B]); }
	template <int A, int B, int C>
	[[nodiscard]] inline FVector3f Swizzle3(const FVector2f& V) { return FVector3f(V[A], V[B], V[C]); }
	template <int A, int B, int C>
	[[nodiscard]] inline FVector3f Swizzle3(const FVector3f& V) { return FVector3f(V[A], V[B], V[C]); }

	// ---- rotations ---------------------------------------------------------------------------

	[[nodiscard]] inline FQuat4f ToQuat(const FRotator3f& R) { return R.Quaternion(); }
	[[nodiscard]] inline FRotator3f ToRotator(const FQuat4f& Q) { return Q.Rotator(); }
	[[nodiscard]] inline FQuat4f QuatInverse(const FQuat4f& Q) { return Q.Inverse(); }
	[[nodiscard]] inline FQuat4f Slerp(const FQuat4f& A, const FQuat4f& B, float T) { return FQuat4f::Slerp(A, B, T); }
	[[nodiscard]] inline FVector3f Rotate(const FQuat4f& Q, const FVector3f& V) { return Q.RotateVector(V); }
	[[nodiscard]] inline FVector3f Rotate(const FRotator3f& R, const FVector3f& V) { return R.Quaternion().RotateVector(V); }
	[[nodiscard]] inline FVector3f Unrotate(const FQuat4f& Q, const FVector3f& V) { return Q.UnrotateVector(V); }
	[[nodiscard]] inline FVector3f Unrotate(const FRotator3f& R, const FVector3f& V) { return R.Quaternion().UnrotateVector(V); }
	[[nodiscard]] inline FRotator3f NormalizeRotator(const FRotator3f& R) { return R.GetNormalized(); }
	[[nodiscard]] inline FRotator3f LookAt(const FVector3f& From, const FVector3f& To) { return (To - From).ToOrientationRotator(); }

	[[nodiscard]] inline float AngleBetween(const FVector3f& A, const FVector3f& B)
	{
		const FVector3f NA = A.GetSafeNormal();
		const FVector3f NB = B.GetSafeNormal();
		if (NA.IsZero() || NB.IsZero())
		{
			return 0.0f;
		}
		return FMath::RadiansToDegrees(FMath::Acos(FVector3f::DotProduct(NA, NB)));
	}

	[[nodiscard]] inline FRotator3f DeltaRotator(const FRotator3f& A, const FRotator3f& B)
	{
		const FRotator3f Delta = A - B;
		return Delta.GetNormalized();
	}

	// ---- interpolation -----------------------------------------------------------------------

	[[nodiscard]] inline float InterpTo(float Current, float Target, float Dt, float Speed)
	{
		return FMath::FInterpTo(Current, Target, Dt, Speed);
	}

	[[nodiscard]] inline FVector2f InterpTo(const FVector2f& Current, const FVector2f& Target, float Dt, float Speed)
	{
		return FMath::Vector2DInterpTo(Current, Target, Dt, Speed);
	}

	// FMath::VInterpTo exists for FVector (double) only, so the float version follows its documented behaviour.
	[[nodiscard]] inline FVector3f InterpTo(const FVector3f& Current, const FVector3f& Target, float Dt, float Speed)
	{
		if (Speed <= 0.0f)
		{
			return Target;
		}
		const FVector3f Dist = Target - Current;
		if (Dist.SizeSquared() < UE_KINDA_SMALL_NUMBER)
		{
			return Target;
		}
		return Current + Dist * FMath::Clamp(Dt * Speed, 0.0f, 1.0f);
	}

	// FMath::RInterpTo exists for FRotator (double) only; same behaviour in float.
	[[nodiscard]] inline FRotator3f InterpTo(const FRotator3f& Current, const FRotator3f& Target, float Dt, float Speed)
	{
		if (Dt == 0.0f || Current == Target)
		{
			return Current;
		}
		if (Speed <= 0.0f)
		{
			return Target;
		}
		const FRotator3f Delta = (Target - Current).GetNormalized();
		if (Delta.IsNearlyZero())
		{
			return Target;
		}
		const FRotator3f Move = Delta * FMath::Clamp(Speed * Dt, 0.0f, 1.0f);
		return (Current + Move).GetNormalized();
	}

	[[nodiscard]] inline FQuat4f InterpTo(const FQuat4f& Current, const FQuat4f& Target, float Dt, float Speed)
	{
		return FMath::QInterpTo(Current, Target, Dt, Speed);
	}

	[[nodiscard]] inline float InterpConstantTo(float Current, float Target, float Dt, float Speed)
	{
		return FMath::FInterpConstantTo(Current, Target, Dt, Speed);
	}

	[[nodiscard]] inline FVector2f InterpConstantTo(const FVector2f& Current, const FVector2f& Target, float Dt, float Speed)
	{
		const FVector2f Delta = Target - Current;
		const float DeltaM = Delta.Size();
		const float MaxStep = Speed * Dt;
		if (DeltaM > MaxStep)
		{
			return MaxStep > 0.0f ? Current + (Delta / DeltaM) * MaxStep : Current;
		}
		return Target;
	}

	[[nodiscard]] inline FVector3f InterpConstantTo(const FVector3f& Current, const FVector3f& Target, float Dt, float Speed)
	{
		const FVector3f Delta = Target - Current;
		const float DeltaM = Delta.Size();
		const float MaxStep = Speed * Dt;
		if (DeltaM > MaxStep)
		{
			return MaxStep > 0.0f ? Current + (Delta / DeltaM) * MaxStep : Current;
		}
		return Target;
	}

	[[nodiscard]] inline FRotator3f InterpConstantTo(const FRotator3f& Current, const FRotator3f& Target, float Dt, float Speed)
	{
		if (Dt == 0.0f || Current == Target)
		{
			return Current;
		}
		if (Speed <= 0.0f)
		{
			return Target;
		}
		const float Step = Speed * Dt;
		const FRotator3f Delta = (Target - Current).GetNormalized();
		FRotator3f Result = Current;
		Result.Pitch += FMath::Clamp(Delta.Pitch, -Step, Step);
		Result.Yaw += FMath::Clamp(Delta.Yaw, -Step, Step);
		Result.Roll += FMath::Clamp(Delta.Roll, -Step, Step);
		return Result.GetNormalized();
	}

	[[nodiscard]] inline FQuat4f InterpConstantTo(const FQuat4f& Current, const FQuat4f& Target, float Dt, float Speed)
	{
		return FMath::QInterpConstantTo(Current, Target, Dt, Speed);
	}

	[[nodiscard]] inline float ExponentialSmoothing(float Current, float Target, float Dt, float SmoothingTime)
	{
		FMath::ExponentialSmoothingApprox(Current, Target, Dt, SmoothingTime);
		return Current;
	}

	[[nodiscard]] inline FVector2f ExponentialSmoothing(FVector2f Current, const FVector2f& Target, float Dt, float SmoothingTime)
	{
		FMath::ExponentialSmoothingApprox(Current, Target, Dt, SmoothingTime);
		return Current;
	}

	[[nodiscard]] inline FVector3f ExponentialSmoothing(FVector3f Current, const FVector3f& Target, float Dt, float SmoothingTime)
	{
		FMath::ExponentialSmoothingApprox(Current, Target, Dt, SmoothingTime);
		return Current;
	}
} // namespace DalMath
