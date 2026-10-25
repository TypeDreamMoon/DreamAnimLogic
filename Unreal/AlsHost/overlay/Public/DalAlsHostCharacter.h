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
#include "GameplayTagContainer.h"
#include "DalAlsHostCharacter.generated.h"

class UAlsCharacter;
class UAlsInput;
class UAnimInstance;
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

	/** B_Als_Character.OverlayAnimationInstanceClasses: the animation instance class that implements the Overlay layer of AB_Als for each overlay mode. */
	UPROPERTY(EditAnywhere, BlueprintReadOnly, Category = "ALS")
	TMap<FName, TSubclassOf<UAnimInstance>> OverlayAnimationInstanceClasses;     // by tag name, Als.OverlayMode.<Mode>

public:
	explicit ADalAlsHostCharacter(const FObjectInitializer& ObjectInitializer);

	virtual void NotifyControllerChanged() override;
	virtual void PostInitializeComponents() override;
	virtual void BeginPlay() override;
	virtual void Tick(float DeltaSeconds) override;

private:
	/**
	 * B_Als_Character's OnOverlayModeChanged -> RefreshOverlayLinkedAnimationLayer: link the layers of the overlay mode's animation instance class
	 * into the mesh's animation instance.  The Blueprint event is called by SetOverlayMode in the original; a generated logic has no such event (the
	 * Blueprint events of AAlsCharacter are dropped, als/AlsCharacter.dal), so the host character watches the logic's OverlayMode instead.
	 */
	void RefreshOverlayLinkedAnimationLayer();

	FGameplayTag LinkedOverlayMode;
};
