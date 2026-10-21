#include "DalLiveSubsystem.h"

#include "DalInterpHost.h"
#include "Engine/Engine.h"
#include "Misc/CommandLine.h"

#if WITH_EDITOR
#include "Framework/Application/SlateApplication.h"
#include "Framework/Notifications/NotificationManager.h"
#include "IMessageLogListing.h"
#include "Logging/MessageLog.h"
#include "MessageLogModule.h"
#include "Modules/ModuleManager.h"
#include "Widgets/Notifications/SNotificationList.h"
#endif

UDalLiveSubsystem* UDalLiveSubsystem::Get()
{
	return GEngine != nullptr ? GEngine->GetEngineSubsystem<UDalLiveSubsystem>() : nullptr;
}

void UDalLiveSubsystem::Deinitialize()
{
	OnHostBegin.Clear();
	OnHostEnd.Clear();
	OnHostTick.Clear();
	bPythonReady = false;
	Super::Deinitialize();
}

void UDalLiveSubsystem::SetPythonReady(const bool bReady)
{
	bPythonReady = bReady;
}

TArray<UDalInterpHost*> UDalLiveSubsystem::GetActiveHosts() const
{
	TArray<UDalInterpHost*> Result;
	for (const TWeakObjectPtr<UDalInterpHost>& Host : Hosts)
	{
		if (UDalInterpHost* Pointer = Host.Get())
		{
			Result.Add(Pointer);
		}
	}
	return Result;
}

void UDalLiveSubsystem::RegisterHost(UDalInterpHost* Host)
{
	Hosts.AddUnique(Host);
	OnHostBegin.Broadcast(Host);
}

void UDalLiveSubsystem::UnregisterHost(UDalInterpHost* Host)
{
	OnHostEnd.Broadcast(Host);
	Hosts.Remove(Host);
}

void UDalLiveSubsystem::ShowToast(const FString& Text, const EDalLiveMessageLevel Level, const float Seconds)
{
#if WITH_EDITOR
	if (IsRunningCommandlet() || !FSlateApplication::IsInitialized())
	{
		return;
	}
	FNotificationInfo Info(FText::FromString(Text));
	Info.ExpireDuration = Seconds;
	Info.bFireAndForget = true;
	Info.bUseSuccessFailIcons = true;
	Info.bUseLargeFont = false;
	const TSharedPtr<SNotificationItem> Item = FSlateNotificationManager::Get().AddNotification(Info);
	if (Item.IsValid())
	{
		switch (Level)
		{
		case EDalLiveMessageLevel::Success:
			Item->SetCompletionState(SNotificationItem::CS_Success);
			break;
		case EDalLiveMessageLevel::Error:
			Item->SetCompletionState(SNotificationItem::CS_Fail);
			break;
		default:
			Item->SetCompletionState(SNotificationItem::CS_None);
			break;
		}
	}
#endif
}

void UDalLiveSubsystem::AddMessageLogLine(const FString& Text, const EDalLiveMessageLevel Level)
{
#if WITH_EDITOR
	static const FName ListingName(TEXT("DalLive"));
	FMessageLogModule& MessageLogModule = FModuleManager::LoadModuleChecked<FMessageLogModule>("MessageLog");
	if (!MessageLogModule.IsRegisteredLogListing(ListingName))
	{
		MessageLogModule.RegisterLogListing(ListingName, FText::FromString(TEXT("DAL Live")));
	}
	FMessageLog Log(ListingName);
	const FText Line = FText::FromString(Text);
	switch (Level)
	{
	case EDalLiveMessageLevel::Error:
		Log.Error(Line);
		break;
	case EDalLiveMessageLevel::Warning:
		Log.Warning(Line);
		break;
	default:
		Log.Info(Line);
		break;
	}
#endif
}
