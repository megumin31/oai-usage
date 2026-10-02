# oai-usage

简体中文 | [English](./README.en.md)

`oai-usage` 是一个本地运行的命令行工具，用于汇总 Codex session 日志中的 token 用量、按当前公开 Standard API 单价计算等价成本，并显示账户额度信息。

- 只维护最新版稳定 Python 3；只使用标准库，无需安装第三方依赖。运行脚本时，所用的 `python3` 或 `python` 应指向当前最新版稳定 Python 3。
- 原始 JSONL 日志只读；工具不保留会话正文，不上传日志，也不会读取凭据内容或消耗额度重置次数。
- 安装时只需复制 [`oai-usage`](./oai-usage) 一个文件；它已内置价格处理代码，运行时从 GitHub 获取价格表。当前版本为 0.0.1；程序提供的 CLI、报告、诊断和 JSON 说明文字使用英文，日志中的模型名等用户数据保持原样。

## 快速开始

可以直接拉取执行：通过 `curl` 下载单文件 CLI，再由最新版稳定 Python 3 从标准输入执行，无需持久安装或临时脚本。macOS、Linux 或 WSL 使用：

```sh
curl -fsSL https://raw.githubusercontent.com/megumin31/oai-usage/main/oai-usage | python3 -

# 带参数运行：参数放在 Python 的 - 后
curl -fsSL https://raw.githubusercontent.com/megumin31/oai-usage/main/oai-usage | python3 - --today
curl -fsSL https://raw.githubusercontent.com/megumin31/oai-usage/main/oai-usage | python3 - watch --quota logs --count 3

# 查看版本或帮助
curl -fsSL https://raw.githubusercontent.com/megumin31/oai-usage/main/oai-usage | python3 - --version
curl -fsSL https://raw.githubusercontent.com/megumin31/oai-usage/main/oai-usage | python3 - --help
```

Windows 使用 PowerShell 7.4+，并安装最新版稳定 Python 3：

```powershell
curl -fsSL https://raw.githubusercontent.com/megumin31/oai-usage/main/oai-usage | python -
curl -fsSL https://raw.githubusercontent.com/megumin31/oai-usage/main/oai-usage | python - --today
curl -fsSL https://raw.githubusercontent.com/megumin31/oai-usage/main/oai-usage | python - watch --count 3
```

`python3` 和 `python` 是不同平台常见的解释器名称；如果 Linux 上的 `python` 也指向所需的 Python 3，同一条 `curl ... | python -` 命令也可使用。标准输入执行保留本地执行的 CLI 参数、JSON 和 `--output` 规则，生成报告仍须联网获取价格表。

实时额度查询已改用跨平台异步子进程管道；Windows 使用 Python 默认的 Proactor 事件循环。须在本机安装可直接运行的原生 Codex，并登录 ChatGPT 账户。程序支持从 PATH、官方 npm 包装入口对应的原生程序、官方 standalone 目录及桌面已迁移的 runtime 中发现 `codex.exe`，也可用 `--codex-binary` 或 `CODEX_CLI_PATH` 指定；不会通过 shell 执行 `.cmd`。若首次安装仅有 Store 包内文件且权限阻止直接启动，可先打开 Codex 桌面应用让它迁移 runtime，或显式指定原生 CLI。默认本地时区及显式 `--timezone UTC` 无需额外时区数据；其他 IANA 命名时区仍依赖系统时区数据库，本轮未补齐该数据库。Windows 的实际验证范围见下方“安装与测试”。

直接运行脚本：

```sh
./oai-usage
```

默认生成最近 30×24 小时的报告，按模型汇总，并在输出顶部显示 **Current cycle** 的 API-equivalent usage。周期使用实际起止时间，并非固定时长或日历周。常用命令如下：

```sh
# 当前统计时区内的今天
./oai-usage --today

# 使用指定时区；日期参数均按该时区的日历日期解释
./oai-usage --since 2026-09-01 --until 2026-09-22 --timezone Asia/Shanghai --by day

# 全部历史、按独立 session 显示（包括独立子任务）
./oai-usage --days all --by session --top 0

# 同时显示模型、日期和 session 维度
./oai-usage --all

# 只扫描指定目录；--root 可以重复
./oai-usage --root /path/to/sessions --root /path/to/archived_sessions --quota off

# 输出完整 JSON 到标准输出或文件
./oai-usage --quota off --json
./oai-usage --by day --output report.json

# 持续刷新；Ctrl+C 退出
./oai-usage watch
./oai-usage watch --quota off --refresh 5 --count 3

# 查看当前价格表和来源
./oai-usage prices
```

