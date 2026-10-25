// Copyright DreamMoon. All Rights Reserved.

using UnrealBuildTool;

// The host's only module.  Without host_als.py it does nothing: a code project needs a primary game module, and the code project is what gives
// the host an editor target that builds the plugins in Plugins/.  host_als.py apply replaces this file with the one that follows (restore
// puts the original back): the module also carries the test character and the game mode that use the ALS logic generated from als/*.dal.
public class DreamAnimTestHost : ModuleRules
{
	public DreamAnimTestHost(ReadOnlyTargetRules Target) : base(Target)
	{
		PCHUsage = PCHUsageMode.UseExplicitOrSharedPCHs;

		// EnhancedInput: the generated .dal classes (Public/Dal/*.h, written by dab.ps1 dal) list it as a dependency.
		PublicDependencyModuleNames.AddRange(new string[] { "Core", "CoreUObject", "Engine", "EnhancedInput" });

		// The ALS module is the one generated from als/*.dal; DalRuntime is the native half of it.
		PublicDependencyModuleNames.AddRange(new string[] { "ALS", "DalRuntime", "GameplayTags", "InputCore", "AnimGraphRuntime" });
	}
}
