# oai-usage

简体中文 | [English](./README.en.md)

汇总本机 Codex 日志中的 token 用量，估算 API 等价成本，并显示账户额度与当前周期预测。单文件运行，仅使用 Python 标准库。

## 快速开始

需要最新版稳定 Python 3。示例统一使用 `python3`；若本机命令为 `python`，替换即可。Windows 用户请先阅读下方的 [Windows 安装与注意事项](#windows-安装与注意事项)。

直接拉取执行：

```sh
curl -fsSL https://raw.githubusercontent.com/megumin31/oai-usage/main/oai-usage | python3 -
```

默认按模型汇总当前账户额度周期内的本机日志；多个有效窗口并存时选择最长窗口（例如 Secondary），并标明实际起点与窗口名称。关闭额度查询或无法验证当前周期时，明确回退到最近 30×24 小时。价格表需联网获取；实时额度需本机已安装原生 Codex，并登录 ChatGPT 账户。

Tokens 和 API 等价费用仅覆盖本机 Codex 会话日志；额度百分比是账户级数据，覆盖范围不同。本机时段内无记录不代表账户未使用：云端协调的 Work 或 dot 任务可以在这台电脑执行工具，而模型用量不进入这些日志；额度百分比本身无法判断用量来自哪些任务或设备。

<details>
<summary>可选：带参数执行</summary>

参数放在 Python 的 `-` 后，每条命令可单独复制。

统计今天：

```sh
curl -fsSL https://raw.githubusercontent.com/megumin31/oai-usage/main/oai-usage | python3 - --today
```

持续监看，Ctrl+C 退出：

```sh
curl -fsSL https://raw.githubusercontent.com/megumin31/oai-usage/main/oai-usage | python3 - watch
```

查看完整参数：

```sh
curl -fsSL https://raw.githubusercontent.com/megumin31/oai-usage/main/oai-usage | python3 - --help
```

</details>

## Windows 安装与注意事项

请安装新版稳定 PowerShell（7.4+）和最新版稳定 Python 3。先在现有 PowerShell 中通过 WinGet 安装 PowerShell 和官方 Python 安装管理器：

```powershell
winget install --id Microsoft.PowerShell --exact --source winget
winget install --id Python.PythonInstallManager --exact --source winget
```

安装后关闭并重新打开终端，从开始菜单打开 **PowerShell 7**，或在新终端输入 `pwsh`。新版 PowerShell 与系统自带的 Windows PowerShell 5.1 并存，安装后原窗口不会自动切换。然后安装 Python；`default` 在管理器默认配置下选择最新稳定版，不固定小版本号：

```powershell
pymanager install default
```

检查当前版本：

```powershell
$PSVersionTable.PSVersion
python --version
```

如果没有 `winget`，先从 Microsoft Store 安装或更新“应用安装程序（App Installer）”。也可参考官方的 [PowerShell 安装说明](https://learn.microsoft.com/powershell/scripting/install/install-powershell-on-windows)和 [Python 安装说明](https://docs.python.org/3/using/windows.html)。

在 PowerShell 7 中直接运行：

```powershell
curl.exe -fsSL https://raw.githubusercontent.com/megumin31/oai-usage/main/oai-usage | python -
```

Windows 示例显式使用 `curl.exe`，避免旧版 PowerShell 的 `curl` 别名差异。如果 `python` 不存在或打开 Microsoft Store，但 `py --version` 正常，可将示例中的 `python` 替换为 `py`。默认本地时区和 `--timezone UTC` 无需额外依赖；使用 `--timezone America/New_York` 等 IANA 命名时区时，若系统没有时区数据库（通常包括 Windows），先运行 `python -m pip install tzdata`。

下载脚本到当前目录后，也可以运行：

```powershell
python .\oai-usage
python .\oai-usage watch
python .\oai-usage --days all
```

使用 Codex 桌面版默认 Windows 原生环境时，在 PowerShell 中用 Windows Python 运行本脚本即可。未设置 `CODEX_HOME` 时，脚本读取 `%USERPROFILE%\.codex` 下的 `sessions` 和 `archived_sessions`。仅切换桌面版的“集成终端 Shell”不会改变这一路径；“Agent 运行环境”是另一项设置，参见 [Codex Windows 说明](https://learn.chatgpt.com/docs/windows/windows-app#windows-subsystem-for-linux-wsl)。

如果更改过 Agent 运行环境或 `CODEX_HOME`，请确认实际日志目录与脚本读取位置一致；脚本不会自动搜索 WSL 中的其他目录。可用多个 `--root` 显式指定日志目录（替换默认目录），同时包含 `sessions` 和 `archived_sessions`。`--root` 只影响日志扫描，不切换实时额度查询所用的 Codex 程序或登录环境。默认报告采用有效的当前额度周期；无法确定周期时回退最近 30 天，更早记录可使用 `--days all`。

## 常用命令

下载 [`oai-usage`](./oai-usage) 后，也可以直接运行本地文件：

```sh
python3 ./oai-usage
```

持续监看：

```sh
python3 ./oai-usage watch
```

按日期汇总并保存 JSON：

```sh
python3 ./oai-usage --by day --output report.json
```

查看价格和来源：

```sh
python3 ./oai-usage prices
```

默认终端显示两个独立期间区块：This cycle（Selected）和 Last 30 days（参考），每块保留名称、日期和来源，直接显示该期间自己的 By model 逐模型 token 和成本明细，表格底部的 Total 行汇总该期间全部模型，重叠期间不会相加。`--today`、`--days`、`--since` 或 `--until` 优先决定 Selected 及其模型明细，近 30 天保留为参考。无法确定周期时 Selected 回退最近 30 天；Selected 与参考范围完全相同时只显示一次。`--top` 分别限制每个期间显示的模型数，Total 行始终包含该期间全部模型；`--top 0` 显示全部。token 列依次为 Input、Cached、Output、Reasoning、Total，Total 仍为 Input + Output，Reasoning 已包含在 Output 中。比例按合计分子、分母重算，sessions 按去重会话计数；JSON 始终保留完整模型明细，不新增名为 Total 的模型。

其他选项用 `report --help`、`watch --help` 或 `prices --help` 查看。常用筛选有 `--today`、`--days all`、`--since`、`--until`、`--root` 和 `--by`；`--price` 可手动覆盖单价。

## 实验性分段估算（主动启用）

`segments` 是独立实验命令，不改变默认报告、额度面板或 `watch`。它将显式配对的额度观测与有完整证据的本机请求收据对应，给出**仅针对声明工作负载、附带前提的 API 等价金额**，不是官方额度、订阅美元余额或全账户费用；账户百分比仍是主要额度指标。

### 版本 1：单一声明工作负载

可先只读诊断已有本机日志（若 `CODEX_HOME` 不同，请替换为实际目录）：

```sh
python3 ./oai-usage segments diagnose --root "$HOME/.codex/sessions" --root "$HOME/.codex/archived_sessions" --price-catalog ./prices.json
```

此离线路径展示数据是否充分、历史候选配对和日志时间之间可能对应的本机 API 费用。历史候选始终不具备估算资格，不拟合 `K`：落盘时间不能证明请求边界或额度观测的新鲜度。费用覆盖所有选中本机会话；候选区间可能重叠，不能相加。加 `--json` 可查看完整诊断。

在仓库目录运行完全离线的合成分析示例：

```sh
python3 ./oai-usage segments analyze --input tests/fixtures/segments/complete.json --price-catalog tests/fixtures/prices.json
```

加 `--json` 输出结构化数据，或用 `--output trial-result.json` 将 JSON 保存到新文件。分析仅读取指定的清单和价格表，不联网、不自动发现日志、不读取凭据，也没有自动收据导入器。示例只验证计算与拒绝规则，不能验证实际准确度。

真实试用时，可由你自愿在自己已登录原生 Codex 的电脑上运行以下单次命令：开始明确划定的本机工作前采集 `before-1`，工作结束、且有依据支持结算假设后采集 `after-1`：

```sh
python3 ./oai-usage segments snapshot --id before-1 --account-key local-account-a --mode standard --window secondary --output before-1.json
# 两次命令之间运行明确划定的本机工作，并检查所需证据。
python3 ./oai-usage segments snapshot --id after-1 --account-key local-account-a --mode standard --window secondary --output after-1.json
```

每次调用只查询一次实时额度并输出 JSON，不启动后台采集、不读取凭据内容、不自动重放任务。本地账户标签和声明的模式不由 RPC 核实；收到响应的时间不是服务器测量时间，单纯等待也不能证明额度已结算。

清单需按[格式与证据说明](./docs/segmented-quota-format.md)手工编写。普通 JSONL 的落盘或完成时间**不能同时证明请求的 START 和 END**；历史额度快照、间隔后第一条 `token_count` 都不能作为新鲜基线。若缺少可靠的起止时间、完整收据、无其他设备用量、边界无在途请求、结算或额度池归属依据，应保留相应断言为 false，接受 `cannot_estimate`；不可捏造证据或仅为得到数字而改成 true。

预先声明采集期间及全部尝试片段，一份清单只对应一个账户／计划／额度池／窗口／模式和一种工作负载定义。失败片段和百分比变化为零的片段都须保留；任一片段被拒绝，会阻止其相同窗口／重置时间／来源组出估值；跨重置片段同时阻止两端涉及的组，不连带阻止其他周期。重置变化或漂移不会合并。使用 `K = 100 × Σ(本机 API 费用) / Σ(百分点变化)`，同时报告实际模型混合，不对单段比值取平均。可选范围仅覆盖声明的端点量化误差（每个端点至少 ±1 个百分点，仅同一快照 ID 可抵消；这是选定假设，不是服务器舍入保证）；累计至少 5 个百分点、相对量化误差不超过 50% 只是工程门槛，不代表统计充分性、独立样本、置信区间或已验证准确度。

### 版本 2：逐模型备选情景

`schema_version: 2` 按精确模型、速度、推理强度和缓存工作负载分别汇总受控的单配置片段。每行回答：“如果同一账户额度全部用于这一配置，整个周期相当于多少 API 金额？”当所选快照和不确定性通过校验时，还估算**截至显式指定额度快照**的剩余 API 等价金额。各行是备选情景，总额和剩余额度都不能相加，也不能把比值当作通用、固定的模型额度权重。

可运行完全虚构的双模型示例：

```sh
python3 ./oai-usage segments analyze --input tests/fixtures/model_segments/two_models.json --price-catalog tests/fixtures/model_segments/fictional_prices.json
```

合成示例中，`synthetic-astra` 的名义整周期等价金额为 $75，`synthetic-sol` 为 $100；指定快照若显示剩余 40%，则分别是 $30 或 $40。模型名、价格、观测和金额全是测试数据，不是实际模型价格、实测额度或校准精度验证。加 `--json` 可查看实际 token／缓存组合、适用范围、数值价格指纹、仅含量化误差的范围和无法估算的原因。

- 结果只适用于相同账户、计划、额度池、窗口、重置时间、观测来源、模式、模型／速度／推理强度／缓存工作负载及数值价格基准；配置改变需单独校准
- 预先声明全部尝试，保留失败和零变化片段的费用。预先声明的混合片段（`workload_id: null`）仅诊断为不支持，不按 API 价格或 token 占比分摊；单配置片段中出现意外混合，会使受影响的配置组无法估算
- `quantization.rounding` 显式选择假设：`unknown` 为正负一个声明的分辨率步长，`floor`、`nearest`、`ceil` 使用相应的闭区间外包络。官方取整规则并不已知；只有同一配置中共享的精确快照 ID 可抵消误差，独立片段以及模型切换两侧的边界误差仍保留
- 累计至少 5 个百分点、相对百分点误差不超过 50%、总金额范围宽度／名义值不超过 50%，只是工程筛选条件，不是统计置信区间或精度证明。证据不足时点估值保持未知，诊断范围可能仍保留；上界 `null` 表示无有限上界
- 当前快照缺失、不匹配或早于校准端点时，可以只让剩余金额未知，保留有效的整周期估值。接近已用 100% 时，即使整周期通过筛选，剩余金额的不确定性仍可能过大

详见[版本 2 格式、公式、取整示例和安全填写模板](./docs/model-quota-scenarios.md)。目前仍**没有自动生成可信证据的日志采样器或收据导入器**；`segments diagnose` 仍只读检查历史资料是否充分，不能产出校准估值。缺乏依据的断言应保持 false，不要为了得到数字而填写无法证实的声明。

### 历史日志研究模式：按重置批次隔离

```sh
python3 ./oai-usage segments historical --root "$HOME/.codex/sessions" --root "$HOME/.codex/archived_sessions" --price-catalog ./prices.json
```

此模式仅离线读取明确指定的日志和价格，复用响应、fork 和 compaction 去重账本；不采集实时额度。每个额度批次（epoch）内再按模型、已观察到的 service tier 和推理强度分开。提前新窗口、同截止时间的下降、无法解决的时间戳别名都会切断跨边界样本；疑似补额或缓存回退保持歧义，不等到恢复旧高点再混回旧批次。新窗口之后回返的旧缓存不能成为当前余额。

主金额只有在批次无未解决歧义、信号和量化范围通过筛选时才显示；上界无穷、范围过宽或预设敏感性不稳定时显示未知。诊断名义比值不代表可靠容量。零百分点变化的成本仍保留，独立片段的端点误差不会被忽略。历史与当前结果单独标注，不把历史系数自动套到当前周期。

加 `--json` 保留全部预设策略、对齐偏移、批次边界、散列观察来源和剔除理由。历史模式的输出版本与严格 manifest 格式独立，默认 report/watch/JSON 和 `segments analyze`/`diagnose` 不变。详见[历史模式规则、歧义和输出说明](./docs/historical-quota-heuristic.md)。这些工程筛选不能证明账户归属、无其他设备消费、同步结算或预测准确度。

### 单向污染稳健估算（离线、条件模型）

`segments robust` 在独立重置批次内，寻找重复出现的低额度消耗率群，并用后段数据检验。它保留零百分点成本、共享端点误差、所有预设块长和对齐偏移；不会取最大容量或平均片段容量。额外设备消费为非负、存在足够近乎无污染片段、工作负载稳定和测量误差有界均是显式假设，不能由历史日志自动证明。失败时仍显示诊断候选、支持情况和原因。

```sh
python3 ./oai-usage segments robust --root "$HOME/.codex/sessions" --root "$HOME/.codex/archived_sessions" --price-catalog ./prices.json
```

训练/留出按实际目标工作负载的去重观测时长切分，各时间方案共用一个切点。新增连续小信号聚合诊断，保留原始端点约束，不据此自动发布主值；单侧边界明确保留无穷上界。

R4在稳定上沿有重复片段支持时显示条件估计；严格后段检验用于可信标签，不再一律隐藏数值。量化范围、端点误差压力范围和时间偏移敏感性分别展示；保留全部原子约束，无干净片段时仍可能整体低估。详见[R4算法说明](./docs/stable-frontier-r4.md)。

主估计是同模型、同批次的条件情景，不能相加或套到其他周期。离线缓存没有独立新鲜度证明时，当前剩余金额保持未知。详见[算法、识别假设与验证说明](./docs/robust-quota-estimator.md)。

## 额度、价格与数据

`--quota` 控制额度来源：

| 模式 | 行为 |
|---|---|
| `auto`（默认） | 实时查询失败时回退到日志快照。 |
| `live` | 只用实时查询结果。 |
| `logs` | 只用日志快照。 |
| `off` | 不查询额度；价格表仍需联网获取。 |

`watch` 默认每 2 秒刷新；额度查询完成后等待 30 秒再查，可用 `--refresh` 和 `--quota-interval` 调整。`--count N` 限制刷新次数。找不到 Codex 时，可用 `--codex-binary` 指定程序路径。

完整面板超出终端高度时，`watch` 会明确提示并切换为完整滚动输出。向上滚动即可查看两个期间各自的模型行和 Total 合计；窗口变大后仍保留此模式，避免清掉此前输出。`--top 0` 显示所有模型。

Windows 控制台会自动启用终端控制，并在退出时恢复原模式；启用失败时关闭颜色，`watch` 改为逐帧普通输出，不清屏或隐藏光标。

价格来自第三方 models.dev 的 OpenAI 数据，经仓库整理为 [`prices.json`](./prices.json)。客户端仅从 GitHub 下载成品表，不保存价格缓存。启动取价失败会退出；watch 每小时后台刷新价格，失败时继续使用本轮有效价格。

成本按准确模型 ID、单次请求的输入量和价格档位估算，**不是历史账单或订阅账单**。未知模型、请求信息不足或缓存单价缺失时，结果会标为不完整。

账户额度和当前周期合并显示，额度百分比来自账户快照，本机 token 与 API 等价成本来自日志。当前周期起点由快照的重置时间减窗口长度计算，要求窗口及快照时间有效；它不是自然月。默认报告采用最长有效窗口，截止报告当前时间；周期成本估算仅截止额度快照时间，两者明确标注。显式日期筛选仍与周期估算独立。预计总量和剩余基于本机成本与账户已用比例推算，**不是官方额度或订阅美元余额**；条件不足时会说明无法预测。`--no-project` 可隐藏周期估算，保留账户额度。

查询清理在各平台均限制管道排空等待时间。POSIX 会终止本工具创建的进程组，包括继承输出管道的后代；Windows 会终止直接子进程并在清理时限内关闭管道，尚未在原生 Windows 验证整个后代进程树的终止。

统计同时支持旧版 `token_count` 累计快照和逐响应的 `token_usage_record`。通过 response ID 去除重传，并按请求向量和累计覆盖关系去重，两个账本不会直接相加。仅有旧格式的历史仍保留；新格式保留响应时间和模型归属，首次累计基线不会当作本次请求计费。分叉继承的父线程记录、压缩检查点不重复统计。无法协调累计域时，报告会指出不确定的旧账本区间，只保留已观测的响应量，不凭不明差额推算费用。

价格错误会区分下载失败和价格表数据格式错误，后台刷新也保留该分类。`--today` 在本地时间精确午夜允许空期间，watch 会继续跨日刷新。

日志默认读取 `$CODEX_HOME`（未设置时为 `~/.codex`）下的 `sessions` 和 `archived_sessions`，可用 `--root` 替换。原始日志只读，不上传日志或读取凭据内容。`--json` 输出完整数据；watch 每帧输出一行 JSON。`period.selection` 标明 `current_cycle`、`explicit` 或 `fallback`；当前周期附带 `cycle_window` 和 `cycle_resets_at`，回退附带 `fallback_reason`。原有用量、成本和各维度 JSON 字段保留；新增 `period_summaries` 分别提供各期间的本地汇总、完整 `by_model` / `by_model_detail`、日期范围和来源，参考期间不改变原 `period` 或 `summary`。`--output` 不会覆盖程序或 JSONL 日志。

日期默认使用本地时区；UTC 无需额外数据，其他命名时区使用系统 IANA 数据库或 Python 官方维护的 [`tzdata`](https://docs.python.org/3/library/zoneinfo.html#data-sources) 包。缺少指定时区时会提示检查名称或安装 `tzdata`。终端日期的时区标签按该时间点的实际偏移显示为 `UTC±HH:MM`，零偏移显示为 `UTC`。

## 开发与测试

直接维护 `oai-usage`，无需构建。Actions 每 4 小时运行 [`scripts/update_prices.py`](./scripts/update_prices.py)，从 models.dev 的 `openai.models` 维护价格表，再经 [`scripts/publish_prices.py`](./scripts/publish_prices.py) 校验发布。只维护当前数据格式，格式变更时同步发布程序和价格表。

运行测试：

```sh
python3 -m pip install tzdata
python3 -W error::ResourceWarning -m unittest discover -s tests -q
```

`tzdata` 是完整测试套件的依赖，确保命名时区、夏令时和午夜边界测试都实际运行。CI 在 Ubuntu 和 Windows 上安装它，并通过空 `PYTHONTZPATH` 强制验证包内数据来源。测试使用模拟 app-server；真实 Windows 账户、周期预测及 Ctrl+C 清理仍需实机验证。

## 许可证

[GNU Affero General Public License v3.0](./LICENSE)，SPDX：`AGPL-3.0-only`。

### R5：原始额度读数联合约束（实验）

新增离线 `segments joint`，保留每条有效相邻额度读数及内部共享端点，在同一期段、模型配置和时间偏移假设内联合求解。5/15/30 分钟仅用于发现近净候选；候选按整段 5% 污染预算约束，不能把同一观测重复计为支持。不同时间偏移报告各自区间及并集，不直接求交。

```sh
python3 ./oai-usage segments joint --root /明确授权的日志目录 --price-catalog ./prices.json --json
```

原 `segments robust` 保留 R4，便于对照。有限上界仍依赖近净片段与端点误差预算；纯量化无解、替代偏移与搜索未穷尽都会明确标注。详见 [R5 方法与限制](docs/joint-raw-estimator-r5.md)。
