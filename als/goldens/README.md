# ALS 数学库黄金值（T1）

这个目录里是 **UE 5.8 编辑器里真实编译的 ALS 函数**算出来的黄金值，用来离线回归测试 DAL（`.dal`）对 ALS 数学库的改写（`examples/als/als_lib.dal`、`dal/builtins.py`、`dal/runtime.py`）。对应 `Plan/als-dal-rewrite.md` §7.1 的 T1，以及 §1.9 陷阱 T6（除零审计）、T10（float32 与 double）。

- 引擎：`5.8.2-56702186+++UE5+Release-5.8`（`index.json` 里也有）
- 宿主工程：`DreamAnimTestHost`（`I:\UnrealProject_Moon\DEV_58\DreamVEdAnimHost\`），ALS-Refactored 插件版本 4.18，二进制是宿主里已编译好的那份；没有改动宿主的任何文件，没有保存任何资产。
- 生成方式：`UnrealEditor-Cmd.exe ... -run=pythonscript -script=gen_goldens.py`，在编辑器 Python 里调用 `unreal.AlsMath` / `unreal.AlsRotation` / `unreal.AlsVector` / `unreal.AlsUtility` / `unreal.AlsMontageUtility` 以及 `unreal.MathLibrary`。固定随机种子，没有时间戳，重复运行得到逐字节相同的文件（已验证）。
- 全部输入是“随机（固定种子）+ 手挑边界”两类，边界用例的 `tag` 以 `edge:` 开头。

## 1. 文件清单

| 文件 | 内容 | 函数数 | 用例数 | 大小 |
|---|---|---|---|---|
| `als_math.json` | `UAlsMath`：Clamp01、LerpClamped、DamperExactAlpha、SpringDamperFloat（单步 + 11 条连续序列）、CalculateMovementDirection、TryCalculatePoleVector | 6 | 1300 + 513 步 | 288 KB |
| `als_rotation.json` | `UAlsRotation`：RemapAngle…、LerpAngle、LerpRotation、InterpolateAngleConstant、DamperExactAngle、DamperExactRotation、InterpolateQuaternionFast、GetTwist | 8 | 1603 | 299 KB |
| `als_vector.json` | `UAlsVector`：ClampMagnitude01（FVector）、ClampMagnitude012D、Radian/AngleToDirection（2D 与 XY）、DirectionToAngle（2D 与 XY）、PerpendicularClockwise/CounterClockwiseXY、AngleBetweenSkipNormalization、AngleBetweenSignedXY、SlerpSkipNormalization、SpringDamperVector（单步 + 4 条序列） | 14 | 2249 + 185 步 | 419 KB |
| `als_utility.json` | `UAlsUtility`：NameToDisplayString、GetSimpleTagName、GetChildTags（ALS 的标签） | 3 | 145 | 17 KB |
| `als_montage.json` | `UAlsMontageUtility`：ExtractRootTransformFromMontage / ExtractLastRootTransformFromMontage / ExtractRootMotionFromMontage，12 个蒙太奇 | 3 | 360 | 61 KB |
| `ue_math.json` | 引擎函数（`unreal.MathLibrary`）：ALS C++ 直接调用、DAL 内建函数对应的那些 | 58 | 2039 | 325 KB |
| `index.json` | 引擎版本、工程、生成脚本、每个文件的函数列表与用例数、不可调用函数清单、引擎函数可达性 | | | 25 KB |
| `gen_goldens.py` | 生成脚本（编辑器端，文件头有运行方法） | | | |
| `sanity_check.py` | 纯标准库的自检脚本：用 float32 逐运算舍入重新实现 C++ 并和黄金值比对（见 §8） | | | |

数据文件合计约 1.41 MB。`als_math.json` 的 `UAlsMath::SpringDamperFloat` 与 `als_vector.json` 的 `UAlsVector::SpringDamperVector` 体积最大（带序列）。

## 2. JSON 结构

### 2.1 顶层

```json
{"schema":"als-goldens/1","engine":"5.8.2-...","project":"DreamAnimTestHost","als_plugin_version":"4.18","generator":"gen_goldens.py","file":"als_math.json","functions":[ ... ]}
```

文件每个函数占若干行，**一个用例一行**，可以直接 `json.load`，也可以按行 grep。

### 2.2 函数条目

```json
{"function":"UAlsRotation::LerpAngle","python":"AlsRotation.lerp_angle","signature":"float LerpAngle(float From, float To, float Ratio)",
 "params":["From","To","Ratio"],"param_types":{"From":"float32","To":"float32","Ratio":"float32"},"returns":"float32",
 "note":"...","cases":[ ... ]}
