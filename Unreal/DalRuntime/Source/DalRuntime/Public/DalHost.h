// DalHost.h -- host traffic types of the DAL (Dream Anim Logic) C++ backend: events, commands, trace results.
//
// Part of the dal compiler package; copied next to the generated sources by `python -m dal compile --cpp`
// when a program uses the v1 host features (event handlers, host commands, actor logic).
//
// * FEvent   something the host tells the logic (notify, montage ended, input action, a hook of the character ...);
//            queued on the game thread, handled at the start of the logic's next update;
// * FCommand something the logic asks the host to do (play a montage, change the walk speed ...);
//            queued by the update (any thread), executed on the game thread after it;
// * FHit     the result of a host trace (a reduced FHitResult: positions as float and, for point_d / location_d, as the double the engine has);
// * IEventSink / FEventRouter   how events that do not come from an animation delegate reach the logics of an actor:
//            the hooks of the character (the movement component of DalRuntime posts them) and the notifies of the
//            classes a program declares (the generated notify classes post them).  A logic that handles such an
//            event registers itself as a sink; Post() hands the event to every sink of the actor.
//
// The ids (montage, notify, action, section) are indices into tables the generator writes into the
// logic's .cpp.  Do not edit; regenerate with the compiler.
#pragma once

#include "CoreMinimal.h"
#include "Misc/ScopeLock.h"

class AActor;
class UObject;
class UPrimitiveComponent;
class UAnimMontage;
class UAnimSequenceBase;

#define DAL_HOST_VERSION 4

namespace DalHost
{
	/** Result of a trace; a miss has bBlocking == false, Point == Location == the trace end, Distance == 0 and Time == 1.
	 *  Point / Normal are the ImpactPoint / ImpactNormal of the FHitResult, FaceNormal its Normal, Location the swept shape's
	 *  center at the hit; Primitive is the component that was hit.  PointD and LocationD are Point and Location as the engine has
	 *  them (double, not narrowed): `hit.point_d` and `hit.location_d` of the language. */
	struct FHit
	{
		bool bBlocking = false;
		FVector3f Point = FVector3f(0.0f, 0.0f, 0.0f);
		FVector3f Normal = FVector3f(0.0f, 0.0f, 0.0f);
		float Distance = 0.0f;
		float Time = 1.0f;
		FVector3f Location = FVector3f(0.0f, 0.0f, 0.0f);
		bool bStartPenetrating = false;
		UPrimitiveComponent* Primitive = nullptr;
		FVector3f FaceNormal = FVector3f(0.0f, 0.0f, 0.0f);
		FVector PointD = FVector(0.0, 0.0, 0.0);
		FVector LocationD = FVector(0.0, 0.0, 0.0);
	};

	enum class EEventKind : uint8
	{
		Notify,
		NotifyBegin,
		NotifyEnd,
		MontageEnded,
		MontageBlendingOut,
		Input,
		// v4
		NotifyTick,
		MovementModeChanged,
		Jumped,
		Landed,
		CrouchStarted,
		CrouchEnded,
		Teleported,
		PhysicsRotation,                       // Value.X: the delta time of the rotation update
	};

	enum class EInputPhase : uint8
	{
		Started,
		Triggered,
		Ongoing,
		Completed,
		Canceled,
	};

	struct FEvent
	{
		EEventKind Kind = EEventKind::Notify;
		int32 Id = INDEX_NONE;                 // notify / montage / action index
		EInputPhase Phase = EInputPhase::Started;
		bool bFlag = false;                    // montage events: interrupted
		FVector3f Value = FVector3f(0.0f, 0.0f, 0.0f);   // input events: bool / float in X, vec2 in XY, vec3 in XYZ
		// v4
		int32 ModeA = 0;                       // MovementModeChanged: the previous EMovementMode ...
		int32 ModeB = 0;                       // ... and the new one
		FHit Hit;                              // Landed
		FName Name;                            // events posted by a notify class: the declared name (the logic looks up its Id)
		const UObject* Source = nullptr;       // events posted by a notify class: the notify object, its properties are the payload
		int32 MontageInstance = INDEX_NONE;    // events posted by a notify class: the montage instance that raised it
	};

