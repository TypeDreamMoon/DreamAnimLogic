// Minimal stand-in for Unreal's CoreMinimal.h -- ONLY to compile and run generated DAL code outside the engine.
//
// This is not Unreal code. It re-implements, in plain float/double C++, the small part of the UE 5 math API
// that dal's generated sources and DalMath.h use, with the signatures and behaviours of the UE 5.8 headers
// (UnrealMathUtility.h, Vector.h, Vector2D.h, Rotator.h, Quat.h) as read from the engine source. It lets
// tests/test_cpp_compile.py check that the generated C++ is well-formed and computes what the Python
// backend computes. It cannot prove the code builds inside the real engine.
#pragma once

#include <algorithm>
#include <cmath>
#include <cstdint>
#include <cstdio>
#include <cstdlib>
#include <limits>
#include <type_traits>

using int32 = std::int32_t;
using uint8 = std::uint8_t;
using uint32 = std::uint32_t;
using int64 = std::int64_t;

#define MAX_int32 INT32_MAX
#define MIN_int32 INT32_MIN
#define UE_PI (3.1415926535897932f)
#define UE_TWO_PI (6.2831853071795864f)
#define UE_HALF_PI (1.57079632679f)
#define UE_LN2 (0.69314718056f)
#define UE_SMALL_NUMBER (1.e-8f)
#define UE_KINDA_SMALL_NUMBER (1.e-4f)
#define UE_ARRAY_COUNT(array) (sizeof(array) / sizeof((array)[0]))

// reflection macros are empty outside UHT
#define UCLASS(...)
#define UENUM(...)
#define UPROPERTY(...)
#define UFUNCTION(...)
#define GENERATED_BODY() public: typedef DAL_STUB_SUPER Super;
#define UE_INLINE_GENERATED_CPP_BY_NAME(name) "stub_empty.h"

template <class T>
struct TObjectPtr
{
	T* Ptr = nullptr;
	TObjectPtr() = default;
	TObjectPtr(T* In) : Ptr(In) {}
	template <class U, class = std::enable_if_t<std::is_convertible_v<U*, T*>>>
	TObjectPtr(const TObjectPtr<U>& Other) : Ptr(Other.Ptr) {}
	TObjectPtr& operator=(T* In) { Ptr = In; return *this; }
	operator T*() const { return Ptr; }
	T* operator->() const { return Ptr; }
	bool operator==(std::nullptr_t) const { return Ptr == nullptr; }
	bool operator!=(std::nullptr_t) const { return Ptr != nullptr; }
};

template <class E>
struct TEnumAsByte
{
	uint8 Value = 0;
	TEnumAsByte() = default;
	TEnumAsByte(E In) : Value(static_cast<uint8>(In)) {}
	TEnumAsByte& operator=(E In) { Value = static_cast<uint8>(In); return *this; }
	operator E() const { return static_cast<E>(Value); }
	E GetValue() const { return static_cast<E>(Value); }
};

// ---- FMath -------------------------------------------------------------------------------------

namespace UE::Math
{
	template <class T> struct TVector2;
	template <class T> struct TVector;
	template <class T> struct TRotator;
	template <class T> struct TQuat;
}

using FVector2f = UE::Math::TVector2<float>;
using FVector2D = UE::Math::TVector2<double>;
using FVector3f = UE::Math::TVector<float>;
using FVector = UE::Math::TVector<double>;
using FRotator3f = UE::Math::TRotator<float>;
using FRotator = UE::Math::TRotator<double>;
using FQuat4f = UE::Math::TQuat<float>;
using FQuat = UE::Math::TQuat<double>;

struct FMath
{
	template <class T> static constexpr T Abs(T A) { return (A < (T)0) ? (T)-A : A; }
	template <class T> static constexpr T Sign(T A) { return (A > (T)0) ? (T)1 : ((A < (T)0) ? (T)-1 : (T)0); }
	template <class T> static constexpr T Max(T A, T B) { return (B < A) ? A : B; }
	template <class T> static constexpr T Min(T A, T B) { return (A < B) ? A : B; }
	template <class T> static constexpr T Clamp(T X, T Lo, T Hi) { return Max(Min(X, Hi), Lo); }
	template <class T> static constexpr T Square(T A) { return A * A; }
	template <class T, class U> static constexpr T Lerp(const T& A, const T& B, const U& Alpha) { return (T)(A + Alpha * (B - A)); }