`report` 是默认子命令，因此 `./oai-usage --today` 等同于 `./oai-usage report --today`。使用 `./oai-usage report --help`、`./oai-usage watch --help` 或 `./oai-usage prices --help` 查看完整参数。

默认显示终端中的当前周期部分；使用 `--no-project` 可隐藏它。`--quota off` 不读取账户额度，也不显示当前周期，但生成报告仍须联网获取价格表。

## 安装与测试

脚本可直接执行。如需将它作为本机命令安装到目标路径，若该路径已有命令，请先保留原文件的备份；以下命令会写入目标路径：

```sh
install -m 755 ./oai-usage ~/.local/bin/oai-usage
oai-usage --version
```

运行要求最新版稳定 Python 3；旧版 Python 不在维护范围内。CI 使用 `3.x` 和 `check-latest: true` 选择最新稳定 Python 3，不启用预发布版本。新增的 Ubuntu/Windows 测试矩阵运行全量 `unittest`（将 `ResourceWarning` 作为错误）；原 Ubuntu 价格同步工作流保留。该矩阵尚未在 Windows runner 上运行，不能作为 Windows 实测通过的证据。

更新安装时只需替换 `oai-usage`。价格表和 JSON 输出只维护当前格式，不提供旧格式兼容。价格表格式变更时，程序与云端 `prices.json` 须在同一次仓库发布中更新。程序不内置价格值，也不读取同目录的 `prices.json` 或本机价格缓存。每次运行报告、监看或价格命令都需要能访问 GitHub 上的价格表；若启动时无法取得有效价格表，程序会提示检查网络并以状态码 1 退出。

测试使用标准库的 `unittest`：

```sh
python3 -W error::ResourceWarning -m unittest discover -s tests -q
```

CLI 直接维护在 [`oai-usage`](./oai-usage)，无需构建或重建生成区；用户安装仍只需这一个文件。它只消费 GitHub 固定地址的成品 [`prices.json`](./prices.json)，不下载上游模型目录或价格数据。Actions 生产端由 [`scripts/update_prices.py`](./scripts/update_prices.py) 与独立的同步支持模块 [`scripts/price_support.py`](./scripts/price_support.py) 维护，发布脚本通过更新脚本引用该模块；生产端不依赖或从 CLI 加载代码，`price_support.py` 也不是用户安装依赖。原 `oai_price_catalog.py` 和 `scripts/bundle_prices.py` 已删除。

本机 macOS 的 Python 3.14.7 已通过 136 项测试和 diff 检查，覆盖单文件复制、标准输入执行、异步管道及清理、客户端唯一下载地址、生产端独立运行、生产输出与消费结果费率一致，以及旧字段拒绝。本轮真实原生 app-server watch 使用当前格式的本地价格测试样本，连续三帧取得 `app_server` 额度和可用周期预测，退出后两个额度子进程全部回收；这不是新版真实 GitHub 价格下载通过的证据。此前真实 GitHub 下载验证发生在格式调整之前，当前公网价格表仍含旧字段，新版客户端会拒绝；本轮尚未发布，须将程序与价格表一起发布后再验证公网链路。尚无 Windows 实机账户或原生控制台 Ctrl+C 验证，安装类型也未全部实测。在已安装并登录原生 Codex、且云端价格表已同步发布的 Windows 上，可用 PowerShell 验证：

```powershell
python ./oai-usage --quota live --json
python ./oai-usage watch --quota live --count 3 --json
```

检查 `summary.current_rate_limits.source` 为 `app_server`；在有效额度窗口、可归属且足够的本机日志用量和非零已用比例等投影条件满足时，`summary.cycle_estimates` 中对应窗口的 `projection_available` 应为 `true`，并提供预计周期总量和剩余。另行检查 Ctrl+C 或查询超时后没有遗留 app-server/价格下载子进程。`auto` 回退到日志不代表实时查询验收通过。

## 日志范围与日期

未指定 `--root` 时，工具从 `$CODEX_HOME/sessions` 和 `$CODEX_HOME/archived_sessions` 扫描日志；若没有设置 `CODEX_HOME`，则使用 `~/.codex`。指定一个或多个 `--root` 后，默认目录会被替代。

时间范围规则：

