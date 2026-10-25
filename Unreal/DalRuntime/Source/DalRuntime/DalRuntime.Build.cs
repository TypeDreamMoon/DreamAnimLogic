// DalRuntime: the native support code of the C++ the dal compiler generates (UDalCharacterMovementComponent, FDalRootMotionSource_Override,
// UDalRagdollService, the hook events of the character).  See Unreal/DalRuntime/README.md.

using UnrealBuildTool;

public class DalRuntime : ModuleRules
{
	public DalRuntime(ReadOnlyTargetRules Target) : base(Target)
	{
		PCHUsage = PCHUsageMode.UseExplicitOrSharedPCHs;

		PublicDependencyModuleNames.AddRange(new string[] {
			"Core",
			"CoreUObject",
			"Engine",
			"PhysicsCore",
			"GameplayTags"
		});

		// DalHost.h: the event router (the registry of the logics that receive the hooks) is one object in this module, not one per
		// module that includes the header.  Every module that depends on DalRuntime sees the definition.
		PublicDefinitions.Add("DAL_ROUTER_EXTERNAL=1");
	}
}
