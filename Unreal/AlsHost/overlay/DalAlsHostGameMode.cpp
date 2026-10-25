#include "DalAlsHostGameMode.h"

#include "DalAlsHostCharacter.h"
#include "GameFramework/PlayerController.h"

ADalAlsHostGameMode::ADalAlsHostGameMode()
{
	DefaultPawnClass = ADalAlsHostCharacter::StaticClass();
	PlayerControllerClass = APlayerController::StaticClass();
}
