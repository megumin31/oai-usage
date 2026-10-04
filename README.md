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