	/** A logic that receives the events of its actor that do not come from the animation delegates. */
	class IEventSink
	{
	public:
		virtual ~IEventSink() = default;
		virtual const AActor* DalSinkOwner() const = 0;
		virtual void DalReceiveEvent(const FEvent& Event) = 0;
		/** A hook slot (`update(dt) in physics_walking { ... }`): the movement component of the actor runs the block itself, at the point the slot is
		 *  named after (the start of its walking physics), with the delta time of that step.  A logic without such a slot does nothing. */
		virtual void DalRunHook(const FName Hook, const float DeltaTime) { (void)Hook; (void)DeltaTime; }
	};

	/** The sinks of the running logics.  Post() is called by the movement component and the notify classes (game thread);
	 *  a sink registers when play begins and unregisters when it ends.
	 *
	 *  The registry must exist once per process.  Built with DalRuntime (DAL_ROUTER_EXTERNAL, defined by its Build.cs for every module that
	 *  depends on it) the class is exported by that module and its statics live in DalEventRouter.cpp; otherwise (the test stand-ins, a
	 *  project that does not use the hooks of the movement component) the whole thing is inline in this header. */
#if defined(DAL_ROUTER_EXTERNAL) && DAL_ROUTER_EXTERNAL
	class DALRUNTIME_API FEventRouter
	{
	public:
		static void Register(IEventSink* Sink);
		static void Unregister(IEventSink* Sink);
		static void Post(const AActor* Owner, const FEvent& Event);
		static void RunHook(const AActor* Owner, const FName Hook, const float DeltaTime);
		static void PostNotify(const AActor* Owner, const EEventKind Kind, const FName& Name, const UObject* Source, const int32 MontageInstance);
	};
#else
	class FEventRouter
	{
	public:
		static void Register(IEventSink* Sink)
		{
			FScopeLock Lock(&Guard());
			Sinks().AddUnique(Sink);
		}

		static void Unregister(IEventSink* Sink)
		{
			FScopeLock Lock(&Guard());
			Sinks().Remove(Sink);
		}

		static void Post(const AActor* Owner, const FEvent& Event)
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

		/** The movement component runs the hook slot `Hook` of the logics of `Owner` (see IEventSink::DalRunHook). */
		static void RunHook(const AActor* Owner, const FName Hook, const float DeltaTime)
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
				Sink->DalRunHook(Hook, DeltaTime);
			}
		}

		/** What a generated notify class posts: the notify object (its properties are the payload) under its declared name, and the
		 *  montage instance that raised it when a montage did (INDEX_NONE otherwise). */
		static void PostNotify(const AActor* Owner, const EEventKind Kind, const FName& Name, const UObject* Source, const int32 MontageInstance)
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

	private:
		static TArray<IEventSink*>& Sinks()
		{
			static TArray<IEventSink*> Registered;
			return Registered;
		}

		static FCriticalSection& Guard()
		{
			static FCriticalSection Lock;
			return Lock;
		}
	};
