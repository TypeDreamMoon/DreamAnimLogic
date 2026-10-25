# AlsHost -- 生成的 ALS 在 UE 沙盒宿主里替换原版逻辑层

把 `als/*.dal` 生成的 C++（模块名 `ALS`，类名和属性名同 ALS 原版，`als-dal-rewrite.md` 决定 1）放进 UE 沙盒宿主 `I:\UnrealProject_Moon\DEV_58\DreamVEdAnimHost`（引擎 `I:\UnrealEngine\UE_5.8`），关掉原版 ALS 的 C++ 逻辑类，PIE 里跑，用 R1 的真值录制脚本录一遍，和原版 ALS 的真值录像（`G:\DCC\AlsTruth`）逐帧比。原生部分在 `../DalRuntime`。

**脚本都不弹窗口**：编辑器隐藏启动，优先级低于正常；UBT 限 8 个并行动作，同样低优先级。检查（`runtime_checks.py`）用 `-nullrhi`；录像用 `-RenderOffscreen -ResX=1024 -ResY=576`（`run_editor.ps1 -Rendered`）：有渲染器，没有窗口。原版 ALS 只对“被渲染”的网格整帧更新姿势（`VisibilityBasedAnimTickOption`），`-nullrhi` 下网格不算被渲染，蒙太奇的通知也不一样，所以拿它和有渲染器的原版录像比不公平（`cycle.ps1 -Windowless` 还保留，只在不在乎这点时用）。离屏渲染的原版录像和有真窗口录的真值帧数一致、各段同时开始；离屏跑得比开窗口快得多（十一条录像约 6 分钟）。

## 怎么替换（`host_als.py`）

两个模块不能都叫 `ALS`，两份同名类也不能共存，所以原版逻辑源码不能在构建里。UBT 编译模块目录下所有 `.cpp`，UHT 读所有头文件，所以“关掉”就是“不在 `Source/` 下”。`host_als.py apply` 做的事：

| 动作 | 内容 |
|---|---|
| 移出构建 | 原版的 `AlsCharacter*`、`AlsCharacterMovementComponent`、`AlsAnimationInstance`（+ 代理）、`AlsLinkedAnimationInstance`、根运动源、全部 `State/`、全部 `Settings/`、`AlsGameplayTags`、五个通知类（`FootstepEffects`、`SetGroundedEntryMode`、`SetLocomotionAction`、`SetRootMotionScale`、`EarlyBlendOut`，它们调旧类；等 `.dal` 里的 `notify` 声明）共 29 项，原样搬到 `Plugins/ALS-Refactored/OriginalLogic/`（同样的相对路径） |
| 放进来 | 生成的 `*.h` → `Source/ALS/Public/Dal/`，`*.cpp` → `Source/ALS/Private/Dal/`（`DalHost.h` 不放：用 DalRuntime 的那份，登记表一个进程一份） |
| 改的文件 | `ALS.Build.cs`（加 `DalRuntime`、`EnhancedInput`、`InputCore` 和 `Public/Dal` 包含路径）、`ALS.uplugin`（去掉 `ALSCamera`、`ALSExtras` 两个模块——它们建在 `AAlsCharacter` 上，相机这轮不改写——加 `DalRuntime`）、`Public/State/AlsMovementDirection.h`（留着的 `AlsMath.h` 包含它；换成指向 `Dal/AlsLib.h` 的垫片） |
| 宿主工程 | `Source/DreamAnimTestHost/` 加 `DalAlsHostCharacter`（`ADalTestCharacter` + `UAlsCharacter` + `UAlsInput` + `UDalRagdollService` + 钩子探针 + 弹簧臂和相机；胶囊、网格、动画蓝图、设置资产取自从原版 `B_Als_Character` 导出的默认值）、`DalAlsHostGameMode`、`DalHookProbe`；`DreamAnimTestHost.Build.cs` 加依赖；`DalAlsHostCharacter` 还替原版蓝图 `B_Als_Character` 做一件事：监视逻辑的 `OverlayMode`，变了就把对应叠加层动画实例类（`AB_Als_<Mode>`，13 个，表在 `OverlayAnimationInstanceClasses`）的层链接进网格的动画实例（`LinkAnimClassLayers`，原版是 `OnOverlayModeChanged` 里的 Blueprint 事件，生成的逻辑没有这个事件）；网格的 tick 排在角色之后；`DreamAnimTestHost.uproject` 加 `DalRuntime` 插件（这一条手工加的，不在脚本里） |
| 宿主里的录制器 | `Plugins/DreamVEdForUnreal/.../DreamVEdAnimTruth.h/.cpp`（宿主拷贝，不是 DevTest 的源）：真值通道的 `Char.*` 除了角色的原生类链，还导出 `/Script/ALS` 模块里的原生组件（`UAlsCharacter`、`UAlsInput`）的属性，键名同原版。`DreamVEdAnimRecorder.h/.cpp` 另外让 `var:` 监视项能解析主动画实例的纯函数（`var:GetControlRigInput`：不带成员的写法展开成每个标量叶一条监视，采样时 `ProcessEvent` 调一次）；这一处也复制回了 DevTest 的源（`DreamVEdAnimRecorder.*`） |

