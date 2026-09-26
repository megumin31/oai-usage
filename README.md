# oai-usage

`oai-usage` 是一个本地运行的命令行工具，用于汇总 Codex session 日志中的 token 用量、按当前公开 Standard API 单价计算等价成本，并显示账户额度信息。

- Python 3.9+；只使用标准库，无需安装第三方依赖。
- 原始 JSONL 日志只读；工具不保留会话正文，不上传日志，也不会读取凭据内容或消耗额度重置次数。
- 安装时须将 [`oai-usage`](./oai-usage)、[`oai_price_catalog.py`](./oai_price_catalog.py) 与 [`prices.json`](./prices.json) 放在同一目录；后者是首次离线可用的价格表。当前版本为 0.0.1；程序提供的 CLI、报告、诊断和 JSON 说明文字使用英文，日志中的模型名等用户数据保持原样。

## 快速开始

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

# 不访问 GitHub，使用有效的本机缓存或随附价格表
./oai-usage prices --offline-prices

# 忽略网络和本机缓存，只使用随附价格表
./oai-usage prices --bundled-prices

# 清除当前及上一份本机价格缓存后重新加载
./oai-usage prices --reset-price-cache
```

`report` 是默认子命令，因此 `./oai-usage --today` 等同于 `./oai-usage report --today`。使用 `./oai-usage report --help`、`./oai-usage watch --help` 或 `./oai-usage prices --help` 查看完整参数。

`--project` 为兼容参数，当前默认开启；使用 `--no-project` 可隐藏终端中的当前周期部分，二者互斥。`--quota off` 只生成本地报告，不显示当前周期或账户额度。

## 安装与测试

脚本可直接执行。如需将它作为本机命令安装到目标路径，若该路径已有命令，请先保留原文件的备份；以下命令会写入目标路径：

```sh
install -m 755 ./oai-usage ~/.local/bin/oai-usage
install -m 644 ./oai_price_catalog.py ~/.local/bin/oai_price_catalog.py
install -m 644 ./prices.json ~/.local/bin/prices.json
oai-usage --version
```

工具继续兼容 Python 3.9+；日常联网使用建议 Python 3.13 或 3.14。

测试使用标准库的 `unittest`：

```sh
python3 -m unittest discover -s tests -q
```

## 日志范围与日期

未指定 `--root` 时，工具从 `$CODEX_HOME/sessions` 和 `$CODEX_HOME/archived_sessions` 扫描日志；若没有设置 `CODEX_HOME`，则使用 `~/.codex`。指定一个或多个 `--root` 后，默认目录会被替代。

时间范围规则：

- 默认 `--days 30` 是从当前时间回看 30×24 小时；`--days all` 或 `--days 0` 不限制起点。
- `--today`、`--since` 与 `--until` 使用 `--timezone` 指定的统计时区；默认是系统本地时区。`--until` 包含该日。
- 有时间范围时，缺少时间戳的用量不会被纳入统计；数量记录在 JSON 的诊断和汇总字段中。

同一 session 即使跨多个日志文件也会按时间戳和累计用量合并去重。session 的首条 `session_meta.id` 是该 session 的固定身份；父任务 ID 仅作为关联信息保存，因此子任务不会并入父任务。继承的 fork 历史快照不会重复计入新用量。

## 成本、价格与额度

报告中的“API 等价成本”是按当前公开的 Standard API 单价换算的估计，**不是历史账单，也不是订阅账单**。价格表由仓库中的 [`prices.json`](./prices.json) 提供；运行 `prices` 可查看模型价格、长上下文规则、最近核对日期和当前目录来源。

每次启动 `report`、`watch` 或 `prices` 时，工具会从固定 HTTPS 地址的公开仓库 `main/prices.json` 查询有效的价格表，并缓存到 `$XDG_CACHE_HOME/oai-usage/prices.json`（未设置时为 `~/.cache/oai-usage/prices.json`）。远端不可用或数据校验失败时，按当前有效缓存、上一份有效缓存 `prices.previous.json`、脚本同目录随附表的顺序回退；不再按 `verified_at` 比较优先级。`verified_at` 必须是 UTC 当天或更早日期。客户端价格表下载上限为 1MB、socket 3 秒、总计 6 秒；下载和本地数据均会限制大小、格式和字段，重定向或不完整数据不会写入缓存。`watch` 会每小时重新查询；刷新时先按远端、当前缓存、上一份缓存、随附表的顺序回退，所有来源都不可用时才继续使用内存中的有效价格表。

使用 `--offline-prices` 或设置 `OAI_USAGE_OFFLINE_PRICES=1` 可禁止远端查询，此时仍按上述顺序使用本机缓存和随附价格表。`--bundled-prices` 会完全忽略网络和缓存，仅使用脚本同目录的表；`--reset-price-cache` 会清除当前及上一份缓存，再按所选来源加载。`--price` 指定的手动单价优先于价格表。

该估算不包括工具费用、地区加价、Fast/优先服务或订阅费用。成本按用量事件计费，并以 `Decimal` 计算；已知模型的长上下文规则按模型适用的请求或 session 级范围处理。可以覆盖单价：

```sh
./oai-usage --price 'my-model=2.5,0.25,15,3.125'
```

参数格式为 `MODEL=IN,CACHED,OUT[,WRITE]`；四项都是每百万 tokens 的美元单价，`WRITE` 可选，所有值必须是有限的非负数。已知模型别名会统一解析，相关长上下文规则仍然生效。

仓库的 GitHub Actions 每日 03:17 UTC 以及手动触发时，从 OpenAI 官方定价页读取 Standard 价格，并从模型页读取长上下文阈值和请求或 session 范围。Actions 抓取较大的官方 Markdown 页面，上限为 2MB、socket 5 秒、每页总计 20 秒；它与客户端共用下载器并同样拒绝重定向。只在本仓库 `main` 上运行的只读校验任务生成候选表；独立发布任务以受信任代码再次校验后，才通过 GitHub API 原子更新固定的 `prices.json`。解析、测试或候选价格异常都会停止发布：包括大批模型消失、新模型任一存在费率为零、已有模型零价状态变化、单价剧烈变化或长上下文规则变化；不适用的缓存写入价格可保持 `null`。此时请人工核对官方价格，并修订基准价格或解析器后重新运行；没有自动放行开关。

解析和测试均成功时，价格变化会立即提交；价格未变但 `verified_at` 已满 30 天时，也会仅刷新该核对日期并提交，以维持公开仓库的定时任务活动。未满 30 天且价格未变时不提交。该同步仅覆盖具备输入、缓存输入和输出单价的 GPT 模型。该机制仍信任该 GitHub 仓库及其发布流程：目录和来源字段没有签名，不能独自证明真实价格；发布写入权限也是仓库级别，不能限定到单一路径。

未知模型不会套用其他模型的价格。请求大小无法确定、而价格可能因长上下文阈值变化时，成本也会标记为不完整。因此：

- `api_cost_usd` 仅在总成本完整时给出数值；不完整时为 `null`。
- `known_api_cost_usd` 始终给出已知部分的成本。
- `unpriced_usage` 表示未定价模型的用量；`uncertain_pricing_usage` 表示计价条件无法确定的用量。

额度来源由 `--quota` 控制：

| 选项 | 行为 |
| --- | --- |
| `auto`（默认） | 尝试实时读取；失败时回退到日志快照。 |
| `live` | 只尝试实时读取。 |
| `logs` | 只使用日志中的额度快照。 |
| `off` | 不读取额度，也不创建实时查询线程。 |

`watch` 默认每 2 秒刷新，实时额度默认每 30 秒轮询；用 `--refresh` 与 `--quota-interval` 调整。`--count N` 在刷新 N 次后退出，`0` 表示持续运行。存在多个额度桶时默认优先选择 `codex` 桶，可用 `--limit LIMIT_ID` 切换。TTY 默认使用彩色圆角卡片和额度进度条：标题/边框为青色，金额高亮，低/中/高额度用量分别使用绿/黄/红色；`--no-color` 或 `NO_COLOR` 关闭 ANSI，`--color` 可在非 TTY 启用颜色，JSON 始终不含样式控制符。

每个可用的额度窗口以 **Primary**、**Secondary** 或 **Individual Limit** 区分，统一标为 **Current cycle**，并显示实际周期起点、重置时间和观测时间，以及账户已用/剩余比例、**LOCAL API COST**（窄卡为 **Local Cost Used**）、**EST. TOTAL**、**EST. REMAINING** 和 token 明细。完整报告还会显示缓存写入、reasoning、session/event 数、完整计价的 token 占比和周期起点；宽屏金额分栏，窄屏卡片保留本机成本。watch 使用紧凑卡片，在宽度不少于 96 列且恰有两个窗口时并排显示。

当前周期范围在每次 report/watch 时都根据接口最新的重置时间减去 `window_minutes` 重新计算，统计截至额度观测时间；它独立于 `--today`、`--days`、`--since`、`--until` 选择的 **Selected report period**。这不预设一周或其他固定长度，任意有效窗口长度均可用。手动提前 reset 更新接口边界后，即使日志缓存未变化，工具也会切换到新周期并排除旧周期用量；不会仅凭已用百分比下降推断 reset。即使额度已用比例为 0 或无法外推，工具仍显示可用的本机周期用量和成本，并说明投影不可用。过期或无效窗口仅显示不可用，不能充当当前周期；本机外推并非官方额度。若无法确定模型与额度桶的对应关系，工具不会为专属额度桶作此类外推。

## JSON v2 与兼容性

使用 `--json` 时，普通报告输出一个严格 JSON 文档；`watch --json` 每帧输出一行 NDJSON。`--output FILE` 原子写入完整 JSON；在 watch 模式中保存最后一帧。输出不会覆盖脚本本身或 session JSONL 日志。终端的日期分组按最近日期优先，模型和 session 按总 token 用量降序；`--top` 只截断终端展示，JSON 始终包含完整分组数据。

持续刷新时，`ReportCache` 会复用未变化日志的会话、计价和汇总结果；滑动时间窗口只有跨过用量事件边界时才重新汇总。

顶层 JSON 的 `schema_version` 为 `2`，包含 `version`、`generated_at`、`period`、`roots`、`pricing`、`diagnostics`、`summary` 和 `sessions`。`pricing` 包含价格表的 `verified_at`、`catalog_source`（`github`、`cache`、`previous_cache` 或 `bundled`）、本地下载时间 `fetched_at`、陈旧标记 `stale` 与提示 `warnings`，以及每个模型的普通和长上下文单价；长上下文字段为 `long_input`、`long_cached_input`、`long_cache_write` 和 `long_output`。价格表超过 45 天未核对时会标为陈旧并给出提示。`prices --json` 同样返回这些目录元数据和每模型价格字段。`summary` 保留常用的 `usage`、`by_model`、`by_day`、`by_session`、`unpriced_usage` 与 `api_cost_usd` 语义，并新增 `known_api_cost_usd`、`estimate_is_partial`、`uncertain_pricing_usage`、`pricing_issues`；每个 session 可包含 `parent_session_id`。JSON 字段和数值计价逻辑保持 schema v2 兼容，说明文字改为英文；旧 JSON 使用者应按 schema v2 升级解析。

下列旧参数仍可用作别名：

- `--watch`、`-w` → `watch`
- `--group-by` → `--by`
- `--all-dimensions` → `--all`
- `--limits-source` → `--quota`
- `--limits-timeout` → `--timeout`
- `--json-out` → `--output`
- `--interval`、`-n` → `--refresh`
- `--project` 保留且默认开启；`--no-project` 可隐藏终端当前周期部分

## 数据质量提示

报告和 JSON 的 `diagnostics` 会显示扫描与解析中的事实性提示，例如目录或文件不可读、损坏 JSON 行、无效用量快照、尚未写完的日志行、重复快照、继承的 fork 基线或无法归属的 fork 基线。计数器重置会作为新段处理；分项计数下降会给出诊断。终端中来自日志的文本会过滤控制字符。

## 许可证

本项目采用 [GNU Affero General Public License v3.0](./LICENSE)，SPDX 标识为 `AGPL-3.0-only`。