#endif

	enum class ECommand : uint8
	{
		SetMaxWalkSpeedCrouched,
		SetMaxAccelerationWalking,
		SetGroundFriction,
		SetBrakingFrictionFactor,
		SetInputBlocked,
		SetActorRotation,
		AddControllerYawInput,
		AddControllerPitchInput,
		SetMaxWalkSpeed,
		SetMaxAcceleration,
		SetBrakingDecelerationWalking,
		SetGravityScale,
		SetJumpZVelocity,
		SetAirControl,
		SetRotationRate,
		SetOrientRotationToMovement,
		SetUseControllerDesiredRotation,
		SetMovementMode,
		Jump,
		StopJumping,
		Crouch,
		UnCrouch,
		AddMovementInput,
		Launch,
		MontagePlay,
		MontageStop,
		MontageStopAll,
		MontageJumpToSection,
		MontageSetNextSection,
		MontageSetPlayRate,
		SetControlRotation,
		CameraSetArmLength,
		CameraSetSocketOffset,
		CameraSetFov,
		// v4
		MontageSetPosition,
		RootMotionOverride,
		RootMotionClear,
		SetMovementModeLocked,
		SetActorLocation,
		SetVelocity,
		SetAnimRootMotionTranslationScale,
		RagdollBegin,
		RagdollEnd,
		RagdollLimitSpeed,
		RagdollSetMotorStrength,
		RagdollAddForce,
		MontageStopSource,
		PlaySlotAnimation,
		StopMontagesWithSlot,
		SnapshotPose,
		// v6
		SetMovementBase,
	};

	struct FCommand
	{
		ECommand Kind = ECommand::Jump;
		int32 Montage = INDEX_NONE;            // index of the montage slot
		int32 SectionA = 0;                    // section index (0 = none)
		int32 SectionB = 0;
		int32 Mode = 0;                        // EMovementMode
		float F = 0.0f;
		bool B0 = false;
		bool B1 = false;
		FVector3f V = FVector3f(0.0f, 0.0f, 0.0f);
		FRotator3f R = FRotator3f(0.0f, 0.0f, 0.0f);
		// v4
		FVector VD = FVector(0.0, 0.0, 0.0);                 // the commands that take a vec3d: root_motion_override (the velocity), set_actor_location (the location)
		FQuat QD = FQuat(0.0, 0.0, 0.0, 1.0);                // root_motion_override: the rotation delta (double, as the engine takes it)
		FName Name;                            // slot name, or the dotted path of the snapshot state
		int32 Count = 0;                       // play_slot_animation: loops
		int32 Instance = INDEX_NONE;           // montage_stop_source: the montage instance that raised the notify
		float F1 = 0.0f;                       // play_slot_animation: blend out time, rate, blend out trigger time, start time
		float F2 = 0.0f;
		float F3 = 0.0f;
		float F4 = 0.0f;
		UAnimMontage* MontageAsset = nullptr;  // a `montage` value as the command's montage (instead of the index of a declared one)
		UAnimSequenceBase* Sequence = nullptr; // play_slot_animation: the sequence
		UPrimitiveComponent* Primitive = nullptr; // set_movement_base: the component to stand on (none: no base)
	};

	/** Constructors of the commands, one per `host.<name>(...)` statement. */
	namespace Cmd
	{
		inline FCommand Make(ECommand Kind)
		{
			FCommand C;
			C.Kind = Kind;
			return C;
		}

		inline FCommand WithFloat(ECommand Kind, float F)
		{
			FCommand C = Make(Kind);
			C.F = F;
			return C;
		}

		inline FCommand WithBool(ECommand Kind, bool B)
		{
			FCommand C = Make(Kind);
			C.B0 = B;
			return C;
		}

		inline FCommand SetMaxWalkSpeed(float V) { return WithFloat(ECommand::SetMaxWalkSpeed, V); }
		inline FCommand SetMaxWalkSpeedCrouched(float V) { return WithFloat(ECommand::SetMaxWalkSpeedCrouched, V); }
		inline FCommand SetMaxAccelerationWalking(float V) { return WithFloat(ECommand::SetMaxAccelerationWalking, V); }
		inline FCommand SetGroundFriction(float V) { return WithFloat(ECommand::SetGroundFriction, V); }
		inline FCommand SetBrakingFrictionFactor(float V) { return WithFloat(ECommand::SetBrakingFrictionFactor, V); }
		inline FCommand SetInputBlocked(bool B) { return WithBool(ECommand::SetInputBlocked, B); }
		inline FCommand AddControllerYawInput(float V) { return WithFloat(ECommand::AddControllerYawInput, V); }
		inline FCommand AddControllerPitchInput(float V) { return WithFloat(ECommand::AddControllerPitchInput, V); }
		inline FCommand SetActorRotation(const FRotator3f& R, bool bTeleport)
		{
			FCommand C = Make(ECommand::SetActorRotation);
			C.R = R;
			C.B0 = bTeleport;
			return C;
		}
		inline FCommand SetMaxAcceleration(float V) { return WithFloat(ECommand::SetMaxAcceleration, V); }
		inline FCommand SetBrakingDecelerationWalking(float V) { return WithFloat(ECommand::SetBrakingDecelerationWalking, V); }
		inline FCommand SetGravityScale(float V) { return WithFloat(ECommand::SetGravityScale, V); }
		inline FCommand SetJumpZVelocity(float V) { return WithFloat(ECommand::SetJumpZVelocity, V); }
		inline FCommand SetAirControl(float V) { return WithFloat(ECommand::SetAirControl, V); }
		inline FCommand SetOrientRotationToMovement(bool B) { return WithBool(ECommand::SetOrientRotationToMovement, B); }
		inline FCommand SetUseControllerDesiredRotation(bool B) { return WithBool(ECommand::SetUseControllerDesiredRotation, B); }
		inline FCommand CameraSetArmLength(float V) { return WithFloat(ECommand::CameraSetArmLength, V); }
		inline FCommand CameraSetFov(float V) { return WithFloat(ECommand::CameraSetFov, V); }
		inline FCommand Jump() { return Make(ECommand::Jump); }
		inline FCommand StopJumping() { return Make(ECommand::StopJumping); }
		inline FCommand Crouch() { return Make(ECommand::Crouch); }
		inline FCommand UnCrouch() { return Make(ECommand::UnCrouch); }

		inline FCommand SetRotationRate(const FRotator3f& R)
		{
			FCommand C = Make(ECommand::SetRotationRate);
			C.R = R;
			return C;
		}

		inline FCommand SetControlRotation(const FRotator3f& R)
		{
			FCommand C = Make(ECommand::SetControlRotation);
			C.R = R;
			return C;
		}

		inline FCommand SetMovementMode(int32 Mode, int32 Sub = 0)
		{
			FCommand C = Make(ECommand::SetMovementMode);
			C.Mode = Mode;
			C.Count = Sub;                                    // the custom sub mode (MOVE_Custom)
			return C;
		}

		inline FCommand RootMotionOverride(const FVector& Velocity, const FQuat& RotationDelta)
		{
			FCommand C = Make(ECommand::RootMotionOverride);
			C.VD = Velocity;
			C.QD = RotationDelta;
			return C;
		}

		inline FCommand RootMotionClear() { return Make(ECommand::RootMotionClear); }

		inline FCommand SetMovementModeLocked(bool B) { return WithBool(ECommand::SetMovementModeLocked, B); }
		inline FCommand SetAnimRootMotionTranslationScale(float V) { return WithFloat(ECommand::SetAnimRootMotionTranslationScale, V); }
		inline FCommand RagdollLimitSpeed(float V) { return WithFloat(ECommand::RagdollLimitSpeed, V); }
		inline FCommand RagdollSetMotorStrength(float V) { return WithFloat(ECommand::RagdollSetMotorStrength, V); }
		inline FCommand RagdollEnd() { return Make(ECommand::RagdollEnd); }

		inline FCommand SetActorLocation(const FVector& Location, bool bSweep, bool bTeleport)
		{
			FCommand C = Make(ECommand::SetActorLocation);
			C.VD = Location;
			C.B0 = bSweep;
			C.B1 = bTeleport;
			return C;
		}

		inline FCommand SetVelocity(const FVector3f& Velocity)
		{
			FCommand C = Make(ECommand::SetVelocity);
			C.V = Velocity;
			return C;
		}

		inline FCommand SetMovementBase(UPrimitiveComponent* Primitive)
		{
			FCommand C = Make(ECommand::SetMovementBase);
			C.Primitive = Primitive;
			return C;
		}

		inline FCommand RagdollBegin(bool bLimitSpeed, float SpeedLimit)
		{
			FCommand C = Make(ECommand::RagdollBegin);
			C.B0 = bLimitSpeed;
			C.F = SpeedLimit;
			return C;
		}

		inline FCommand RagdollAddForce(const FVector3f& Force, bool bSpine)
		{
			FCommand C = Make(ECommand::RagdollAddForce);
			C.V = Force;
			C.B0 = bSpine;
			return C;
		}

		inline FCommand AddMovementInput(const FVector3f& Direction, float Scale)
		{
			FCommand C = Make(ECommand::AddMovementInput);
			C.V = Direction;
			C.F = Scale;
			return C;
		}

		inline FCommand Launch(const FVector3f& Velocity, bool bXYOverride, bool bZOverride)
		{
			FCommand C = Make(ECommand::Launch);
			C.V = Velocity;
			C.B0 = bXYOverride;
			C.B1 = bZOverride;
			return C;
		}

		inline FCommand CameraSetSocketOffset(const FVector3f& Offset)
		{
			FCommand C = Make(ECommand::CameraSetSocketOffset);
			C.V = Offset;
			return C;
		}

		inline FCommand MontagePlay(int32 Montage, float Rate, int32 Section)
		{
			FCommand C = Make(ECommand::MontagePlay);
			C.Montage = Montage;
			C.F = Rate;
			C.SectionA = Section;
			return C;
		}

		inline FCommand MontageStop(int32 Montage, float BlendOut)
		{
			FCommand C = Make(ECommand::MontageStop);
			C.Montage = Montage;
			C.F = BlendOut;
			return C;
		}

		inline FCommand MontageStopAll(float BlendOut) { return WithFloat(ECommand::MontageStopAll, BlendOut); }

		inline FCommand MontageJumpToSection(int32 Montage, int32 Section)
		{
			FCommand C = Make(ECommand::MontageJumpToSection);
			C.Montage = Montage;
			C.SectionA = Section;
			return C;
		}

		inline FCommand MontageSetNextSection(int32 Montage, int32 Section, int32 Next)
		{
			FCommand C = Make(ECommand::MontageSetNextSection);
			C.Montage = Montage;
			C.SectionA = Section;
			C.SectionB = Next;
			return C;
		}

		inline FCommand MontageSetPlayRate(int32 Montage, float Rate)
		{
			FCommand C = Make(ECommand::MontageSetPlayRate);
			C.Montage = Montage;
			C.F = Rate;
			return C;
		}

		// v4: the montage is a `montage` value (it has no sections known to the program)
		inline FCommand MontagePlay(UAnimMontage* Asset, float Rate)
		{
			FCommand C = Make(ECommand::MontagePlay);
			C.MontageAsset = Asset;
			C.F = Rate;
			return C;
		}

		inline FCommand MontageStop(UAnimMontage* Asset, float BlendOut)
		{
			FCommand C = Make(ECommand::MontageStop);
			C.MontageAsset = Asset;
			C.F = BlendOut;
			return C;
		}

		inline FCommand MontageSetPlayRate(UAnimMontage* Asset, float Rate)
		{
			FCommand C = Make(ECommand::MontageSetPlayRate);
			C.MontageAsset = Asset;
			C.F = Rate;
			return C;
		}

		inline FCommand MontageSetPosition(int32 Montage, float Position)
		{
			FCommand C = Make(ECommand::MontageSetPosition);
			C.Montage = Montage;
			C.F = Position;
			return C;
		}

		inline FCommand MontageSetPosition(UAnimMontage* Asset, float Position)
		{
			FCommand C = Make(ECommand::MontageSetPosition);
			C.MontageAsset = Asset;
			C.F = Position;
			return C;
		}

		inline FCommand MontageStopSource(int32 Instance, float BlendOut)
		{
			FCommand C = Make(ECommand::MontageStopSource);
			C.Instance = Instance;
			C.F = BlendOut;
			return C;
		}

		inline FCommand PlaySlotAnimation(UAnimSequenceBase* Sequence, const FName& Slot, float BlendIn, float BlendOut, float Rate, int32 Loops,
		                                  float BlendOutTriggerTime, float StartTime, int32 BlendOutMode)
		{
			FCommand C = Make(ECommand::PlaySlotAnimation);
			C.Sequence = Sequence;
			C.Name = Slot;
			C.F = BlendIn;
			C.F1 = BlendOut;
			C.F2 = Rate;
			C.Count = Loops;
			C.F3 = BlendOutTriggerTime;
			C.F4 = StartTime;
			C.Mode = BlendOutMode;
			return C;
		}

		inline FCommand StopMontagesWithSlot(const FName& Slot, float BlendOut)
		{
			FCommand C = Make(ECommand::StopMontagesWithSlot);
			C.Name = Slot;
			C.F = BlendOut;
			return C;
		}

		inline FCommand SnapshotPose(const FName& Name)
		{
			FCommand C = Make(ECommand::SnapshotPose);
			C.Name = Name;
			return C;
		}
	} // namespace Cmd

	/** `flush_peers`: hands the commands that a peer queued in the calls of a block to the host now, with the commands of the block that called it.
	 *  The peer is a logic with host commands and public methods (its DalFlushCommands is public); any other peer has nothing to hand over. */
	template <typename TPeer>
	auto FlushPeerCommands(TPeer* Peer, int) -> decltype(Peer->DalFlushCommands(), void())
	{
		if (Peer != nullptr)
		{
			Peer->DalFlushCommands();
		}
	}

	template <typename TPeer>
	void FlushPeerCommands(TPeer*, long)
	{
	}
} // namespace DalHost
