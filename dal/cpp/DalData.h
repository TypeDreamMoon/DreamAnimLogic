// DalData.h -- what generated DAL code uses to read `settings` (data assets): sampling curves, looking up tables and lists.
//
// Static header (not generated), shipped next to DalMath.h.  The behaviour is the one of the DAL interpreter and of the
// generated Python (dal/runtime.py): a curve that was not assigned gives 0, a key that is not in a table (an index that is
// not in a list) gives the default value of the element type.
#pragma once

#include "CoreMinimal.h"
#include "Curves/CurveFloat.h"
#include "Curves/CurveVector.h"

#include <type_traits>

namespace DalData
{
	/** curve.eval(x): UCurveFloat::GetFloatValue; 0 for a curve that is not assigned. */
	inline float EvalCurve(const UCurveFloat* Curve, const float X)
	{
		return Curve != nullptr ? Curve->GetFloatValue(X) : 0.0f;
	}

	/** curve3.eval(x): UCurveVector::GetVectorValue (the three float curves); zero for a curve that is not assigned. */
	inline FVector3f EvalCurve3(const UCurveVector* Curve, const float X)
	{
		return Curve != nullptr ? FVector3f(Curve->GetVectorValue(X)) : FVector3f(0.0f, 0.0f, 0.0f);
	}

	/** table[key]: the value, or Default when the key is not in the table. */
	template <class K, class V, class Q>
	const V& MapGet(const TMap<K, V>& Table, const Q& Key, const std::type_identity_t<V>& Default)
	{
		const V* Found = Table.Find(Key);
		return Found != nullptr ? *Found : Default;
	}

	/** items[i]: the element, or Default when the index is not in the list. */
	template <class T>
	const T& ArrayGet(const TArray<T>& Items, const int32 Index, const std::type_identity_t<T>& Default)
	{
		return Items.IsValidIndex(Index) ? Items[Index] : Default;
	}

	/** The declared defaults of a settings struct (what a missing table entry reads as). */
	template <class T>
	const T& DefaultOf()
	{
		static const T Value{};
		return Value;
	}
}