	static float FloorToFloat(float F) { return floorf(F); }
	static float CeilToFloat(float F) { return ceilf(F); }
	static float TruncToFloat(float F) { return truncf(F); }
	static float RoundToFloat(float F) { return FloorToFloat(F + 0.5f); }
	static float Frac(float F) { return F - FloorToFloat(F); }
	static float Fmod(float X, float Y) { return FMath::Abs(Y) <= UE_SMALL_NUMBER ? 0.0f : fmodf(X, Y); }
	static float Sqrt(float F) { return sqrtf(F); }
	static float InvSqrt(float F) { return 1.0f / sqrtf(F); }
	static float Pow(float A, float B) { return powf(A, B); }
	static float Exp(float F) { return expf(F); }
	static float Loge(float F) { return logf(F); }
	static float Sin(float F) { return sinf(F); }
	static float Cos(float F) { return cosf(F); }
	static float Tan(float F) { return tanf(F); }
	static float Asin(float F) { return asinf((F < -1.f) ? -1.f : ((F < 1.f) ? F : 1.f)); }
	static float Acos(float F) { return acosf((F < -1.f) ? -1.f : ((F < 1.f) ? F : 1.f)); }
	static float Atan(float F) { return atanf(F); }
	static float Atan2(float Y, float X) { return (X == 0.0f && Y == 0.0f) ? 0.0f : atan2f(Y, X); }
	static float DegreesToRadians(float D) { return D * (UE_PI / 180.f); }
	static float RadiansToDegrees(float R) { return R * (180.f / UE_PI); }

	static int32 TruncToInt(float F) { return (int32)F; }
	static int32 FloorToInt(float F) { int32 I = (int32)F; I -= ((float)I > F); return I; }

	template <class T> static constexpr T InvExpApprox(T X)
	{
		constexpr T A(1.00746054f);
		constexpr T B(0.45053901f);
		constexpr T C(0.25724632f);
		return 1 / (1 + A * X + B * X * X + C * X * X * X);
	}

	static bool IsNearlyZero(float V, float Tol = UE_SMALL_NUMBER) { return Abs(V) <= Tol; }
	static bool IsNearlyEqual(float A, float B, float Tol = UE_SMALL_NUMBER) { return Abs(A - B) <= Tol; }

	static float UnwindDegrees(float A)
	{
		while (A > 180.f) { A -= 360.f; }
		while (A < -180.f) { A += 360.f; }
		return A;
	}

	static float Wrap(float X, float Min_, float Max_)
	{
		float Size = Max_ - Min_;
		if (Size == 0) { return Max_; }
		float EndVal = X;
		if (EndVal < Min_)
		{
			float Mod = Fmod(Min_ - EndVal, Size);
			EndVal = (Mod != 0.0f) ? Max_ - Mod : Min_;
		}
		else if (EndVal > Max_)
		{
			float Mod = Fmod(EndVal - Max_, Size);
			EndVal = (Mod != 0.0f) ? Min_ + Mod : Max_;
		}
		return EndVal;
	}

	static float FindDeltaAngleDegrees(float A1, float A2) { return Wrap(A2 - A1, -180.f, 180.f); }
	static float ClampAngle(float Angle, float Lo, float Hi);
	static float GetRangePct(float Lo, float Hi, float V)
	{
		const float Divisor = Hi - Lo;
		if (IsNearlyZero(Divisor)) { return (V >= Hi) ? 1.0f : 0.0f; }
		return (V - Lo) / Divisor;
	}

	static float FInterpTo(float Current, float Target, float Dt, float Speed)
	{
		if (Speed <= 0.f) { return Target; }
		const float Dist = Target - Current;
		if (Square(Dist) < UE_SMALL_NUMBER) { return Target; }
		return Current + Dist * Clamp<float>(Dt * Speed, 0.f, 1.f);
	}

	static float FInterpConstantTo(float Current, float Target, float Dt, float Speed)
	{
		const float Dist = Target - Current;
		if (Square(Dist) < UE_SMALL_NUMBER) { return Target; }
		const float Step = Speed * Dt;
		return Current + Clamp(Dist, -Step, Step);
	}