- 默认 `--days 30` 是从当前时间回看 30×24 小时；`--days all` 或 `--days 0` 不限制起点。
- `--today`、`--since` 与 `--until` 使用 `--timezone` 指定的统计时区；默认是系统本地时区。`--until` 包含该日。
- 有时间范围时，缺少时间戳的用量不会被纳入统计；数量记录在 JSON 的诊断和汇总字段中。

同一 session 即使跨多个日志文件也会按时间戳和累计用量合并去重。session 的首条 `session_meta.id` 是该 session 的固定身份；父任务 ID 仅作为关联信息保存，因此子任务不会并入父任务。继承的 fork 历史快照不会重复计入新用量。

## 成本、价格与额度

报告中的“API 等价成本”是按当前公开的 Standard API 单价换算的估计，**不是历史账单，也不是订阅账单**。价格表来自公开仓库 `main/prices.json`；运行 `prices` 可查看模型价格、长上下文规则、最近核对日期和当前目录来源。

每次启动 `report`、`watch` 或 `prices` 时，工具会从固定 HTTPS 地址的公开仓库 `main/prices.json` 下载并严格校验价格表，仅在进程内存中使用，不在本地保存。`verified_at` 必须是 UTC 当天或更早日期。客户端下载上限为 1MB、socket 3 秒、总计 6 秒，并限制大小、格式、字段，拒绝重定向或不完整数据。客户端只允许成品价格表的固定下载地址；文件执行和标准输入执行共用同一份固定、最小的下载 worker 源码，由异步调度层管理独立子进程。`watch` 每小时在后台重新下载，不阻塞正常界面刷新；整表下载并校验成功后才切换，每帧使用一份完整的价格表和额度快照。刷新失败时会给出提示，并继续使用本轮最后一份有效价格表；成功恢复后清除刷新失败提示，退出后即丢弃价格表。

`--price` 指定的手动单价优先于价格表，但启动时仍须成功下载有效价格表。

该估算不包括工具费用、地区加价、Fast/优先服务或订阅费用。每条用量事件独立计价，并以 `Decimal` 计算；长上下文按单次请求的输入量选档，session 只汇总各事件结果。这是本工具的 Standard API 等价估算约定，不代表旧 API 的官方长上下文计价范围已经明确。可以覆盖单价：

```sh
./oai-usage --price 'my-model=2.5,0.25,15,3.125'
```

参数格式为 `MODEL=IN,CACHED,OUT[,WRITE]`；四项都是每百万 tokens 的美元单价，`WRITE` 可选，所有值必须是有限的非负数。模型按日志中的准确 ID 计价；`--price` 也必须使用准确 ID。名称如 `astra`、日期后缀、`preview` 或 `latest` 不会自动映射到其他模型，显式定义的自定义模型价格仍可使用。

仓库的 GitHub Actions 配置为每 4 小时运行一次（每日 6 次，UTC 00:17、04:17、08:17、12:17、16:17、20:17；北京时间同为 00:17、04:17、08:17、12:17、16:17、20:17），也支持手动触发；GitHub 的定时调度可能延迟。任务从 OpenAI 官方模型目录提取模型页面 ID，再与 `models.dev/api.json` 中的 `openai.models` 精确匹配。官方目录用于模型身份，`models.dev` 用于价格；两者匹配不需要 API Key。Actions 对官方目录下载上限为 2MB、对 `models.dev` 为 8MB，二者均为 socket 5 秒、每源总计 20 秒，并拒绝重定向。同步范围为 GPT 5.4+、具备文本输出及文本/图像/PDF 输入能力、且同时给出输入和输出单价的模型；缓存读取和写入价格可为空。日期、`preview`、`latest` 等名称后缀不能用于猜测导入，新命名需要检查。

长上下文只接受 `models.dev` 中唯一、明确的 context tier：直接用 `tier.size` 作为阈值，按本工具的 request 范围计价，并将 `models.dev` 记为规则数据来源。request 是唯一计价规则，价格表不提供可选 scope。新模型有有效的显式 tier 即可自动导入，不需逐型号本地规则；只有旧 `context_over_200k` 而没有显式阈值、多档价格或未知定价维度时仍保持 pending，不猜测或补零。未匹配、缺少必需单价或不支持的模型会记录在 Actions 日志；已有模型保留其历史价格。