```

- `params`：C++ 参数名（按调用顺序）；`in` 里的键就是这些名字。
- `param_types` / `returns` 的取值：`float32`、`float64`、`vec3`（FVector，[x,y,z]）、`vec3f`（FVector3f）、`vec2`（FVector2D）、`rotator`（[pitch,yaw,roll]）、`quat`（[x,y,z,w]）、`bool`、`name`、`tag`（输入是标签名字符串）、`enum_move_dir`、`pole`、`tags`、`str`、`transform`。
- `note`：这个函数的实现要点和 UE 实际行为（强烈建议读一遍）。
- `als_math.json` / `als_vector.json` 里的弹簧函数另有 `state_type` 和 `sequences`（§2.4）；`ue_math.json` 的条目有 `dal_builtin`（对应的 DAL 内建函数名，没有就不出现）。

### 2.3 用例

```json
{"in":{"From":170.0,"To":-170.0,"Ratio":0.5},"out":180.0,"tag":"edge:pair_170_-170_ratio_0.5"}
```

- `tag`：`random`、`random:<子类>`（如 `random:wide`、`random:nearly_parallel`）、`edge:<原因>`（边界 / 退化 / 除零 / NaN 相邻）、`fixed`（工具类函数的固定表）。
- `out` 的形状由 `returns` 决定：
  - 标量：数字，或字符串 `"nan"` / `"inf"` / `"-inf"`。
  - 向量 / 旋转器 / 四元数：数组（顺序见 §3.4）。
  - `enum_move_dir`：`{"name":"Forward","value":0}`；`EAlsMovementDirection` 的整数值：Forward=0、Backward=1、Left=2、Right=3。
  - `pole`（TryCalculatePoleVector）：`[bool, ProjectionLocation, PoleDirection]`；返回 false 时后两项是 `null`（原因见 §5.2）。
  - `tags`（GetChildTags）：标签名字符串数组（UE 返回的顺序；另有 `out_sorted`，比较请按集合比）。
  - `name` / `str`：字符串（GetSimpleTagName 找不到标签时是 `"None"`）。
  - `transform`（montage 文件）：`{"translation":[x,y,z],"rotation":[x,y,z,w],"scale":[x,y,z]}`。
- 如果 UE 调用抛了异常，用例里会多一个 `error` 字段且 `out` 为 `null`。这一版所有用例都没有出错（`sanity_check.py` 会检查）。

### 2.4 弹簧（有状态）函数

`FAlsSpringFloatState` / `FAlsSpringVectorState` 有三个字段：`Velocity`、`PreviousTarget`、`bStateValid`。**Python 调用不会就地修改传入的状态结构**：`unreal.AlsMath.spring_damper_float(state, ...)` 返回 `(value, new_state)`，新状态在返回值里（已用“连续两次调用”核对过：用返回的状态继续调用才得到连续的轨迹；传回旧状态对象会被当成无效状态）。因此链式序列里每一步都用上一步返回的状态。

单步用例：

```json
{"in":{"Current":8.0,"Target":12.0,"DeltaTime":0.0166..,"Frequency":5.0,"DampingRatio":1.0,"TargetVelocityAmount":1.0},
 "state_in":{"Velocity":3.0,"PreviousTarget":10.0,"bStateValid":true},
 "out":9.0,"state_out":{"Velocity":125.66,"PreviousTarget":12.0,"bStateValid":true},"tag":"edge:..."}
```

序列（`sequences`，每个函数条目里的一个数组）：

```json
{"tag":"chain:critical_step","in":{"Frequency":4.0,"DampingRatio":1.0,"TargetVelocityAmount":1.0},
 "current_start":0.0,"state_start":{"Velocity":0.0,"PreviousTarget":0.0,"bStateValid":false},
 "events":[{"step":25,"op":"Reset"}],
 "columns":["Target","DeltaTime","out","state_out.Velocity","state_out.PreviousTarget","state_out.bStateValid"],
 "feedback":"...","rows":[[0.0,0.0166..,0.0,0.0,0.0,true], ...]}