	template <class T> static void ExponentialSmoothingApprox(T& InOut, const T& Target, const float Dt, const float SmoothingTime)
	{
		if (SmoothingTime > UE_KINDA_SMALL_NUMBER)
		{
			const float A = Dt / SmoothingTime;
			float E = InvExpApprox(A);
			InOut = Target + (InOut - Target) * E;
		}
		else
		{
			InOut = Target;
		}
	}

	template <class T> static UE::Math::TVector2<T> Vector2DInterpTo(const UE::Math::TVector2<T>& Current, const UE::Math::TVector2<T>& Target, float Dt, float Speed);
	template <class T> static UE::Math::TQuat<T> QInterpTo(const UE::Math::TQuat<T>& Current, const UE::Math::TQuat<T>& Target, float Dt, float Speed);
	template <class T> static UE::Math::TQuat<T> QInterpConstantTo(const UE::Math::TQuat<T>& Current, const UE::Math::TQuat<T>& Target, float Dt, float Speed);
};

// ---- vectors, rotators, quaternions ------------------------------------------------------------

namespace UE::Math
{
	template <class T>
	struct TVector2
	{
		T X = 0, Y = 0;
		static const TVector2 ZeroVector;
		TVector2() = default;
		TVector2(T InX, T InY) : X(InX), Y(InY) {}
		template <class U, class = std::enable_if_t<!std::is_same_v<T, U>>>
		explicit TVector2(const TVector2<U>& V) : X((T)V.X), Y((T)V.Y) {}
		TVector2 operator+(const TVector2& V) const { return TVector2(X + V.X, Y + V.Y); }
		TVector2 operator-(const TVector2& V) const { return TVector2(X - V.X, Y - V.Y); }
		TVector2 operator*(T S) const { return TVector2(X * S, Y * S); }
		TVector2 operator*(const TVector2& V) const { return TVector2(X * V.X, Y * V.Y); }
		TVector2 operator/(T S) const { const T R = 1 / S; return TVector2(X * R, Y * R); }
		TVector2 operator-() const { return TVector2(-X, -Y); }
		T operator[](int32 I) const { return I == 0 ? X : Y; }
		T Size() const { return std::sqrt(X * X + Y * Y); }
		T SizeSquared() const { return X * X + Y * Y; }
		TVector2 GetSafeNormal(T Tolerance = UE_SMALL_NUMBER) const
		{
			const T SquareSum = X * X + Y * Y;
			if (SquareSum > Tolerance) { const T Scale = FMath::InvSqrt(SquareSum); return TVector2(X * Scale, Y * Scale); }
			return TVector2(0, 0);
		}
		TVector2 GetClampedToMaxSize(T MaxSize) const
		{
			if (MaxSize < UE_KINDA_SMALL_NUMBER) { return TVector2(0, 0); }
			const T VSq = SizeSquared();
			if (VSq > FMath::Square(MaxSize)) { const T Scale = MaxSize * FMath::InvSqrt(VSq); return TVector2(X * Scale, Y * Scale); }
			return *this;
		}
		static T DotProduct(const TVector2& A, const TVector2& B) { return A.X * B.X + A.Y * B.Y; }
	};
	template <class T> const TVector2<T> TVector2<T>::ZeroVector(0, 0);
	template <class T> TVector2<T> operator*(T S, const TVector2<T>& V) { return V * S; }

