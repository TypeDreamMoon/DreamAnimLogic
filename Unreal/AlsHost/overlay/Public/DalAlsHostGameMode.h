// ADalAlsHostGameMode: spawns ADalAlsHostCharacter (the ALS character with the generated logic) for the player, like B_Als_GameMode does for
// B_Als_Character.  Set it as the world settings' game mode override of the recording map (in the editor world, not saved: the recording
// scripts of Unreal/AlsHost do that before they start PIE).

#pragma once

#include "CoreMinimal.h"
#include "GameFramework/GameModeBase.h"
#include "DalAlsHostGameMode.generated.h"

UCLASS()
class ADalAlsHostGameMode : public AGameModeBase
{
	GENERATED_BODY()

public:
	ADalAlsHostGameMode();
};