`apply` / `restore` 都会删宿主的 `Intermediate/Build/SourceFileCache.bin` 和 makefile：搬来搬去之后 UBT 缓存的源文件表会漏文件，UHT 报“找不到 `EAlsMovementDirection`”（原版在 `restore` 之后构不过，删了就好）。

状态写在 `Plugins/ALS-Refactored/.dal_host_manifest.json`，`python host_als.py restore` 全部还原（搬回的文件、被改文件的原始内容都在里面）。手工还原：`git -C Plugins/ALS-Refactored checkout -- Source ALS.uplugin`，删 `OriginalLogic`、`Source/ALS/Public/Dal`、`Source/ALS/Private/Dal`、`.dal_host_manifest.json`。**不要** `git checkout -- .`：`Content/` 里是用 `.dab` 重建的动画蓝图，在 git 看来是“已修改”，这是故意的。还原宿主录制器：用 DevTest 的源（`DevTest/Plugins/DreamVEdForUnreal`）覆盖，或照 `Source/DreamVEdForUnrealEditor/Private/DreamVEdAnimTruth.*` 里 `CharComps` 的几处删掉。

## 用法

```
pwsh Unreal\AlsHost\scripts\cycle.ps1 -Rev worktree -Out <目录>        # 下面全部串起来（约 12 分钟）：换进去 + 构建、14 项检查、十一条录像（含布娃娃）、逐条比较、一张表；-WithC3 再跑 c3
pwsh Unreal\AlsHost\scripts\refresh_host.ps1                            # 下面前五步合在一起：生成（HEAD）、还原旧的、换进去、DalRuntime 拷进宿主、构建
python Unreal\AlsHost\gen_als.py --out G:\DCC\scratch\h5\gen            # als/*.dal 在 HEAD 的版本 -> C++（--module ALS_API --double-members）
python Unreal\AlsHost\host_als.py restore                               # 已经换过的话先还原
python Unreal\AlsHost\host_als.py apply --gen G:\DCC\scratch\h5\gen
robocopy Unreal\DalRuntime <宿主>\Plugins\DalRuntime /MIR /XD Binaries Intermediate
pwsh Unreal\AlsHost\scripts\build_host.ps1                              # UBT：DreamAnimTestHostEditor Win64 Development
pwsh Unreal\AlsHost\scripts\record_takes.ps1 -Takes lane,in_air -Out <目录>      # 录像 als_<take>.dvrec
python Unreal\AlsHost\scripts\compare_takes.py <目录>\als_lane.dvrec G:\DCC\AlsTruth\als_lane.dvrec --segments <Res>\r1_info_lane.json
pwsh Unreal\AlsHost\scripts\run_editor.ps1 -Script Unreal\AlsHost\scripts\runtime_checks.py -Fixed60   # DalRuntime 的 PIE 检查，结果 JSON 在 DALCHK_OUT
python Unreal\AlsHost\scripts\c3_host_compare.py --set als_ext          # Blender 一侧 c3 的 18 个 UE 确认场景（8 个要 DalTestCharacter）
```