```

- 第 0 步的 `Current` 是 `current_start`，之后每一步的 `Current` 是上一步的 `out`；状态同理（第 0 步用 `state_start`，之后用上一步的 `state_out`）。
- `events` 里的 `Reset` 表示在该步之前把状态换成 `{0, 0, false}`（等价于 `State.Reset()`）。
- 序列里有：临界阻尼 / 欠阻尼 / 过阻尼 / 无阻尼（阻尼 0）/ 频率 0 / `TargetVelocityAmount` 0 与 0.5 / 变 dt（含 `dt=0`、`dt=1e-8` 的步）/ 中途 Reset / 大 dt（0.25）/ 起点不等于目标等组合；浮点 11 条（20 到 60 步，dt=1/60 为主）、向量 4 条。第 0 步都是 `bStateValid=false`（返回 Target 并建立状态）。
- 向量版的 `Current` / `Target` / `out` / `Velocity` / `PreviousTarget` 都是 `[x,y,z]`（double）。

### 2.5 `als_montage.json`

`functions`（三个函数的签名）、`skipped`（没能用的蒙太奇，这一版为空）、`all_anim_montages_in_registry`（资产注册表里全部 15 个 AnimMontage）和 `montages`（12 个）。每个蒙太奇：

```json
{"montage":"/ALS/ALS/Animations/Actions/Mantle/AM_Als_Mantle_High","play_length":2.6666667461395264,"slot_name":"PostLocomotion",
 "segments":[{"anim":"...","start_pos":0.0,"anim_start_time":0.0,"anim_end_time":2.666...,"play_rate":1.0,"looping_count":1}],
 "root_transform_samples":[{"Time":0.0,"out":{transform},"tag":"sample_0_of_15"}, ... 16 个均匀样本（含 0 和 play_length）+ 3 个边界: time_negative / time_beyond_length / time_just_below_length],
 "last_root_transform":{transform},
 "root_motion_ranges":[{"StartTime":0.0,"EndTime":2.67,"out":{transform},"tag":"full"}, ... 10 对: full / first_half / second_half / quarter_to_three_quarters / first_0.1_s / 0.1_to_0.2_s / zero_length_range / end_beyond_length / start_negative / last_frame_step_1/30]}
```

选了 9 个 ALS 蒙太奇（`AM_Als_Mantle_High/Low/Low_BothArms/Low_Box/Low_Left/Low_Right`、`AM_Als_Roll`、`AM_Als_GetUp_Back/Front`）加 3 个 MoverExamples 的（`VaultOver_Montage`、`MM_Dash_Forward_Montage`、`MM_Death_Front_03_ExtractedRM_Montage`，用来多测几种根运动）；注册表里另外 3 个 MoverExamples 冲刺蒙太奇没有取。结果是“组件空间”的变换。自检：`ExtractLast…` 等于 `Time=play_length` 的样本；`full` 区间的根运动等于 `last - first`（12 个蒙太奇全部成立）。

### 2.6 `index.json`

`files.<文件名>.functions[]`（函数名、python 名、`cases`、`sequences`、`sequence_steps`）、`bytes`、`errors`；`not_callable_from_python`（§6）；`engine_functions_reachability`（§7）。

## 3. 数值规则

### 3.1 float32 输入

ALS 的 `float` 参数在 C++ 里是 float32。Python 的 float 是 double，调用时会被舍入成 float32。生成脚本**先**把所有 float32 类型的输入舍入成 float32 再构造用例，存进 JSON 的就是舍入后的值，所以 DAL 测试喂进去的输入和 UE 看到的输入逐位相同。

- double 类型的参数（`FVector`、`FRotator`、`FQuat`、`FVector2D`、`double`）的随机输入**也**取 float32 可表示的值，这样 float32 实现看到相同的输入。
- 例外（保留真正的 double）：`tag` 含 `double_precision` 的用例，以及超出 float32 范围的边界值（如 `1e154`、`1e155`、`1e200`，用来测 double 的平方溢出）。`sanity_check.py` 会检查这条规则。
- `vec3f`（`AngleBetweenSignedXY` 的 `FVector3f`）是真正的 float32 向量，Python 类型是 `unreal.Vector3f`。

### 3.2 输出精度

- 返回 `float` 的函数：Python 拿到的是精确的 float32 值（以 double 表示），JSON 里是 `repr` 的完整往返精度，例如 `0.30000001192092896`。读回来是同一个 double，也就是同一个 float32。
- 返回 `double` 的函数（`DirectionToAngle`、`DirectionToAngleXY`、`AngleBetweenSkipNormalization`）和 `FVector` / `FRotator` / `FQuat` / `FVector2D` 返回值是 double 精度，但**内部常混有 float 运算**（比如 `RadianToDirection` 的 cos / sin 是 float 算出来再放进 FVector2D；弹簧的指数近似系数是 float）。所以别假定“double 返回值 = 精确数学值”。
- 带符号的零保留：`-0.0` 写成 `-0.0`，比较时请注意（`Clamp01(-0.0)` 是 `+0.0`，不是 `-0.0`）。
- 引擎函数文件 `ue_math.json` 里 `unreal.MathLibrary`（UKismetMathLibrary）的 “float” 引脚是 double，计算也是 double，而 ALS 的 C++ 调用的是 float32 重载。所以 `ue_math.json` 要用容差比（约 1e-5 相对），不要要求逐位相等。

### 3.3 NaN / inf 的编码

JSON 没有 NaN / inf，统一写成字符串：`"nan"`、`"inf"`、`"-inf"`（输入和输出都一样，数组里的元素也一样）。NaN 不区分符号位和载荷。负零写 `-0.0`。读取时用类似

```python
def dec(x):
    if isinstance(x, str): return {"nan": float("nan"), "inf": float("inf"), "-inf": float("-inf")}.get(x, x)
    if isinstance(x, list): return [dec(i) for i in x]
    if isinstance(x, dict): return {k: dec(v) for k, v in x.items()}
    return x
