// Test character for the DalLive W4 harness: the template character with the Manny mesh and no template input bindings.

#pragma once

#include "CoreMinimal.h"
#include "DalLiveHostCharacter.h"
#include "DalTestCharacter.generated.h"

UCLASS()
class ADalTestCharacter : public ADalLiveHostCharacter
{
	GENERATED_BODY()

public:
	ADalTestCharacter();

protected:
	/** The harness injects input itself; the template's Jump / Move / Look bindings (with unassigned actions) are not wanted. */
	virtual void SetupPlayerInputComponent(UInputComponent* PlayerInputComponent) override;
};