`refresh_host.ps1` 把 DalRuntime 复制进宿主以后，把插件源码的修改时间更新到现在：UBT 靠和上一次构建的 .obj 比时间戳决定重编什么，robocopy 保留源文件的时间，换成较旧的版本（旧提交、`git archive` 打的包）时头文件比 .obj 还旧，模块被当作最新而沿用另一个版本的 .obj（`DalHost.h` 里 `FHit` / `FCommand` 的布局变了就会在运行时崩溃，`UDalHookProbe`）。

double 类型之后（`DAL_HOST_VERSION` 3，`dal-syntax-v3.md` 第 35 节）roll 的脚锁定和 mantle 的终点对原版：`Anim.FeetState.*.FinalLocation.Y` 最大差 1.632 → 0.0005 cm，`Actor.Location.Z` 最大差 0.250 → 0.0000000 cm；`cycle.ps1` 的整轮（十条录像）位置 / 速度 / 偏航全 0.0，角色离散字段 0 帧不符。

`gen_als.py` 默认取 HEAD 的 `.dal`（别人在工作区里改 `als/` 不影响），`--rev worktree` 取工作区。生成时带的编译选项在它的 `ASSET_COMPAT`：`--double-members`（Control Rig 的引脚）、`--object-settings AlsTurnInPlaceSettings`（原地转的 8 个设置在资产里是 `Instanced` 的 `UObject`）、`--native-struct AlsFloatInterval=FFloatInterval=Math/Interval.h`、四条 `--channel-default`（原版构造函数设的碰撞通道默认值，资产里没存）。没有它们，生成的设置类型读不进原版资产里的东西：原地转不播、攀爬打不到任何东西、落地预测扫不到地面。