只在本仓库 `main` 上运行的只读校验任务生成仅含 JSON 的候选表；独立发布任务以受信任代码再次校验后，才通过 GitHub API 在一个 `expectedHeadOid` 下原子更新固定的 `prices.json` 路径。候选必须符合当前价格表字段，顶层 `source` 固定为 `models.dev`，不能包含代码。解析、测试或候选价格异常都会停止发布：包括任何已有模型删除、大批模型消失、新模型任一存在费率为零、已有模型零价状态变化、单价剧烈变化或长上下文阈值变化；已有费率丢失也会停止，原先缺失的可选缓存费率变为有效正费率则可自动更新。此时请人工核对上游价格，并修订基准价格后重新运行；没有自动放行开关。

解析和测试均成功时，价格变化会立即提交；但只有本轮已检查目录中的全部既有模型的上游名单和价格时，`verified_at` 才能前推。若有既有模型未在本轮观察到，工具会保留其价格并在 Actions 日志列出具体模型，且即使其余模型价格变化或已满 30 天，也不会前推整个目录的核对日期。全部既有模型均已检查且价格未变时，`verified_at` 满 30 天才会仅刷新该日期并提交，以维持公开仓库的定时任务活动；未满 30 天则不提交。`verified_at` 表示本轮上游价格和模型名单的检查日期，不能证明官方核价或本地长上下文规则已经重新审核。该机制仍信任该 GitHub 仓库及其发布流程：目录和来源字段没有签名，不能独自证明真实价格；发布写入权限也是仓库级别，不能限定到单一路径。

未知模型不会套用其他模型的价格。若单次请求输入量缺失，累计增量又超过长上下文阈值，或最后一次请求详情互相矛盾，该事件的档位未知；累计增量不超过阈值且详情无冲突时，可以确定其中的请求都属于短档。有缓存读取 token 但缺少对应单价时，已知费用仍可计算，但总成本标为不完整。因此：

- `api_cost_usd` 仅在总成本完整时给出数值；不完整时为 `null`。
- `known_api_cost_usd` 始终给出已知部分的成本；档位未知的整个事件不计入该小计，其他事件照常计算。
- `unpriced_usage` 表示未定价模型的用量；`uncertain_pricing_usage` 表示计价条件无法确定的用量。`pricing_issues` 中用 `unknown_request_size` 标记未知档位，用 `unknown_cache_read_price` 标记缺少缓存读取单价，用 `unknown_cache_write_price` 标记缺少缓存写入单价；缓存费率缺失时，其他已知费用仍可计入小计。

额度来源由 `--quota` 控制：

| 选项 | 行为 |
| --- | --- |
| `auto`（默认） | 尝试实时读取；失败时回退到日志快照。 |
| `live` | 只尝试实时读取。 |
| `logs` | 只使用日志中的额度快照。 |
| `off` | 不读取额度，也不创建实时查询任务。 |

`report` 和 `watch` 在 `--quota auto` 或 `live` 下启动时并发获取必需价格表和实时额度；额度预期失败仍按上表规则处理，启动价格下载或校验失败则以状态码 1 退出并清理额度任务。普通报告在额度观测完成后才扫描日志，周期统计截至额度观测时刻。`logs` 和 `off` 不启动 Codex 查询任务或进程，`prices` 不读取账户额度。

每次实时查询通过异步子进程管道启动一次 `codex app-server --listen stdio://`，依次完成 initialize 响应、initialized 通知和 account/rateLimits/read 请求，不保持长连接。整次查询默认总超时为 10 秒，响应缓冲上限为 8MiB；超时、取消和退出时会终止并等待子进程退出，关闭管道。实时额度只读取当前 app-server 的 `rateLimitsByLimitId` 和 `rateLimitResetCredits.availableCount`；不会解析旧的单桶 `rateLimits` 或顶层 snake_case 别名。实时接口与日志快照是独立的当前数据源，`--quota logs` 及 `auto` 的日志回退仍可用。

`watch` 默认每 2 秒刷新；在 `auto` 或 `live` 下，启动时等待首次实时额度查询完成后才输出第一帧，之后每次查询完成后默认等待 30 秒再查询。用 `--refresh` 与 `--quota-interval` 分别调整界面刷新间隔和查询完成后的等待间隔。`--count N` 在刷新 N 次后退出，`0` 表示持续运行。存在多个额度桶时默认优先选择 `codex` 桶，可用 `--limit LIMIT_ID` 切换。TTY 默认使用彩色圆角卡片和额度进度条：标题/边框为青色，金额高亮，低/中/高额度用量分别使用绿/黄/红色；`--no-color` 或 `NO_COLOR` 关闭 ANSI，`--color` 可在非 TTY 启用颜色，JSON 始终不含样式控制符。

