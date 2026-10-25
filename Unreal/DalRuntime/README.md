# DalRuntime -- `.dal` 生成的 C++ 依赖的原生模块

`python -m dal compile ... --cpp` 生成的 C++ 只是逻辑层（`UActorComponent` / `UAnimInstance` 子类）。有些事 `.dal` 写不了，也不该写：改造过的行走物理、引擎的根运动源对象、布娃娃的物理开关、角色的钩子事件。这些放在这个插件的运行时模块 `DalRuntime` 里，生成物依赖它（生成头文件第三行 `Module dependencies: ... DalRuntime`）。设计见 `I:\DCC\Plan\als-dal-rewrite.md` §5，Blender 一侧的对应物见 `I:\DCC\Plan\anim-3c-design.md` §11。

状态：版本 0.1.0。2026-10-25 在 UE 5.8.2 里编译通过，生成的 ALS（`als/*.dal`）在 PIE 里跑通，对原版 ALS 的真值录像逐帧对照过（数字见 `Unreal/AlsHost/README.md`）。

## 内容

| 文件 | 是什么 | 谁用它 |
|---|---|---|
| `DalCharacterMovementComponent.h/.cpp` | `UDalCharacterMovementComponent`：CMC 子类。ALS 的 CMC 扩展：构造函数默认值、`bMovementModeLocked`（`SetMovementMode` 守卫，组件自己的调用也走它）、`bInputBlocked`、地面 `GetMaxAcceleration`、穿插前速度（读一次就清）、自定义移动模式（只按根运动移动）、`SetRootMotionOverride` / `ClearRootMotionOverride`；钩子事件（模式变化、起跳、落地带 `hit`、蹲 / 站起、瞬移、`PhysicsRotation`）投递给 `FEventRouter` | `set_max_acceleration_walking`、`set_input_blocked`、`set_movement_mode_locked`、`root_motion_override` / `root_motion_clear` 命令；读 `pre_penetration_velocity`；`on movement_mode_changed` 等处理器 |
| `DalRootMotionSource_Override.h/.cpp` | `FDalRootMotionSource_Override`：优先级 1000、没有时长、`Override` 累加模式的根运动源，根运动就是 `FTransform(旋转差, 速度)`。ALS 攀爬每帧算一次目标位置，把 `(目标 - 当前) / dt` 交给它（`AlsRootMotionSource_Mantling.cpp:117-127` 的做法，蒙太奇同步和变换插值留在 `.dal`） | CMC |
| `DalRagdollService.h/.cpp` | `UDalRagdollService`：布娃娃的物理一半。`Begin(限速, 速度)`（分离网格、关胶囊碰撞、开网格物理、前几帧限速）、`End()`（反过来，网格贴回胶囊）、`LimitSpeed`、`SetMotorStrength`、`AddForce`、`IsSupported()`（物理资产有 pelvis 和 spine_03 刚体，即 `ragdoll_supported`）；三个读：`GetPelvisLocation()`（骨盆刚体的世界位置）、`GetPelvisVelocity()`（线速度）、`IsFacingUp()`（骨盆骨的 roll 展开到 ±180° 后 ≤ 0，ALS 用它选起身蒙太奇），对应宿主读 `ragdoll_pelvis_location` / `ragdoll_velocity` / `ragdoll_facing_up`（ALS 读物理刚体，不读骨骼：骨骼比模拟慢一拍）。什么时候开 / 停、胶囊跟骨盆、起身朝向、姿势快照、起身蒙太奇在 `.dal` 里 | `ragdoll_*` 命令和三个 `ragdoll_*` 读 |
| `DalHost.h`（拷贝） | 生成物的宿主类型（`FEvent` / `FCommand` / `FHit` / `IEventSink`）和 `FEventRouter`。**这里的 `FEventRouter` 是导出的类**（`DAL_ROUTER_EXTERNAL=1`，由本模块 `Build.cs` 对依赖它的模块定义），状态在 `DalEventRouter.cpp`：登记表一个进程一份。头文件里的版本（没有 DalRuntime 的工程、测试替身）是内联的。`dal/cpp/DalHost.h` 是源，`tests/test_dalruntime.py` 检查两份一致 | 生成的逻辑（`FEventRouter::Register`）、CMC（`Post`）、生成的通知类（`PostNotify`） |
| `DalTestCharacter.h/.cpp` | `ADalTestCharacter`：只有 `UDalCharacterMovementComponent` 的 `ACharacter`，加 `SetMovementModeLocked` / `SetInputBlocked` / `RootMotionOverride(FVector, FQuat)` / `RootMotionClear` 四个 UFUNCTION。Blender 一侧 c3 对照场景的 `als_cmc` 角色（`/Script/DalRuntime.DalTestCharacter`，`anim-3c-design.md` §11.5） | `moon/tools/c3_ue_compare.py --set als_ext`（`Unreal/AlsHost/scripts/c3_host_compare.py` 指向沙盒宿主） |

