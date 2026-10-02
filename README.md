# oai-usage

简体中文 | [English](./README.en.md)

汇总本机 Codex 日志中的 token 用量，估算 API 等价成本，并显示账户额度与当前周期预测。单文件运行，仅使用 Python 标准库。

## 快速开始

需要最新版稳定 Python 3。示例统一使用 `python3`；若本机命令为 `python`，替换即可。Windows 用户请先阅读下方的 [Windows 安装与注意事项](#windows-安装与注意事项)。

直接拉取执行：

```sh
curl -fsSL https://raw.githubusercontent.com/megumin31/oai-usage/main/oai-usage | python3 -
```

默认统计最近 30×24 小时，按模型汇总。价格表需联网获取；实时额度需本机已安装原生 Codex，并登录 ChatGPT 账户。

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

Windows 示例显式使用 `curl.exe`，避免旧版 PowerShell 的 `curl` 别名差异。如果 `python` 不存在或打开 Microsoft Store，但 `py --version` 正常，可将示例中的 `python` 替换为 `py`。无需额外安装 Python 依赖。

下载脚本到当前目录后，也可以运行：

```powershell
python .\oai-usage
python .\oai-usage watch
python .\oai-usage --days all
```

使用 Codex 桌面版默认 Windows 原生环境时，在 PowerShell 中用 Windows Python 运行本脚本即可。未设置 `CODEX_HOME` 时，脚本读取 `%USERPROFILE%\.codex` 下的 `sessions` 和 `archived_sessions`。仅切换桌面版的“集成终端 Shell”不会改变这一路径；“Agent 运行环境”是另一项设置，参见 [Codex Windows 说明](https://learn.chatgpt.com/docs/windows/windows-app#windows-subsystem-for-linux-wsl)。

如果更改过 Agent 运行环境或 `CODEX_HOME`，请确认实际日志目录与脚本读取位置一致；脚本不会自动搜索 WSL 中的其他目录。可用多个 `--root` 显式指定日志目录（替换默认目录），同时包含 `sessions` 和 `archived_sessions`。`--root` 只影响日志扫描，不切换实时额度查询所用的 Codex 程序或登录环境。默认报告仅含最近 30 天，更早记录需使用 `--days all`。

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

Windows 控制台会自动启用终端控制，并在退出时恢复原模式；启用失败时关闭颜色，`watch` 改为逐帧普通输出，不清屏或隐藏光标。

价格来自第三方 models.dev 的 OpenAI 数据，经仓库整理为 [`prices.json`](./prices.json)。客户端仅从 GitHub 下载成品表，不保存价格缓存。启动取价失败会退出；watch 每小时后台刷新价格，失败时继续使用本轮有效价格。

成本按准确模型 ID、单次请求的输入量和价格档位估算，**不是历史账单或订阅账单**。未知模型、请求信息不足或缓存单价缺失时，结果会标为不完整。

当前周期使用额度接口的实际窗口，与报告的日期筛选独立。预计总量和剩余基于本机成本与账户已用比例推算，**不是官方额度或订阅美元余额**；条件不足时会说明无法预测。`--no-project` 可隐藏周期卡片。

日志默认读取 `$CODEX_HOME`（未设置时为 `~/.codex`）下的 `sessions` 和 `archived_sessions`，可用 `--root` 替换。原始日志只读，不上传日志或读取凭据内容。`--json` 输出完整数据；watch 每帧输出一行 JSON。`--output` 不会覆盖程序或 JSONL 日志。

日期默认使用本地时区；UTC 无需额外数据，其他命名时区依赖系统时区数据库。终端日期的时区标签按该时间点的实际偏移显示为 `UTC±HH:MM`，零偏移显示为 `UTC`。

## 开发与测试

直接维护 `oai-usage`，无需构建。Actions 每 4 小时运行 [`scripts/update_prices.py`](./scripts/update_prices.py)，从 models.dev 的 `openai.models` 维护价格表，再经 [`scripts/publish_prices.py`](./scripts/publish_prices.py) 校验发布。只维护当前数据格式，格式变更时同步发布程序和价格表。

运行测试：

```sh
python3 -W error::ResourceWarning -m unittest discover -s tests -q
```

CI 覆盖 Ubuntu 和 Windows，测试使用模拟 app-server；真实 Windows 账户、周期预测及 Ctrl+C 清理仍需实机验证。

## 许可证

[GNU Affero General Public License v3.0](./LICENSE)，SPDX：`AGPL-3.0-only`。
