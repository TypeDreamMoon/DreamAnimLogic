// ADalAlsHostCharacter: the ALS character as the generated code sees it -- a plain ACharacter (ADalTestCharacter: the DalRuntime movement
// component) with the generated logic components added: UAlsCharacter (AlsCharacter.dal), UAlsInput (AlsInput.dal), the ragdoll service of
// DalRuntime.  The mesh, the animation blueprint and the data assets are the ones B_Als_Character has (its defaults were dumped from the
// original: capsule 30 x 90, mesh SKM_Als at (0, 0, -92) turned -90, AB_Als, CS_Als_Default, MS_Als_Normal).
//
// This is what AAlsCharacterExample + B_Als_Character are in the original; the camera of the original (UAlsCameraComponent) is not rewritten
// yet, a plain spring arm and camera stand in for it so that the character can be watched.

#pragma once

#include "CoreMinimal.h"
#include "DalTestCharacter.h"
#include "DalAlsHostCharacter.generated.h"

class UAlsCharacter;
class UAlsInput;
class UCameraComponent;
class UDalHookProbe;
class UDalRagdollService;
class UInputMappingContext;
class USpringArmComponent;

UCLASS()
class ADalAlsHostCharacter : public ADalTestCharacter
{
	GENERATED_BODY()

public:
	/** AlsCharacter.dal */
	UPROPERTY(VisibleAnywhere, BlueprintReadOnly, Category = "ALS")
	TObjectPtr<UAlsCharacter> AlsLogic;

	/** AlsInput.dal */
	UPROPERTY(VisibleAnywhere, BlueprintReadOnly, Category = "ALS")
	TObjectPtr<UAlsInput> AlsInputLogic;

	UPROPERTY(VisibleAnywhere, BlueprintReadOnly, Category = "ALS")
	TObjectPtr<UDalRagdollService> RagdollService;

	/** Counts the hook events DalRuntime posts (tests). */
	UPROPERTY(VisibleAnywhere, BlueprintReadOnly, Category = "ALS")
	TObjectPtr<UDalHookProbe> HookProbe;

	UPROPERTY(VisibleAnywhere, BlueprintReadOnly, Category = "ALS")
	TObjectPtr<USpringArmComponent> CameraArm;

	UPROPERTY(VisibleAnywhere, BlueprintReadOnly, Category = "ALS")
	TObjectPtr<UCameraComponent> Camera;

	UPROPERTY(EditAnywhere, BlueprintReadOnly, Category = "ALS")
	TObjectPtr<UInputMappingContext> InputMappingContext;

public:
	explicit ADalAlsHostCharacter(const FObjectInitializer& ObjectInitializer);

	virtual void NotifyControllerChanged() override;
};
