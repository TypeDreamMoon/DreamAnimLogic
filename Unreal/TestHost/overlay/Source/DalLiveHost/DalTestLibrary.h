// Helpers the W4 editor-Python harness calls inside PIE (things the unreal module cannot reach).

#pragma once

#include "CoreMinimal.h"
#include "Kismet/BlueprintFunctionLibrary.h"
#include "DalTestLibrary.generated.h"

class AActor;
class UActorComponent;
class UAnimMontage;
class APlayerController;
class UInputAction;
class UInputMappingContext;

UCLASS()
class UDalTestLibrary : public UBlueprintFunctionLibrary
{
	GENERATED_BODY()

public:
	UFUNCTION(BlueprintCallable, Category = "DalTest")
	static bool AddMappingContext(APlayerController* Controller, UInputMappingContext* Context, int32 Priority);

	/** Inject an action value for the next input processing (the same call game code uses to simulate input). */
	UFUNCTION(BlueprintCallable, Category = "DalTest")
	static bool InjectVector(APlayerController* Controller, const UInputAction* Action, FVector Value);

	UFUNCTION(BlueprintCallable, Category = "DalTest")
	static FVector GetActionVector(APlayerController* Controller, const UInputAction* Action);

	/** A static mesh actor (cube) used as a lock-on target or a line-of-sight blocker. */
	UFUNCTION(BlueprintCallable, Category = "DalTest", meta = (WorldContext = "WorldContextObject"))
	static AActor* SpawnCube(UObject* WorldContextObject, FVector Location, FVector Scale, FName Tag, bool bBlockVisibility);

	UFUNCTION(BlueprintCallable, Category = "DalTest")
	static int64 GetFrameCounter();

	/** Set a bool property by name even when it is VisibleAnywhere (the generated free inputs are; Python refuses to set those). */
	UFUNCTION(BlueprintCallable, Category = "DalTest")
	static bool SetBoolProperty(UObject* Object, FName PropertyName, bool bValue);

	/** A component of Class on Owner, created but not registered yet: set its properties, then call RegisterNewComponent. */
	UFUNCTION(BlueprintCallable, Category = "DalTest")
	static UActorComponent* CreateComponent(AActor* Owner, TSubclassOf<UActorComponent> ComponentClass);

	/** AddInstanceComponent + RegisterComponent: the component begins play when the owner has begun play. */
	UFUNCTION(BlueprintCallable, Category = "DalTest")
	static void RegisterNewComponent(UActorComponent* Component);

	/** UAnimMontage::AddAnimCompositeSection (not exposed to Python); returns the new section index. */
	UFUNCTION(BlueprintCallable, Category = "DalTest")
	static int32 AddMontageSection(UAnimMontage* Montage, FName SectionName, float StartTime);
};
