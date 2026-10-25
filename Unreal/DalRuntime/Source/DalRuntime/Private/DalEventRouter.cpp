// The registry behind DalHost::FEventRouter (declared in DalHost.h when DAL_ROUTER_EXTERNAL is set): one per process, here.

#include "DalHost.h"

namespace
{
	TArray<DalHost::IEventSink*>& Sinks()
	{
		static TArray<DalHost::IEventSink*> Registered;
		return Registered;
	}

	FCriticalSection& Guard()
	{
		static FCriticalSection Lock;
		return Lock;
	}
}

void DalHost::FEventRouter::Register(IEventSink* Sink)
{
	FScopeLock Lock(&Guard());
	Sinks().AddUnique(Sink);
}

void DalHost::FEventRouter::Unregister(IEventSink* Sink)
{
	FScopeLock Lock(&Guard());
	Sinks().Remove(Sink);
}

void DalHost::FEventRouter::Post(const AActor* Owner, const FEvent& Event)
{
	TArray<IEventSink*> Targets;
	{
		FScopeLock Lock(&Guard());
		for (IEventSink* Sink : Sinks())
		{
			if (Sink->DalSinkOwner() == Owner)
			{
				Targets.Add(Sink);
			}
		}
	}
	for (IEventSink* Sink : Targets)
	{
		Sink->DalReceiveEvent(Event);
	}
}

void DalHost::FEventRouter::PostNotify(const AActor* Owner, const EEventKind Kind, const FName& Name, const UObject* Source, const int32 MontageInstance)
{
	if (Owner == nullptr)
	{
		return;
	}
	FEvent Event;
	Event.Kind = Kind;
	Event.Name = Name;
	Event.Source = Source;
	Event.MontageInstance = MontageInstance;
	Post(Owner, Event);
}