	template <class T>
	struct TVector
	{
		T X = 0, Y = 0, Z = 0;
		static const TVector ZeroVector;
		TVector() = default;
		TVector(T InX, T InY, T InZ) : X(InX), Y(InY), Z(InZ) {}
		template <class U, class = std::enable_if_t<!std::is_same_v<T, U>>>
		explicit TVector(const TVector<U>& V) : X((T)V.X), Y((T)V.Y), Z((T)V.Z) {}
		TVector operator+(const TVector& V) const { return TVector(X + V.X, Y + V.Y, Z + V.Z); }
		TVector operator-(const TVector& V) const { return TVector(X - V.X, Y - V.Y, Z - V.Z); }
		TVector operator*(T S) const { return TVector(X * S, Y * S, Z * S); }
		TVector operator*(const TVector& V) const { return TVector(X * V.X, Y * V.Y, Z * V.Z); }
		TVector operator/(T S) const { const T R = 1 / S; return TVector(X * R, Y * R, Z * R); }
		TVector operator-() const { return TVector(-X, -Y, -Z); }
		T operator[](int32 I) const { return I == 0 ? X : (I == 1 ? Y : Z); }
		T Size() const { return std::sqrt(X * X + Y * Y + Z * Z); }
		T SizeSquared() const { return X * X + Y * Y + Z * Z; }
		TVector GetSafeNormal(T Tolerance = UE_SMALL_NUMBER) const
		{
			const T SquareSum = X * X + Y * Y + Z * Z;
			if (SquareSum == 1.f) { return *this; }
			else if (SquareSum < Tolerance) { return TVector(0, 0, 0); }
			const T Scale = FMath::InvSqrt(SquareSum);
			return TVector(X * Scale, Y * Scale, Z * Scale);
		}
		TVector GetClampedToMaxSize(T MaxSize) const
		{
			if (MaxSize < UE_KINDA_SMALL_NUMBER) { return TVector(0, 0, 0); }
			const T VSq = SizeSquared();
			if (VSq > FMath::Square(MaxSize)) { const T Scale = MaxSize * FMath::InvSqrt(VSq); return TVector(X * Scale, Y * Scale, Z * Scale); }
			return *this;
		}
		static T DotProduct(const TVector& A, const TVector& B) { return A.X * B.X + A.Y * B.Y + A.Z * B.Z; }
		static TVector CrossProduct(const TVector& A, const TVector& B)
		{
			return TVector(A.Y * B.Z - A.Z * B.Y, A.Z * B.X - A.X * B.Z, A.X * B.Y - A.Y * B.X);
		}
		static T Dist(const TVector& A, const TVector& B) { return (A - B).Size(); }
	};
	template <class T> const TVector<T> TVector<T>::ZeroVector(0, 0, 0);
	template <class T> TVector<T> operator*(T S, const TVector<T>& V) { return V * S; }

	template <class T>
	struct TRotator
	{
		T Pitch = 0, Yaw = 0, Roll = 0;
		static const TRotator ZeroRotator;
		TRotator() = default;
		TRotator(T InPitch, T InYaw, T InRoll) : Pitch(InPitch), Yaw(InYaw), Roll(InRoll) {}
		template <class U, class = std::enable_if_t<!std::is_same_v<T, U>>>
		explicit TRotator(const TRotator<U>& R) : Pitch((T)R.Pitch), Yaw((T)R.Yaw), Roll((T)R.Roll) {}
		TRotator operator+(const TRotator& R) const { return TRotator(Pitch + R.Pitch, Yaw + R.Yaw, Roll + R.Roll); }
		TRotator operator-(const TRotator& R) const { return TRotator(Pitch - R.Pitch, Yaw - R.Yaw, Roll - R.Roll); }
		TRotator operator*(T S) const { return TRotator(Pitch * S, Yaw * S, Roll * S); }
		bool operator==(const TRotator& R) const { return Pitch == R.Pitch && Yaw == R.Yaw && Roll == R.Roll; }
		bool operator!=(const TRotator& R) const { return !(*this == R); }
		static T ClampAxis(T Angle)
		{
			Angle = FMath::Fmod(Angle, (T)360.0);
			if (Angle < (T)0.0) { Angle += (T)360.0; }
			return Angle;
		}
		static T NormalizeAxis(T Angle)
		{
			Angle = ClampAxis(Angle);
			if (Angle > (T)180.0) { Angle -= (T)360.0; }
			return Angle;
		}
		TRotator GetNormalized() const { return TRotator(NormalizeAxis(Pitch), NormalizeAxis(Yaw), NormalizeAxis(Roll)); }
		bool IsNearlyZero(T Tolerance = UE_KINDA_SMALL_NUMBER) const
		{
			return FMath::Abs(NormalizeAxis(Pitch)) <= Tolerance && FMath::Abs(NormalizeAxis(Yaw)) <= Tolerance && FMath::Abs(NormalizeAxis(Roll)) <= Tolerance;
		}
		TQuat<T> Quaternion() const;
	};
	template <class T> const TRotator<T> TRotator<T>::ZeroRotator(0, 0, 0);

