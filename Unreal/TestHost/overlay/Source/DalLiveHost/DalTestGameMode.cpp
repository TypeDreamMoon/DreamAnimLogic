#include "DalTestGameMode.h"

#include "DalLiveHostPlayerController.h"
#include "DalTestCharacter.h"

ADalTestGameMode::ADalTestGameMode()
{
	DefaultPawnClass = ADalTestCharacter::StaticClass();
	PlayerControllerClass = ADalLiveHostPlayerController::StaticClass();
}
