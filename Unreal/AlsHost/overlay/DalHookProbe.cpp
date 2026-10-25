#include "DalHookProbe.h"

void UDalHookProbe::BeginPlay()
{
	Super::BeginPlay();
	DalHost::FEventRouter::Register(this);
}

void UDalHookProbe::EndPlay(const EEndPlayReason::Type EndPlayReason)
{
	DalHost::FEventRouter::Unregister(this);
	Super::EndPlay(EndPlayReason);
}

void UDalHookProbe::ResetCounts()
{
	ModeChanged = Jumped = Landed = CrouchStarted = CrouchEnded = Teleported = PhysicsRotation = Notifies = 0;
	Log.Reset();
}

void UDalHookProbe::PostTestNotify(const FName Name)
{
	DalHost::FEventRouter::PostNotify(GetOwner(), DalHost::EEventKind::Notify, Name, this, INDEX_NONE);
}

void UDalHookProbe::DalReceiveEvent(const DalHost::FEvent& Event)
{
	using DalHost::EEventKind;
	FString Line;
	switch (Event.Kind)
	{
	case EEventKind::MovementModeChanged:
		++ModeChanged;
		LastModeFrom = Event.ModeA;
		LastModeTo = Event.ModeB;
		Line = FString::Printf(TEXT("mode %d -> %d"), Event.ModeA, Event.ModeB);
		break;
	case EEventKind::Jumped:
		++Jumped;
		Line = TEXT("jumped");
		break;
	case EEventKind::Landed:
		++Landed;
		LastLandedNormalZ = Event.Hit.Normal.Z;
		bLastLandedBlocking = Event.Hit.bBlocking;
		bLastLandedHasPrimitive = Event.Hit.Primitive != nullptr;
		Line = FString::Printf(TEXT("landed blocking=%d normalZ=%.3f"), Event.Hit.bBlocking ? 1 : 0, Event.Hit.Normal.Z);
		break;
	case EEventKind::CrouchStarted:
		++CrouchStarted;
		Line = TEXT("crouch started");
		break;
	case EEventKind::CrouchEnded:
		++CrouchEnded;
		Line = TEXT("crouch ended");
		break;
	case EEventKind::Teleported:
		++Teleported;
		Line = TEXT("teleported");
		break;
	case EEventKind::PhysicsRotation:
		++PhysicsRotation;
		LastPhysicsRotationDelta = Event.Value.X;
		return;                                    // every frame: not logged
	case EEventKind::Notify:
		++Notifies;
		Line = FString::Printf(TEXT("notify %s (source %s)"), *Event.Name.ToString(), Event.Source != nullptr ? *Event.Source->GetName() : TEXT("none"));
		break;
	default:
		return;
	}
	Log.Add(MoveTemp(Line));
	if (Log.Num() > 64)
	{
		Log.RemoveAt(0);
	}
}