	template <class T>
	struct TQuat
	{
		T X = 0, Y = 0, Z = 0, W = 1;
		static const TQuat Identity;
		TQuat() = default;
		TQuat(T InX, T InY, T InZ, T InW) : X(InX), Y(InY), Z(InZ), W(InW) {}
		template <class U, class = std::enable_if_t<!std::is_same_v<T, U>>>
		explicit TQuat(const TQuat<U>& Q) : X((T)Q.X), Y((T)Q.Y), Z((T)Q.Z), W((T)Q.W) {}
		TQuat operator*(const TQuat& Q) const
		{
			return TQuat(W * Q.X + X * Q.W + Y * Q.Z - Z * Q.Y, W * Q.Y - X * Q.Z + Y * Q.W + Z * Q.X,
			             W * Q.Z + X * Q.Y - Y * Q.X + Z * Q.W, W * Q.W - X * Q.X - Y * Q.Y - Z * Q.Z);
		}
		TQuat Inverse() const { return TQuat(-X, -Y, -Z, W); }
		TQuat GetNormalized(T Tolerance = UE_SMALL_NUMBER) const
		{
			const T SquareSum = X * X + Y * Y + Z * Z + W * W;
			if (SquareSum >= Tolerance) { const T Scale = FMath::InvSqrt(SquareSum); return TQuat(X * Scale, Y * Scale, Z * Scale, W * Scale); }
			return TQuat(0, 0, 0, 1);
		}
		TVector<T> RotateVector(TVector<T> V) const
		{
			const TVector<T> Q(X, Y, Z);
			const TVector<T> TT = TVector<T>::CrossProduct(Q, V) * (T)2;
			return V + (TT * W) + TVector<T>::CrossProduct(Q, TT);
		}
		TVector<T> UnrotateVector(TVector<T> V) const
		{
			const TVector<T> Q(-X, -Y, -Z);
			const TVector<T> TT = TVector<T>::CrossProduct(Q, V) * (T)2;
			return V + (TT * W) + TVector<T>::CrossProduct(Q, TT);
		}
		bool Equals(const TQuat& Q, T Tol = UE_KINDA_SMALL_NUMBER) const
		{
			return (FMath::Abs(X - Q.X) <= Tol && FMath::Abs(Y - Q.Y) <= Tol && FMath::Abs(Z - Q.Z) <= Tol && FMath::Abs(W - Q.W) <= Tol)
				|| (FMath::Abs(X + Q.X) <= Tol && FMath::Abs(Y + Q.Y) <= Tol && FMath::Abs(Z + Q.Z) <= Tol && FMath::Abs(W + Q.W) <= Tol);
		}
		T AngularDistance(const TQuat& Q) const
		{
			T Inner = X * Q.X + Y * Q.Y + Z * Q.Z + W * Q.W;
			return FMath::Acos((2 * Inner * Inner) - 1.f);
		}
		static TQuat Slerp(const TQuat& A, const TQuat& B, T Alpha)
		{
			T Cosom = A.X * B.X + A.Y * B.Y + A.Z * B.Z + A.W * B.W;
			const T Sgn = (Cosom >= 0) ? (T)1 : (T)-1;
			Cosom *= Sgn;
			T Scale0 = (T)1 - Alpha;
			T Scale1 = Alpha * Sgn;
			if (Cosom < (T)0.9999)
			{
				const T Omega = FMath::Acos(Cosom);
				const T InvSin = (T)1 / FMath::Sin(Omega);
				Scale0 = FMath::Sin(Scale0 * Omega) * InvSin;
				Scale1 = FMath::Sin(Scale1 * Omega) * InvSin;
			}
			return TQuat(Scale0 * A.X + Scale1 * B.X, Scale0 * A.Y + Scale1 * B.Y, Scale0 * A.Z + Scale1 * B.Z, Scale0 * A.W + Scale1 * B.W).GetNormalized();
		}
		TRotator<T> Rotator() const
		{
			const T SingularityTest = Z * X - W * Y;
			const T YawY = 2.f * (W * Z + X * Y);
			const T YawX = (1.f - 2.f * (FMath::Square(Y) + FMath::Square(Z)));
			const T Threshold = 0.4999995f;
			const T RadToDeg = (180.f / UE_PI);
			T Pitch, Yaw, Roll;
			if (SingularityTest < -Threshold)
			{
				Pitch = -90.f; Yaw = TRotator<T>::NormalizeAxis(-2.0f * FMath::Atan2(X, W) * RadToDeg); Roll = 0.f;
			}
			else if (SingularityTest > Threshold)
			{
				Pitch = 90.f; Yaw = TRotator<T>::NormalizeAxis(2.0f * FMath::Atan2(X, W) * RadToDeg); Roll = 0.f;
			}
			else
			{
				Pitch = FMath::Asin(2.f * SingularityTest) * RadToDeg;
				Yaw = FMath::Atan2(YawY, YawX) * RadToDeg;
				Roll = FMath::Atan2(-2.f * (W * X + Y * Z), (1.f - 2.f * (FMath::Square(X) + FMath::Square(Y)))) * RadToDeg;
			}
			return TRotator<T>(Pitch, Yaw, Roll);
		}
	};
	template <class T> const TQuat<T> TQuat<T>::Identity(0, 0, 0, 1);

