// DalHost.h -- host traffic types of the DAL (Dream Anim Logic) C++ backend: events, commands, trace results.
//
// Part of the dal compiler package; copied next to the generated sources by `python -m dal compile --cpp`
// when a program uses the v1 host features (event handlers, host commands, actor logic).
//
// * FEvent   something the host tells the logic (notify, montage ended, input action ...);
//            queued on the game thread, handled at the start of the logic's next update;
// * FCommand something the logic asks the host to do (play a montage, change the walk speed ...);
//            queued by the update (any thread), executed on the game thread after it;
// * FHit     the result of a host trace (a reduced FHitResult).
//
// The ids (montage, notify, action, section) are indices into tables the generator writes into the
// logic's .cpp.  Do not edit; regenerate with the compiler.
#pragma once

#include "CoreMinimal.h"

#define DAL_HOST_VERSION 1

namespace DalHost
{
	enum class EEventKind : uint8
	{
		Notify,
		NotifyBegin,
		NotifyEnd,
		MontageEnded,
		MontageBlendingOut,
		Input,
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
	};

	enum class ECommand : uint8
	{
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

		inline FCommand SetMovementMode(int32 Mode)
		{
			FCommand C = Make(ECommand::SetMovementMode);
			C.Mode = Mode;
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
	} // namespace Cmd

	/** Result of a trace; a miss has bBlocking == false, Point == the trace end, Distance == 0 and Time == 1. */
	struct FHit
	{
		bool bBlocking = false;
		FVector3f Point = FVector3f(0.0f, 0.0f, 0.0f);
		FVector3f Normal = FVector3f(0.0f, 0.0f, 0.0f);
		float Distance = 0.0f;
		float Time = 1.0f;
	};
} // namespace DalHost
