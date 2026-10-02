# oai-usage

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

Windows 实时额度查询的管道兼容问题尚未解决；默认 `--quota auto` 在实时查询失败后会回退到日志快照，也可显式使用 `--quota logs` 或 `--quota off`。命名时区所需的数据也尚未补齐，因此此入口不代表所有 Windows 功能已完整支持。

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

运行要求最新版稳定 Python 3；旧版 Python 不在维护范围内。CI 使用 `3.x` 和 `check-latest: true` 选择最新稳定 Python 3，不启用预发布版本。

价格目录使用 schema 2；更新安装时只需替换 `oai-usage`。程序不内置价格值，也不读取同目录的 `prices.json` 或本机价格缓存。每次运行报告、监看或价格命令都需要能访问 GitHub 上的价格表；若启动时无法取得有效价格表，程序会提示检查网络并以状态码 1 退出。

测试使用标准库的 `unittest`：

```sh
python3 -m unittest discover -s tests -q
```

开发时，[`oai_price_catalog.py`](./oai_price_catalog.py) 是价格处理代码的维护源，[`prices.json`](./prices.json) 是云端价格目录的维护源。修改价格处理模块后，运行 `python3 scripts/bundle_prices.py` 重建 `oai-usage` 中的生成区；运行 `python3 scripts/bundle_prices.py --check` 可检查生成区是否与模块一致。更新 `prices.json` 不需要重建可执行文件。

## 日志范围与日期

未指定 `--root` 时，工具从 `$CODEX_HOME/sessions` 和 `$CODEX_HOME/archived_sessions` 扫描日志；若没有设置 `CODEX_HOME`，则使用 `~/.codex`。指定一个或多个 `--root` 后，默认目录会被替代。

时间范围规则：

- 默认 `--days 30` 是从当前时间回看 30×24 小时；`--days all` 或 `--days 0` 不限制起点。
- `--today`、`--since` 与 `--until` 使用 `--timezone` 指定的统计时区；默认是系统本地时区。`--until` 包含该日。
- 有时间范围时，缺少时间戳的用量不会被纳入统计；数量记录在 JSON 的诊断和汇总字段中。

同一 session 即使跨多个日志文件也会按时间戳和累计用量合并去重。session 的首条 `session_meta.id` 是该 session 的固定身份；父任务 ID 仅作为关联信息保存，因此子任务不会并入父任务。继承的 fork 历史快照不会重复计入新用量。

## 成本、价格与额度

报告中的“API 等价成本”是按当前公开的 Standard API 单价换算的估计，**不是历史账单，也不是订阅账单**。价格表来自公开仓库 `main/prices.json`；运行 `prices` 可查看模型价格、长上下文规则、最近核对日期和当前目录来源。

每次启动 `report`、`watch` 或 `prices` 时，工具会从固定 HTTPS 地址的公开仓库 `main/prices.json` 下载并严格校验价格表，仅在进程内存中使用，不在本地保存。`verified_at` 必须是 UTC 当天或更早日期。客户端下载上限为 1MB、socket 3 秒、总计 6 秒，并限制大小、格式、字段，拒绝重定向或不完整数据。`watch` 每小时重新下载；刷新失败时会给出提示，并继续使用本轮最后一份有效价格表，退出后即丢弃。

`--price` 指定的手动单价优先于价格表，但启动时仍须成功下载有效价格表。

该估算不包括工具费用、地区加价、Fast/优先服务或订阅费用。每条用量事件独立计价，并以 `Decimal` 计算；长上下文按单次请求的输入量选档，session 只汇总各事件结果。这是本工具的 Standard API 等价估算约定，不代表旧 API 的官方长上下文计价范围已经明确。可以覆盖单价：

```sh
./oai-usage --price 'my-model=2.5,0.25,15,3.125'
```