每个可用的额度窗口以 **Primary**、**Secondary** 或 **Individual Limit** 区分，统一标为 **Current cycle**，并显示实际周期起点、重置时间和观测时间，以及账户已用/剩余比例、**LOCAL API COST**（窄卡为 **Local Cost Used**）、**EST. TOTAL**、**EST. REMAINING** 和 token 明细。完整报告还会显示缓存写入、reasoning、session/event 数、完整计价的 token 占比和周期起点；宽屏金额分栏，窄屏卡片保留本机成本。watch 使用紧凑卡片，在宽度不少于 96 列且恰有两个窗口时并排显示。

当前周期范围在每次 report/watch 时都根据接口最新的重置时间减去 `window_minutes` 重新计算，统计截至额度观测时间；它独立于 `--today`、`--days`、`--since`、`--until` 选择的 **Selected report period**。这不预设一周或其他固定长度，任意有效窗口长度均可用。手动提前 reset 更新接口边界后，即使日志缓存未变化，工具也会切换到新周期并排除旧周期用量；不会仅凭已用百分比下降推断 reset。即使额度已用比例为 0 或无法外推，工具仍显示可用的本机周期用量和成本，并说明投影不可用。过期或无效窗口仅显示不可用，不能充当当前周期；本机外推并非官方额度。若无法确定模型与额度桶的对应关系，工具不会为专属额度桶作此类外推。

## JSON

使用 `--json` 时，普通报告输出一个严格 JSON 文档；`watch --json` 每帧输出一行 NDJSON。`--output FILE` 原子写入完整 JSON；在 watch 模式中保存最后一帧。输出不会覆盖脚本本身或 session JSONL 日志。终端的日期分组按最近日期优先，模型和 session 按总 token 用量降序；`--top` 只截断终端展示，JSON 始终包含完整分组数据。

持续刷新时，`ReportCache` 会复用未变化日志的会话、计价和汇总结果；滑动时间窗口只有跨过用量事件边界时才重新汇总。

顶层报告 JSON 包含 `version`、`generated_at`、`period`、`roots`、`pricing`、`diagnostics`、`summary` 和 `sessions`。`pricing` 包含价格表的 `verified_at`、`catalog_source`（运行时仅为 `github`）、本次下载时间 `fetched_at`、陈旧标记 `stale` 与提示 `warnings`，以及顶层的价格来源 `price_source` 和模型身份来源 `model_source`；每个模型同层包含 `model_source` 和 `rule_source`，并保留普通单价和 `long_input`、`long_cached_input`、`long_cache_write`、`long_output` 长上下文单价字段。`cached_input` 和 `long_cached_input` 可为 `null`。价格表超过 45 天未检查时会标为陈旧并给出提示。`prices --json` 同样返回这些目录元数据和每模型价格字段。`prices.json` 只接受当前字段：顶层 `provider` 为 `openai`、`model_source` 为官方模型目录，`source` 严格为 `models.dev` 的价格来源；每个模型的 `model_source` 必须是对应的官方模型页，长上下文 `long_context.source` 只接受 `models.dev`，输出的 `rule_source` 同样为 `models.dev`，无长上下文时 `rule_source` 为 `null`。这些来源字段表示数据出处，不代表签名或官方核价。`summary` 保留常用的 `usage`、`by_model`、`by_day`、`by_session`、`unpriced_usage` 与 `api_cost_usd` 语义，并新增 `known_api_cost_usd`、`estimate_is_partial`、`uncertain_pricing_usage`、`pricing_issues`；每个 session 可包含 `parent_session_id`。价格表和报告不包含 `schema_version`，也不设置替代版本号；价格表不接受旧 `long_context.scope` 字段，报告与 `prices` 输出不包含 `long_context_scope`，长上下文统一按 request 计价。只维护当前格式，无旧格式回退或迁移兼容；参数不接受缩写或旧别名。

## 数据质量提示

报告和 JSON 的 `diagnostics` 会显示扫描与解析中的事实性提示，例如目录或文件不可读、损坏 JSON 行、无效用量快照、尚未写完的日志行、重复快照、继承的 fork 基线或无法归属的 fork 基线。计数器重置会作为新段处理；分项计数下降会给出诊断。终端中来自日志的文本会过滤控制字符。

## 许可证

本项目采用 [GNU Affero General Public License v3.0](./LICENSE)，SPDX 标识为 `AGPL-3.0-only`。
