// DalLive: hot reload of .dal logic in the editor (editor Python interpreter + a thin C++ host component).

using UnrealBuildTool;

public class DalLive : ModuleRules
{
	public DalLive(ReadOnlyTargetRules Target) : base(Target)
	{
		PCHUsage = PCHUsageMode.UseExplicitOrSharedPCHs;

		PublicDependencyModuleNames.AddRange(new string[] {
			"Core",
			"CoreUObject",
			"Engine",
			"InputCore",
			"EnhancedInput"
		});

		if (Target.bBuildEditor)
		{
			// Toasts and the Message Log page for compile errors (editor only; the component itself runs everywhere).
			PrivateDependencyModuleNames.AddRange(new string[] {
				"Slate",
				"SlateCore",
				"MessageLog"
			});
		}
	}
}