参数格式为 `MODEL=IN,CACHED,OUT[,WRITE]`；四项都是每百万 tokens 的美元单价，`WRITE` 可选，所有值必须是有限的非负数。模型按日志中的准确 ID 计价；`--price` 也必须使用准确 ID。名称如 `astra`、日期后缀、`preview` 或 `latest` 不会自动映射到其他模型，显式定义的自定义模型价格仍可使用。

仓库的 GitHub Actions 配置为每 4 小时运行一次（每日 6 次，UTC 00:17、04:17、08:17、12:17、16:17、20:17；北京时间同为 00:17、04:17、08:17、12:17、16:17、20:17），也支持手动触发；GitHub 的定时调度可能延迟。任务从 OpenAI 官方模型目录提取模型页面 ID，再与 `models.dev/api.json` 中的 `openai.models` 精确匹配。官方目录用于模型身份，`models.dev` 用于价格；两者匹配不需要 API Key。Actions 对官方目录下载上限为 2MB、对 `models.dev` 为 8MB，二者均为 socket 5 秒、每源总计 20 秒，并拒绝重定向。同步范围为 GPT 5.4+、具备文本输出及文本/图像/PDF 输入能力、且同时给出输入和输出单价的模型；缓存读取和写入价格可为空。日期、`preview`、`latest` 等名称后缀不能用于猜测导入，新命名需要检查。

长上下文只接受 `models.dev` 中唯一、明确的 context tier：直接用 `tier.size` 作为阈值，按本工具的 request 范围计价，并将 `models.dev` 记为规则数据来源。旧 schema 2 目录中的 `scope=session` 仍可解析，但报告按 request 范围估算。新模型有有效的显式 tier 即可自动导入，不需逐型号本地规则；只有旧 `context_over_200k` 而没有显式阈值、多档价格或未知定价维度时仍保持 pending，不猜测或补零。未匹配、缺少必需单价或不支持的模型会记录在 Actions 日志；已有模型保留其历史价格。

只在本仓库 `main` 上运行的只读校验任务生成仅含 JSON 的候选表；独立发布任务以受信任代码再次校验后，才通过 GitHub API 在一个 `expectedHeadOid` 下原子更新固定的 `prices.json` 路径。候选必须使用 schema 2，顶层 `source` 固定为 `models.dev`，不能包含代码。解析、测试或候选价格异常都会停止发布：包括任何已有模型删除、大批模型消失、新模型任一存在费率为零、已有模型零价状态变化、单价剧烈变化或长上下文阈值变化；已有费率丢失也会停止，原先缺失的可选缓存费率变为有效正费率则可自动更新。此时请人工核对上游价格，并修订基准价格后重新运行；没有自动放行开关。

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
| `off` | 不读取额度，也不创建实时查询线程。 |

实时额度只读取当前 app-server 的 `rateLimitsByLimitId` 和 `rateLimitResetCredits.availableCount`；不会解析旧的单桶 `rateLimits` 或顶层 snake_case 别名。实时接口与日志快照是独立的当前数据源，`--quota logs` 及 `auto` 的日志回退仍可用。

`watch` 默认每 2 秒刷新，实时额度默认每 30 秒轮询；用 `--refresh` 与 `--quota-interval` 调整。`--count N` 在刷新 N 次后退出，`0` 表示持续运行。存在多个额度桶时默认优先选择 `codex` 桶，可用 `--limit LIMIT_ID` 切换。TTY 默认使用彩色圆角卡片和额度进度条：标题/边框为青色，金额高亮，低/中/高额度用量分别使用绿/黄/红色；`--no-color` 或 `NO_COLOR` 关闭 ANSI，`--color` 可在非 TTY 启用颜色，JSON 始终不含样式控制符。

每个可用的额度窗口以 **Primary**、**Secondary** 或 **Individual Limit** 区分，统一标为 **Current cycle**，并显示实际周期起点、重置时间和观测时间，以及账户已用/剩余比例、**LOCAL API COST**（窄卡为 **Local Cost Used**）、**EST. TOTAL**、**EST. REMAINING** 和 token 明细。完整报告还会显示缓存写入、reasoning、session/event 数、完整计价的 token 占比和周期起点；宽屏金额分栏，窄屏卡片保留本机成本。watch 使用紧凑卡片，在宽度不少于 96 列且恰有两个窗口时并排显示。