| 文件 | 内容 |
|---|---|
| `scripts/g_record.py` | 执行 `G:\DCC\AlsTruth\tools\r1_record.py`（去掉最后一行 `run(steps)`），所以路线、命令、录制调用和原版真值录像完全相同；只换了三件事：编辑器世界的世界设置游戏模式覆盖成 `ADalAlsHostGameMode`（不保存）；驱动脚本对角色的 `set_desired_gait`、`jump`、`start_rolling_grounded` 等原来是 `AAlsCharacter` 的方法，现在转给 `UAlsCharacter` 组件，其余仍给角色；不编译相机的两个动画蓝图（它们的父类在已关掉的 `ALSCamera` 里，编译会崩）；无渲染器时网格按“被渲染”的方式整帧更新（`AlwaysTickPoseAndRefreshBones`；`R1_RENDERED=1` 时不动网格的选项）；`R1_ORIGINAL=1` 时不挂生成的组件，直接驱动原版角色；`ragdoll` 录像（`add_ragdoll_take`：布娃娃开 / 停三次，中间夹一次起跳）是在 `r1_record.py` 的路线之外加的 |
| `scripts/compare_takes.py` | 比较，见下 |
| `scripts/summarize_takes.py`、`scripts/cycle.ps1` | 每条录像一行的表（位置 / 速度 / 偏航最大误差、离散状态不符、姿势、曲线）；`cycle.ps1` 把换进去、检查、录像、比较、汇总串成一条命令 |
| `scripts/phase_ref.py` | 写 `G:\DCC\AlsTruth\phase.json`：每条真值录像第一帧的世界时间（60 fps 的帧数）。**动画循环的相位**：图里待机 / 移动的循环是 160 帧（2.67 s），姿势取决于录像开始前动画实例更新了多少次（`Anim.UpdateCounter`，60 fps 下是世界时间的帧数减一两帧）；两次运行的计数差一个 160 的倍数时，姿势在 0.00–0.15 cm 内一致，不管是原版对原版、生成版对原版还是生成版对生成版，否则相差可达 2.4 cm（原版录了 3 次、生成版录了 4 次，10 条录像里的 186 对：两条录像的姿势距离和它们计数差对 160 取余的相位失配，相关系数 0.93）。`g_record.py` 的 `R1_PHASE=phase.json`（`record_takes.ps1 -Phase`，`cycle.ps1` 默认带上）在录制器启动前等到世界时间对 160 取余和真值那条相同，所以生成版的姿势能和真值逐帧比 |
| `scripts/compare_ragdoll.py` | 布娃娃录像不逐帧比（物理模拟不会逐位一致）：命令的帧、每段布娃娃的起止帧和长度、结束时胶囊相距多少、起身一秒后相距多少、状态机（`Char.LocomotionAction`、`Char.LocomotionMode`、`Cmc.MovementMode`）不同的帧数、起身蒙太奇的选择、`FinalRagdollPose` 握手、`FlailPlayRate`、姿势误差按段分；`cycle.ps1` 对 `ragdoll` 录像用它 |
| `scripts/dump_overlay.py`、`dump_montage.py`、`probe_mesh.py` | 排查用：导出 `B_Als_Character` 的叠加层动画类表、蒙太奇的槽和根运动轨迹、网格的 tick 选项与被渲染状态 |
| `scripts/runtime_checks.py` | DalRuntime 的 14 项 PIE 检查：钩子事件到达另一个模块里的接收者、落地带命中、蹲 / 站、瞬移、通知经路由器、挡输入、锁模式、根运动覆盖、布娃娃开 / 停、空中蹲保持脚底 |
| `scripts/dump_settings.py`、`scripts/diff_dumps.py` | 把 8 个设置数据资产（`CS_Als_Default`、`MS_Als_Normal` / `Responsive`、四个 `Mantle`、`AIS_Als_Default`）按引擎读进来的样子完整导出成 JSON（环境变量 `DUMP_OUT`），再比两份。改了设置类型（新增 / 改类型）之后要做：`host_als.py restore`、构建、`run_editor.ps1 -Script dump_settings.py`（原版的）、`apply`（或 `refresh_host.ps1`）、再导一份、`diff_dumps.py orig.json gen.json`；只应剩表示上的差别（响应容器 ↔ 通道数组、`Vector2f` 的分量看不到） |
| `scripts/c3_host_compare.py` | 把 `blender-moon-engine/moon/tools/c3_ue_compare.py`（写死了 DevTest）指到沙盒宿主；只读地 import，不写字节码 |
| `overlay/` | 放进宿主模块的源码 |

## 比较的做法

两条录像用同一份脚本路线、固定 60 Hz，第 f 帧是路线上同一时刻（各段起始帧完全一致）。自由运行比较，不强制：

- 角色：位置 / 偏航 / 速度 / 移动模式；`Char.*`（原版 `AAlsCharacter` 的成员 ↔ 生成的 `UAlsCharacter` 的 state，键名相同，原版放在 CMC 里的 `GaitAmount`、`MaxAllowedGait`、`RotationMode`、`Stance` 对到 `Char.*`，`Cmc.MaxAccelerationWalking` 对到 `Cmc.MaxAccelerationWalkingApplied`）；
- 动画实例：`Anim.*` 按状态结构分组；
- 推给 CMC 的参数（`post_cmc`、`post_char` 两个采样点）；
- 输出：全部骨骼的组件空间姿势（平移 cm、旋转度）、动画曲线。

数字：每个字段的最大绝对差、均方根、最大差的帧、首次超过容差的帧；枚举 / 标签 / 布尔：不同的帧数和第一帧。`--segments` 按路线段拆。

## 结果

见 `dal-syntax-v3.md` 第 32 节和 `als-dal-rewrite.md` §7.1 的 H5 状态。