	template <class T>
	TQuat<T> TRotator<T>::Quaternion() const
	{
		const T Half = (T)(UE_PI / 360.0);
		const T SP = std::sin(std::fmod(Pitch, (T)360) * Half), CP = std::cos(std::fmod(Pitch, (T)360) * Half);
		const T SY = std::sin(std::fmod(Yaw, (T)360) * Half), CY = std::cos(std::fmod(Yaw, (T)360) * Half);
		const T SR = std::sin(std::fmod(Roll, (T)360) * Half), CR = std::cos(std::fmod(Roll, (T)360) * Half);
		return TQuat<T>(CR * SP * SY - SR * CP * CY, -CR * SP * CY - SR * CP * SY, CR * CP * SY - SR * SP * CY, CR * CP * CY + SR * SP * SY);
	}
}

inline float FMath::ClampAngle(float Angle, float Lo, float Hi)
{
	const float MaxDelta = FRotator3f::ClampAxis(Hi - Lo) * 0.5f;
	const float RangeCenter = FRotator3f::ClampAxis(Lo + MaxDelta);
	const float DeltaFromCenter = FRotator3f::NormalizeAxis(Angle - RangeCenter);
	if (DeltaFromCenter > MaxDelta) { return FRotator3f::NormalizeAxis(RangeCenter + MaxDelta); }
	else if (DeltaFromCenter < -MaxDelta) { return FRotator3f::NormalizeAxis(RangeCenter - MaxDelta); }
	return FRotator3f::NormalizeAxis(Angle);
}

template <class T>
UE::Math::TVector2<T> FMath::Vector2DInterpTo(const UE::Math::TVector2<T>& Current, const UE::Math::TVector2<T>& Target, float Dt, float Speed)
{
	if (Speed <= 0.f) { return Target; }
	const UE::Math::TVector2<T> Dist = Target - Current;
	if (Dist.SizeSquared() < (T)UE_KINDA_SMALL_NUMBER) { return Target; }
	return Current + Dist * (T)Clamp(Dt * Speed, 0.f, 1.f);
}

template <class T>
UE::Math::TQuat<T> FMath::QInterpTo(const UE::Math::TQuat<T>& Current, const UE::Math::TQuat<T>& Target, float Dt, float Speed)
{
	if (Speed <= 0.f) { return Target; }
	if (Current.Equals(Target)) { return Target; }
	return UE::Math::TQuat<T>::Slerp(Current, Target, Clamp<float>(Speed * Dt, 0.f, 1.f));
}

template <class T>
UE::Math::TQuat<T> FMath::QInterpConstantTo(const UE::Math::TQuat<T>& Current, const UE::Math::TQuat<T>& Target, float Dt, float Speed)
{
	if (Speed <= 0.f) { return Target; }
	if (Current.Equals(Target)) { return Target; }
	float DeltaInterpSpeed = Clamp<float>(Dt * Speed, 0.f, 1.f);
	float AngularDistance = Max<float>(UE_SMALL_NUMBER, (float)(Target.AngularDistance(Current)));
	float Alpha = Clamp<float>(DeltaInterpSpeed / AngularDistance, 0.f, 1.f);
	return UE::Math::TQuat<T>::Slerp(Current, Target, Alpha);
}