没有放在这里的：动画实例的代理（`F<Logic>Proxy : FAnimInstanceProxy`，`post_flush` 的钩子）由编译器按逻辑生成；通知类（`notify` 声明，`cpp "类名"`）、姿势快照（`snapshot_pose`）、动态蒙太奇命令也是生成的，用的都是引擎自己的 API。

## 和 ALS 原版的差别

ALS（Sixze，MIT）的 `AlsCharacterMovementComponent.cpp` 里有两个函数是引擎函数的修改拷贝（`PhysWalking` 265 行、`ComputeFloorDist` 173 行）。这里**没有拷贝引擎代码**：ALS 对它们的三处改动都做成了包着引擎函数的覆写，只用引擎的公有 / 受保护接口：

| ALS 的改动 | 这里 |
|---|---|
| 地面检查一开始就在穿插、而地板可走时也解穿插（引擎只在地板不可走时才推出） | `PhysWalking` 之后，`bAllowImprovedPenetrationAdjustment` 且地板可走且 `HitResult.bStartPenetrating` 时调 `GetPenetrationAdjustment` + `ResolvePenetration`（和引擎同样的三行） |
| 记下穿插调整之前走的速度 `PrePenetrationAdjustmentVelocity` | 覆写 `CalcVelocity`，`PhysWalking` 里每个子步记下它之后的 `Velocity`（最后一个胜出），`PhysWalking` 结束时若没瞬移、没根运动再提交 |
| 地板由线追踪给出时保留扫描的 `Normal` / `PenetrationDepth` / `bStartPenetrating` / `HitObjectHandle` | 覆写 `ComputeFloorDist`：调基类；若结果是线追踪，再用 `LineDistance = 0` 调一次基类，只做扫描，把那四项抄回 |

ALS 在 `PhysWalking` 开头刷新移动设置（走速、加速度、制动、摩擦），`.dal` 版本在 `after_movement` 槽末尾推。差一帧的情形：加速度。引擎在 `ControlledCharacterMove` 里用 `GetMaxAcceleration()` 缩放输入向量，在 `PhysWalking` **之前**，所以原版用的是上一步 `PhysWalking` 开头刷出来的加速度，不是当前速度对应的。推过来的值如果立刻生效，会早一步。所以 `MaxAccelerationWalking` 是“最近推的值”，`MaxAccelerationWalkingApplied` 是 `GetMaxAcceleration()` 在地面上返回的值，`PhysWalking` 开头才把前者抄给后者（冲刺起步的 3.14 cm 轨迹误差因此变成 0）。走速 / 制动 / 摩擦是 `PhysWalking` 里才读的，早写没有差别。剩下的差一帧：蹲 / 站起——原版在 CMC 的 `UpdateCharacterStateBeforeMovement` 里调 `OnStartCrouch`，角色立刻改姿态、同一步的 `PhysWalking` 用新姿态的走速；`.dal` 版本在角色 tick 里才看到（`is_crouching` 轮询或钩子事件，都在 CMC 之后）。录像里 1651 / 1951 两帧的推送值差一帧就是它（最大位置误差 0.69 cm）。

## 构建

沙盒宿主 `DreamVEdAnimHost` 里的用法见 `Unreal/AlsHost/README.md`。别的工程：把 `Unreal/DalRuntime` 拷到 `<Project>/Plugins/`（`Source/DalRuntime/Public/DalHost.h` 是 `dal/cpp/DalHost.h` 的拷贝，改了 `dal/cpp/DalHost.h` 要同步，测试会提醒），生成的模块 `Build.cs` 里加 `DalRuntime`（`PublicDependencyModuleNames`）。依赖：`Core`、`CoreUObject`、`Engine`、`PhysicsCore`、`GameplayTags`。

## 没验证的

- 网络：整个插件按单机写，没有预测数据、`FSavedMove`、复制。
- `UDalRagdollService::Begin` 的限速帧数、`End` 里恢复网格相对位置的做法照 ALS 写，没有和原版 ALS 的布娃娃录像对过（真值录像里没有布娃娃）；PIE 里只确认了开 / 停 / 下落 / 加力。
- 移动基座（`UpdateBasedRotation`、`ConsumeInputVector` 里的基座旋转补偿）没有移植。
