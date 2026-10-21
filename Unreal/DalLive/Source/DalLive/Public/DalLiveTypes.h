// Plain data DalLive passes between the host component and the editor Python side.

#pragma once

#include "CoreMinimal.h"
#include "DalLiveTypes.generated.h"

DECLARE_LOG_CATEGORY_EXTERN(LogDalLive, Log, All);

/** Severity of a message DalLive shows in the editor (toast) and the Message Log. */
UENUM(BlueprintType)
enum class EDalLiveMessageLevel : uint8
{
	Info,
	Success,
	Warning,
	Error,
};

/**
 * Something the host tells the logic (the HostV1 event tuples of dal.hostapi.EVENTS):
 *   Kind  notify / notify_begin / notify_end / montage_ended / montage_blending_out / input
 *   Name  the notify name, the montage name used in the .dal, or the action name
 *   Phase input events: started / triggered / ongoing / completed / canceled
 *   bFlag montage events: interrupted
 *   Value input events: bool / float in X, vec2 in XY, vec3 in XYZ
 */
USTRUCT(BlueprintType)
struct FDalLiveEvent
{
	GENERATED_BODY()

	UPROPERTY(BlueprintReadWrite, Category = "DAL")
	FName Kind;

	UPROPERTY(BlueprintReadWrite, Category = "DAL")
	FName Name;

	UPROPERTY(BlueprintReadWrite, Category = "DAL")
	FName Phase;

	UPROPERTY(BlueprintReadWrite, Category = "DAL")
	bool bFlag = false;

	UPROPERTY(BlueprintReadWrite, Category = "DAL")
	FVector Value = FVector::ZeroVector;
};

/** A reduced FHitResult (dal.runtime.Hit): a miss has bBlocking false, Point == the trace end, Distance 0, Time 1. */
USTRUCT(BlueprintType)
struct FDalLiveHit
{
	GENERATED_BODY()

	UPROPERTY(BlueprintReadWrite, Category = "DAL")
	bool bBlocking = false;

	UPROPERTY(BlueprintReadWrite, Category = "DAL")
	FVector Point = FVector::ZeroVector;

	UPROPERTY(BlueprintReadWrite, Category = "DAL")
	FVector Normal = FVector::ZeroVector;

	UPROPERTY(BlueprintReadWrite, Category = "DAL")
	float Distance = 0.0f;

	UPROPERTY(BlueprintReadWrite, Category = "DAL")
	float Time = 1.0f;
};
