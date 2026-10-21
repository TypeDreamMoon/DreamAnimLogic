#include "DalTestLibrary.h"

#include "Animation/AnimMontage.h"
#include "Components/ActorComponent.h"
#include "GameFramework/Actor.h"
#include "Engine/Engine.h"
#include "Engine/LocalPlayer.h"
#include "Engine/StaticMesh.h"
#include "Engine/StaticMeshActor.h"
#include "Engine/World.h"
#include "EnhancedInputSubsystems.h"
#include "EnhancedPlayerInput.h"
#include "GameFramework/PlayerController.h"
#include "InputAction.h"
#include "InputMappingContext.h"
#include "Components/StaticMeshComponent.h"

namespace
{
	UEnhancedInputLocalPlayerSubsystem* GetSubsystem(APlayerController* Controller)
	{
		ULocalPlayer* LocalPlayer = Controller != nullptr ? Controller->GetLocalPlayer() : nullptr;
		return LocalPlayer != nullptr ? LocalPlayer->GetSubsystem<UEnhancedInputLocalPlayerSubsystem>() : nullptr;
	}
}

bool UDalTestLibrary::AddMappingContext(APlayerController* Controller, UInputMappingContext* Context, int32 Priority)
{
	if (UEnhancedInputLocalPlayerSubsystem* Subsystem = GetSubsystem(Controller))
	{
		Subsystem->AddMappingContext(Context, Priority);
		return true;
	}
	return false;
}

bool UDalTestLibrary::InjectVector(APlayerController* Controller, const UInputAction* Action, FVector Value)
{
	if (UEnhancedInputLocalPlayerSubsystem* Subsystem = GetSubsystem(Controller))
	{
		Subsystem->InjectInputVectorForAction(Action, Value, TArray<UInputModifier*>(), TArray<UInputTrigger*>());
		return true;
	}
	return false;
}

FVector UDalTestLibrary::GetActionVector(APlayerController* Controller, const UInputAction* Action)
{
	if (UEnhancedInputLocalPlayerSubsystem* Subsystem = GetSubsystem(Controller))
	{
		if (const UEnhancedPlayerInput* PlayerInput = Subsystem->GetPlayerInput())
		{
			return PlayerInput->GetActionValue(Action).Get<FVector>();
		}
	}
	return FVector::ZeroVector;
}

AActor* UDalTestLibrary::SpawnCube(UObject* WorldContextObject, FVector Location, FVector Scale, FName Tag, bool bBlockVisibility)
{
	UWorld* World = GEngine != nullptr ? GEngine->GetWorldFromContextObject(WorldContextObject, EGetWorldErrorMode::ReturnNull) : nullptr;
	if (World == nullptr)
	{
		return nullptr;
	}
	FActorSpawnParameters Params;
	Params.SpawnCollisionHandlingOverride = ESpawnActorCollisionHandlingMethod::AlwaysSpawn;
	AStaticMeshActor* Actor = World->SpawnActor<AStaticMeshActor>(Location, FRotator::ZeroRotator, Params);
	if (Actor == nullptr)
	{
		return nullptr;
	}
	Actor->SetMobility(EComponentMobility::Movable);
	UStaticMeshComponent* Mesh = Actor->GetStaticMeshComponent();
	Mesh->SetStaticMesh(LoadObject<UStaticMesh>(nullptr, TEXT("/Engine/BasicShapes/Cube.Cube")));
	Actor->SetActorScale3D(Scale);
	Mesh->SetCollisionEnabled(ECollisionEnabled::QueryAndPhysics);
	Mesh->SetCollisionResponseToAllChannels(ECR_Block);
	Mesh->SetCollisionResponseToChannel(ECC_Visibility, bBlockVisibility ? ECR_Block : ECR_Ignore);
	if (!Tag.IsNone())
	{
		Actor->Tags.Add(Tag);
	}
	return Actor;
}

int64 UDalTestLibrary::GetFrameCounter()
{
	return static_cast<int64>(GFrameCounter);
}

int32 UDalTestLibrary::AddMontageSection(UAnimMontage* Montage, FName SectionName, float StartTime)
{
	return Montage != nullptr ? Montage->AddAnimCompositeSection(SectionName, StartTime) : INDEX_NONE;
}

UActorComponent* UDalTestLibrary::CreateComponent(AActor* Owner, TSubclassOf<UActorComponent> ComponentClass)
{
	if (Owner == nullptr || ComponentClass == nullptr)
	{
		return nullptr;
	}
	return NewObject<UActorComponent>(Owner, ComponentClass, NAME_None, RF_Transient);
}

void UDalTestLibrary::RegisterNewComponent(UActorComponent* Component)
{
	if (Component != nullptr && Component->GetOwner() != nullptr)
	{
		Component->GetOwner()->AddInstanceComponent(Component);
		Component->RegisterComponent();
	}
}

bool UDalTestLibrary::SetBoolProperty(UObject* Object, FName PropertyName, bool bValue)
{
	if (Object == nullptr)
	{
		return false;
	}
	if (FBoolProperty* Property = CastField<FBoolProperty>(Object->GetClass()->FindPropertyByName(PropertyName)))
	{
		Property->SetPropertyValue_InContainer(Object, bValue);
		return true;
	}
	return false;
}