```

NaN 输入只用在标量函数上（FVector / FQuat 路径上不放 NaN，编辑器构建在那里可能跑 NaN 诊断）。

### 3.4 数组顺序

- `FVector`：`[x, y, z]`；`FVector2D`：`[x, y]`。
- `FRotator`：`[pitch, yaw, roll]`（UE 的 FRotator 成员顺序）。注意 `unreal.Rotator(...)` 的位置参数顺序是 `(roll, pitch, yaw)`，生成脚本用关键字参数避免弄错。
- `FQuat`：`[x, y, z, w]`。

## 4. DAL 测试怎么用

1. 读文件，`dec()` 解码；对每个函数找到 DAL 里对应的实现（`function` / `python` / `signature` 给出 C++ 和 Python 名；`ue_math.json` 里有 `dal_builtin`）。
2. 对每个用例：用 `in` 里的值（已经是 float32 精确值）调用 DAL 实现，和 `out` 比。
3. 比较规则：
   - **返回 float32 的函数**（Clamp01、LerpClamped、DamperExactAlpha、RemapAngle…、LerpAngle、InterpolateAngleConstant、DamperExactAngle、AngleBetweenSignedXY、SpringDamperFloat；CalculateMovementDirection 比枚举值）：如果 DAL 的 C++ 后端用 `float` 且每步舍入，可以要求**逐位相等**——`sanity_check.py` 用“每个运算都舍入到 float32”的纯 Python 重新实现，与这些函数的黄金值**逐位一致**（说明这些函数在 ALS 二进制里没有被编译成 FMA 融合，每个 float 运算单独舍入）。DAL 的 Python 解释器若用 double 计算，建议容差 1e-4（角度 1e-3 度）；离 175 / 180 / ±180 这些边界几个 ulp 的用例 `tag` 带 `1ulp` / `special_angle`，那里 double 与 float32 可能落在边界的两侧，应当按 float32 复现或者豁免。
   - **NaN**：两边都是 NaN 视为相等；`inf` 要同号；`-0.0` 与 `0.0` 视为相等（除非你特意测符号）。
   - **枚举**：比 `value`。
   - **标签集合**：`GetChildTags` 比集合（用 `out_sorted`）。
   - **ue_math.json**：容差 1e-5 相对（double 引擎函数 vs DAL 的 float32 / double），边界用例看 `note`。
4. 弹簧单步：同时比 `out` 和 `state_out`。弹簧序列：**用“教师强制”**——每一步用记录的 `Current` 和 `state`（上一行的 `out` 和 `state_out`）喂 DAL，而不是用 DAL 自己上一步的输出，否则误差会累积（对应计划 T10）；要测累积可以另做一遍自由运行并放宽容差。
5. `edge:` 用例里有很多“DAL 目前会和 C++ 不同”的有意义的情形（`.dal` 的除零得 0，C++ 得 inf / NaN）：`DamperExactAlpha` 的 `HalfLife = -1e-8` + `DeltaTime = 0` 是 NaN；`SpringDamper` 的 `DeltaTime <= 1e-8`；`ClampMagnitude01` 的 `1e155`；`AngleBetweenSignedXY` 的零向量……DAL 要么对上，要么在测试里显式列入“已知差异（有意）”清单。

## 5. 实测中值得注意的 UE 行为

### 5.1 弹簧状态不是就地修改

见 §2.4。`SpringDamperFloat` / `SpringDamperVector` 从 Python 看是 `(value, new_state)`。

### 5.2 `TryCalculatePoleVector`：返回 false 时拿不到输出

编辑器 Python 对“bool 返回 + 引用输出”的函数在 false 时返回 `None`，两个输出向量丢失（`call_method` 走同一条路，没有办法拿到）。所以 false 用例的 `out` 是 `[false, null, null]`。C++ 源码里 false 的行为是：A 与 B 几乎重合（`|AB|` 各分量 <= 1e-4）→ `ProjectionLocation = A`、`PoleDirection = 0`；A、B、C 共线（`B - Projection` 的平方长度 < 1e-8，不能归一化）→ `ProjectionLocation = A + AB 投影到 AC`、`PoleDirection = B - Projection`（未归一化）。A 与 C 重合（`AC` 不能归一化）**返回 true**，`ProjectionLocation = A`，`PoleDirection = 归一化的 AB`（已在黄金值里）。true 的 146 个随机 / 边界用例做过几何检查：极向量是单位向量、垂直于 AC、投影点在直线 AC 上、B 在极向量一侧。

### 5.3 `DamperExactAlpha` 是近似，不是精确的 `1 - 2^(-dt/hl)`

C++：`Alpha = 1 - InvExpApprox(ln2 / (HalfLife + 1e-8) * DeltaTime)`，`InvExpApprox(x) = 1 / (1 + 1.00746054 x + 0.45053901 x² + 0.25724632 x³)`。近似就是真值，**不要“修正”**。与精确公式的差：x = ln2·dt/hl 在 [0, 1] 内最大 4.1e-4；x 在 [0, 10] 内最大 1.7e-2；随机用例（hl >= 0.05，dt <= 0.1）最大 1.4e-3。边界行为（`edge:dt_…+hl_…` 共 80 个）：
- `HalfLife = 0` 时分母是 1e-8：dt >= 1/60 得 1.0；`dt = 1e-8` 得 0.5001。
- `HalfLife = -1e-8` 时分母恰好是 0：`dt = 0` 得 **NaN**（0 × inf），`dt > 0` 得 1.0。
- 负的半衰期**没有保护**：`dt = 1/60, hl = -0.5` 得 `-0.0236`（负的 alpha，往远离目标的方向走）；`dt = inf, hl < 0` 得 NaN。
- `dt = inf` 且 `hl > 0` 得 1.0；`dt = inf, hl = inf` 得 NaN；`hl = inf` 且 dt 有限得 0.0。
- `dt = 0` 恒为 0（除了上面那个 NaN，以及 `hl = NaN` 时得 NaN）；`dt = NaN` 恒为 NaN。

### 5.4 `FMath::UnwindDegrees` 对大角度 / inf 不终止

引擎实现是 `while (A > 180) A -= 360;` / `while (A < -180) A += 360;`。从源码看，`inf` 和 `|x| >= 约 8.6e9`（float 的 ulp 超过 720 时 `A - 360` 等于 `A`）会死循环（**没有执行验证**，会把编辑器卡死）。所以所有角度输入都有限且 `<= 1e7`。DAL 的 `unwind_degrees` 对大值改用 fmod，不会卡，结果对普通角度相同。`UnwindDegrees` 保留 ±180（范围是 [-180, 180]），而 `NormalizeAxis` 把 -180 映射成 +180（范围 (-180, 180]），这两个不要混用。

### 5.5 `FMath::SinCos(float)` 不是 libm

`RadianToDirection*` / `AngleToDirection*` 用的 `FMath::SinCos(float)` 是引擎自己的范围缩减 + 11 阶 / 10 阶 minimax 多项式（`UnrealMathUtility.h`）。与 libm 的最大差：`|x| <= 7` 为 2.2e-7，`|x| <= 100` 为 3.6e-7，`|x| <= 1000` 为 5.9e-6，`|x| = 1e7` 时差到 0.49（范围缩减里 float 精度耗尽）。例：`sin(float(π/2)) = 0.9999998807907104`（libm 得 1.0），`cos(float(π/2)) = -1.19e-7`。输入是 `nan` 时输出 `[nan, nan]`，输入是 `inf` 时输出 **`[inf, inf]`**（不是 NaN）。`sanity_check.py` 里的 `sincos_f32()` 是这个函数的 float32 逐运算重新实现，对黄金值 **逐位一致**（`|input| <= 1e5` 的全部用例），DAL 可以直接照抄，或者容差 1e-5（`|x| <= 100`）。对 `SlerpSkipNormalization` 也有影响：`Ratio = 1` 时结果不是恰好等于 `To`（例如 `(1,0,0)` 到 `(0,1,0)` 得 `(-1.19e-7, 0.99999988, 0)`）。

### 5.6 `FMath::Atan2(double)` 与 libm 位相同，但不管符号零

`DirectionToAngle` / `DirectionToAngleXY`（以及 `ue_math.json` 的 `FMath::Atan2`）在所有随机输入上**与 libm 的 `atan2` 逐位相同**，不同的只有符号零：`atan2(-0.0, -1)` 得 `+180`（libm 得 `-180`），`atan2(±0, ±0)` 得 `0`（libm 得 `±0` 或 `±180`）。DAL 的 `atan2`（`math.atan2`）要处理这几个边界（或豁免）。

### 5.7 `AngleBetweenSignedXY`：反向得 0，小向量被置零，acos 被 float32 量化

- 实现里用 `FMath::Sign(FromXY ^ ToXY)`，而 `FMath::Sign(0) = 0`，所以**恰好平行与恰好反向的向量都返回 0**（反向不是 ±180）。
- `FVector2f::Normalize()` 在 `|v|² <= 1e-8`（长度 <= 1e-4）时把 2D 向量**置零**（不是保持原样）：零向量、`(1e-5, 0)`、长度恰好 `1e-4` 的向量都得 0；长度 `1.01e-4` 就正常归一化。`1e20` 长度的向量（float 平方溢出成 inf，`InvSqrt(inf) = 0`）也得 0。
- 结果用 `acosf`，float32 的点积在 1 附近的分辨率是 6e-8，所以小于约 0.02°（acos(1 - 6e-8) = 3.45e-4 rad = 0.0198°）的角度要么得 0 要么被量化到 0.0198° 的倍数（`edge:3e-4_rad_ccw_acos_precision` 得 0.0198° 而不是 0.0172°；接近 180° 同理）。与 double 实现的差最大约 0.03°。
- `AngleBetweenSignedXY` 的 Python 参数是 `unreal.Vector3f`，Z 被忽略。

### 5.8 `Clamp01`、`LerpClamped`

`Clamp01(NaN) = 0`；`Clamp01(-0.0) = +0.0`；`Clamp01(±inf)` 是 1 / 0；非规格化数原样保留（没有 flush to zero）。`LerpClamped(0, inf, 0)` 是 NaN（`0 + inf * 0`）。

### 5.9 `CalculateMovementDirection`：C++ 是带阈值的重叠区间

C++（`AlsMath.cpp`）按顺序判断：`Forward`：`-FHA - Thr <= Angle <= FHA + Thr`；`Right`：`FHA - Thr <= Angle <= 180 - FHA + Thr`；`Left`：`-(FHA - Thr) >= Angle >= -(180 - FHA + Thr)`；否则 `Backward`（第三个参数是 `AngleThreshold`，不是 “backward half angle”；ALS 实际用 `FHA = 70, Thr = 5`）。所有边界在 float32 下 `at` / 1ulp 上 / 1ulp 下 都有用例（4 组 FHA / Thr 参数 × 10 个边界）。

### 5.10 `LerpAngle` 与 “逆时针重映射”

`LerpAngle`、`InterpolateAngleConstant`、`DamperExactAngle`、`LerpRotation`、`DamperExactRotation` 都在 `Delta > 175` 时用 `Delta - 360`（`RemapAngleForCounterClockwiseRotation`，严格大于）。结果：`LerpAngle(0, 180, 0.5) = LerpAngle(0, -180, 0.5) = -90`（180° 转向两个方向都往负方向转）；`LerpAngle(170, -170, 0.5) = 180`（返回 +180 而不是 -180）；`DamperExactAngle` 在 `Delta = 175` 与 `175.00002` 两侧方向相反。
- `InterpolateAngleConstant`：`Speed <= 0` 或 `|Delta| <= Speed·DeltaTime` 时**直接返回 Target（没有 unwind）**；`Delta = ±180` 都往负方向走；`Target = NaN` 时返回 `Current`（`FMath::Sign(NaN) = 0`）。
- `DamperExactAngle`：`|Delta| <= 1e-4`（float 的 `UE_KINDA_SMALL_NUMBER`）时返回 Target；负半衰期 / 负 dt 会往远离目标的方向走。
- `LerpRotation` / `DamperExactRotation`：所有分量 `|Delta| <= 1e-4` 时返回 To / Target 本身。

### 5.11 其它

- `ClampMagnitude01(FVector)`（double）：`SizeSquared <= 1` 原样返回；`1e155` 级别的向量平方溢出成 inf，`InvSqrt(inf) = 0`，结果是**零向量**；float32 的版本在长度约 1.8e19 时就会溢出（平方超过 float 最大值 3.4e38）。
- `SlerpSkipNormalization`：`Dot > 0.9995` 走 `GetSafeNormal(Lerp)`；`Dot` 接近 -1 时垂直分量退化成 0，`Ratio = 0.5` 的反向向量得 `(-1.2e-7, 0, 0)`（不是单位向量）；零向量输入不报错。
- `InterpolateQuaternionFast`：`Current.Equals(Target)`（每分量 1e-4）返回 `Target` 本身；`FastLerp` 在 `dot < 0` 时翻转 `Current` 的权重（走短路径）。
- `GetTwist`：不归一化 `TwistAxis`（非单位轴会得到错误的投影），零轴得单位四元数。
- 弹簧：`DeltaTime <= 1e-8`（`UE_SMALL_NUMBER`）返回 `Current`、状态不变；无效状态返回 `Target`、`Velocity` 清零；`Frequency·2π < 1e-8` 只按速度推进；`DampingRatio < 1e-8`（包含负数）走无阻尼分支；`TargetVelocityAmount` 先 `Clamp01`（NaN 得 0）；`DeltaTime` 略大于 1e-8 时速度被放大成 `ΔTarget/dt`（`dt = 1.0000001e-8` 得速度 125.66）。
- 引擎函数（`ue_math.json`）：`Kismet Sqrt(负数)` 返回 0 并打印警告（`FMath::Sqrt` 才是 NaN）；`FInterpConstantTo` 的负速度仍然按 `|速度|·dt` 前进；`FMath::Clamp(NaN, 0, 1) = 1`（`Max`）；`FMath::Acos(NaN) = 0`（先夹到 [-1, 1]）；`FMath::Sign(NaN) = 0`；`GetRangePct` / `GetMappedRangeValueClamped` 在退化范围（A == B）时得 1.0；`FRotator::NormalizeAxis(-180) = +180`。

## 6. 无法从 Python 调用的 ALS 函数

| 函数 | 原因 |
|---|---|
| `UAlsMath::DamperExact<T>` | C++ 模板，没有 UFUNCTION；就是 `FMath::Lerp(Current, Target, DamperExactAlpha(DeltaTime, HalfLife))`，用 `DamperExactAlpha` 的黄金值 |
| `UAlsMath::SpringDamper<T, S>` | C++ 模板；通过 `SpringDamperFloat` / `SpringDamperVector` 覆盖 |
| `UAlsRotation::RemapAngleForCounterClockwiseRotation<T>` | 模板（float 重载 `RemapAngleForCounterClockwiseRotation(float)` 已覆盖） |
| `UAlsRotation::RemapRotationForCounterClockwiseRotation(VectorRegister4Double)` | SIMD 寄存器接口，没有反射；效果通过 `LerpRotation` / `DamperExactRotation` 覆盖 |
| `UAlsVector::ClampMagnitude01(FVector3f)` | 普通 C++ 重载，没有 UFUNCTION（FVector 版已覆盖；float32 版同公式，但在 `float` 里算，`1e19` 就会平方溢出） |
| `UAlsUtility::BoolToString` | constexpr 辅助函数，没有 UFUNCTION |
| `UAlsUtility::TryGetMovementBaseAngularVelocity` | 需要 `FBasedMovementInfo`（活的物理体），没有 UFUNCTION |
| `UAlsUtility::GetAnimationCurveValueFromCharacter` / `GetFirstPlayerPingSeconds` | 需要活的角色 / 世界，按要求跳过 |
| `UAlsMontageUtility::StopMontagesWithSlot` / `StopMontagesWithAnySharedSlots` | 需要播放中的 `UAnimInstance`，有副作用、无返回值 |
| `UAlsConstants::*`、`UAlsDebugUtility::*` | 骨骼 / 曲线 / 插槽名常量与调试绘制，不是数学，不属于 T1 |

## 7. 引擎函数：能 / 不能从 Python 访问

`ue_math.json` 里的 58 个函数都经 `unreal.MathLibrary`（`index.json` 的 `engine_functions_reachability` 有逐项记录和 `hasattr` 的结果）。

**没有暴露（Python 访问不到）：**
- `FMath::QInterpTo` / `QInterpConstantTo`（DAL 的 `interp_to(quat, …)` 镜像它）
- `FQuat::FastLerp`（只在 `UAlsRotation::InterpolateQuaternionFast` 里，那个函数有黄金值）
- `FMath::InvExpApprox`、`FMath::InvSqrt`、`FMath::SinCos(float)`（只能通过 `DamperExactAlpha`、弹簧、`ClampMagnitude01`、`RadianToDirection*` 间接覆盖）
- `FMath::FindDeltaAngleDegrees`（DAL 的 `delta_angle`）、`FMath::Fmod`、`FRotator::GetNormalized / Normalize`、`FQuat::GetTwistAngle / ToSwingTwist`
- `SpringMath::CriticalSpringDamper` / `HalfLifeToSmoothingTime`、`FMath::ExponentialSmoothingApprox`（DAL 的 `critical_spring_damper` / `exponential_smoothing`）
- `FAlphaBlend::AlphaToBlendOption`（DAL 的 `blend_alpha`）
- `FMath::SpringDamper` 本身（只能经 ALS 的 `SpringDamperFloat` / `SpringDamperVector`；`MathLibrary.float_spring_interp` 是另一套 API，没有收录）
- `FTransform` 的运算（`transform_position` 等）有 `MathLibrary.transform_location` 等近似物，这次没有收录。

**间接可达：** `FMath::UnwindDegrees` 只能经 `FVector::UnwindEuler`（`MathLibrary.vector_unwind_euler`，对 float32 输入与 float 版本结果相同，已收录并与 float32 循环逐位核对）。

## 8. 自检：`sanity_check.py`

```
python sanity_check.py [黄金值目录]      # 默认是脚本所在目录
```

纯标准库。内容：所有 float32 输入确实是 float32 值；没有调用错误；对 Clamp01、LerpClamped、DamperExactAlpha、CalculateMovementDirection、RemapAngle、LerpAngle、InterpolateAngleConstant、DamperExactAngle、`FMath::SinCos(float)`、AngleBetweenSignedXY 等用“每步舍入 float32”的重新实现做逐位比对（全部一致）；对 LerpRotation、DamperExactRotation、InterpolateQuaternionFast、GetTwist、ClampMagnitude01 等用 double 重新实现做容差比对；TryCalculatePoleVector 的几何检查；弹簧序列的结构检查；montage 的两个恒等式。`info` 行是“UE 与教科书公式不同”的量化（DamperExactAlpha 的近似误差、SinCos 对 libm 的偏差、atan2 的符号零）。硬检查全部通过时退出码为 0。

## 9. 对 DAL 端的提示（只读了代码，**没有**拿它跑这些黄金值）

对照的是 2026-10-22 工作区里的 `als/als_lib.dal`（提交 `7a65e13` 之后逐行移植 ALS 的数学函数，`calculate_movement_direction`、`lerp_angle` 的逆时针重映射、`interpolate_angle_constant`、`damper_exact_angle`、`lerp_rotation` / `damper_exact_rotation` 的向量路径 `>=` 比较等都和 C++ 对得上）和 `dal/runtime.py`。仍然预计会在 T1 里暴露的是内建函数层面的差异：
- `radian_to_direction*` / `angle_to_direction*` 调 `cos` / `sin` 内建（libm，double）；UE 用的是 `FMath::SinCos(float)` 的多项式（§5.5）：`|x| <= 7` 差 2e-7 量级，`|x| >= 1e3` 差更大；`sin(float(π/2))` UE 得 0.9999998807907104。要对上就照抄 `sanity_check.py` 的 `sincos_f32()`。
- `direction_to_angle*` 调 `atan2` 内建（`math.atan2`）：除符号零（§5.6）外与 UE 位相同。
- `angle_between_signed_xy` 用 `normalize(a.xy)` 和 `acos`：UE 是 `FVector2f::Normalize`（`|v|² <= 1e-8` 置零）加 float32 的 `acosf`，小于约 0.02° 的角度会被量化成 0（§5.7）；double 实现会得到真实的小角度，差最大约 0.03°。
- `inv_exp_approx`、`damper_exact_alpha` 等：系数与 UE 5.8 一致，但 double 计算；C++ 是 float32 每步舍入（`sanity_check.py` 的 `damper_alpha()` 是逐位一致的 float32 版本）。
- `try_calculate_pole_vector` 和弹簧状态（`SpringDamperFloat` / `SpringDamperVector`）目前不在 `als_lib.dal` 里（注释说只被 Control Rig 单元用）；对应黄金值留给将来的移植或内建函数用。`critical_spring_damper` 内建不是 ALS 的 `FMath::SpringDamper`，不能用这些黄金值测。

## 10. 已知局限

- `GetChildTags` 的顺序是 UE 的标签树顺序（本次是每层按字母逆序），重复运行稳定，但请按集合比。
- 蒙太奇只有 12 个（9 个 ALS + 3 个 MoverExamples），都是单段单插槽；没有测多段蒙太奇、循环段、播放速率不为 1 的段。
- 随机用例的覆盖是“实际 ALS 范围 + 边界”，没有做符号 / 溢出的穷尽扫描；`ue_math.json` 是尽力而为，每个函数的随机用例比 ALS 函数少（约 12 到 60 个）。
- NaN / inf 输入只出现在标量函数上。
- 非 ALS 引擎函数用的是 Kismet 的 double 版本，不是 ALS C++ 用的 float32 重载。

## 11. 复现

```powershell
foreach ($n in 'UE-ZenDataPath','UE-LocalDataCachePath') { [Environment]::SetEnvironmentVariable($n, [Environment]::GetEnvironmentVariable($n,'User'), 'Process') }
& 'I:\UnrealEngine\UE_5.8\Engine\Binaries\Win64\UnrealEditor-Cmd.exe' 'I:\UnrealProject_Moon\DEV_58\DreamVEdAnimHost\DreamAnimTestHost.uproject' `
  -run=pythonscript -script="I:\DCC\DreamAnimLogic\als\goldens\gen_goldens.py" `
  -unattended -nopause -nosplash -NoSound -NoLiveCoding -notelemetry -abslog=<日志文件>
```

输出目录默认是脚本所在目录，可用环境变量 `ALS_GOLDENS_OUT` 覆盖。运行约一分钟。脚本在写完全部文件后自己结束进程（退出码 0）：这个宿主里 UE 5.8 的 pythonscript commandlet 在 Python 收尾时会崩溃，`os._exit()` 会在 DLL 卸载里自旋，所以用 `TerminateProcess`。运行完用 `python sanity_check.py` 自检。