当前周期范围在每次 report/watch 时都根据接口最新的重置时间减去 `window_minutes` 重新计算，统计截至额度观测时间；它独立于 `--today`、`--days`、`--since`、`--until` 选择的 **Selected report period**。这不预设一周或其他固定长度，任意有效窗口长度均可用。手动提前 reset 更新接口边界后，即使日志缓存未变化，工具也会切换到新周期并排除旧周期用量；不会仅凭已用百分比下降推断 reset。即使额度已用比例为 0 或无法外推，工具仍显示可用的本机周期用量和成本，并说明投影不可用。过期或无效窗口仅显示不可用，不能充当当前周期；本机外推并非官方额度。若无法确定模型与额度桶的对应关系，工具不会为专属额度桶作此类外推。

## JSON v2

使用 `--json` 时，普通报告输出一个严格 JSON 文档；`watch --json` 每帧输出一行 NDJSON。`--output FILE` 原子写入完整 JSON；在 watch 模式中保存最后一帧。输出不会覆盖脚本本身或 session JSONL 日志。终端的日期分组按最近日期优先，模型和 session 按总 token 用量降序；`--top` 只截断终端展示，JSON 始终包含完整分组数据。

持续刷新时，`ReportCache` 会复用未变化日志的会话、计价和汇总结果；滑动时间窗口只有跨过用量事件边界时才重新汇总。

顶层报告 JSON 的 `schema_version` 为 `2`，包含 `version`、`generated_at`、`period`、`roots`、`pricing`、`diagnostics`、`summary` 和 `sessions`。`pricing` 包含价格表的 `verified_at`、`catalog_source`（运行时仅为 `github`）、本次下载时间 `fetched_at`、陈旧标记 `stale` 与提示 `warnings`，以及顶层的价格来源 `price_source` 和模型身份来源 `model_source`；每个模型同层包含 `model_source` 和 `rule_source`，并保留普通单价和 `long_input`、`long_cached_input`、`long_cache_write`、`long_output` 长上下文单价字段，`long_context_scope` 在报告中固定为 `request`。`cached_input` 和 `long_cached_input` 可为 `null`。价格表超过 45 天未检查时会标为陈旧并给出提示。`prices --json` 同样返回这些目录元数据和每模型价格字段。`prices.json` 自身只接受 schema 2：顶层 `provider` 为 `openai`、`model_source` 为官方模型目录，`source` 严格为 `models.dev` 的价格来源；每个模型的 `model_source` 必须是对应的官方模型页，新同步的长上下文 `rule_source` 为 `models.dev`，旧目录中的官方模型页来源仍可解析，无长上下文时 `rule_source` 为 `null`。这些来源字段表示数据出处，不代表签名或官方核价。`summary` 保留常用的 `usage`、`by_model`、`by_day`、`by_session`、`unpriced_usage` 与 `api_cost_usd` 语义，并新增 `known_api_cost_usd`、`estimate_is_partial`、`uncertain_pricing_usage`、`pricing_issues`；每个 session 可包含 `parent_session_id`。JSON 字段和数值计价逻辑遵循当前报告 schema v2；参数不接受缩写或旧别名。

## 数据质量提示

报告和 JSON 的 `diagnostics` 会显示扫描与解析中的事实性提示，例如目录或文件不可读、损坏 JSON 行、无效用量快照、尚未写完的日志行、重复快照、继承的 fork 基线或无法归属的 fork 基线。计数器重置会作为新段处理；分项计数下降会给出诊断。终端中来自日志的文本会过滤控制字符。

## 许可证

本项目采用 [GNU Affero General Public License v3.0](./LICENSE)，SPDX 标识为 `AGPL-3.0-only`。
