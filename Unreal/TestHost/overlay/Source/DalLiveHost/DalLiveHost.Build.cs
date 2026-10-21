// Test host for DalLive (DreamAnimLogic). The template's variants are dropped.

using UnrealBuildTool;

public class DalLiveHost : ModuleRules
{
	public DalLiveHost(ReadOnlyTargetRules Target) : base(Target)
	{
		PCHUsage = PCHUsageMode.UseExplicitOrSharedPCHs;

		PublicDependencyModuleNames.AddRange(new string[] {
			"Core",
			"CoreUObject",
			"Engine",
			"InputCore",
			"EnhancedInput",
			"UMG",
			"Slate"
		});

		PrivateDependencyModuleNames.AddRange(new string[] { });

		PublicIncludePaths.Add("DalLiveHost");
	}
}
