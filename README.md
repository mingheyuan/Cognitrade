# Cognitrade

当前目录是项目运行根目录。

`Cognitrade` 是一个面向 A 股研究场景的 CLI-first 多 Agent 研究系统。当前代码主线已经从早期最小闭环推进到：

- 顶层研究路由：显式 intent、动态路由表、规则/语义匹配、LLM fallback
- 研究运行时：`planner -> orchestrator -> collector/retriever/reporter`
- 数据层：provider chain、fallback merge、Qdrant 检索、长期 memory
- 对话层：默认 line mode、持久化 chat thread、auto compact、checkpoint、query-aware memory recall

## 当前支持的研究意图

当前代码中的顶层 research intent 有：

- `stock_check`
- `stock_compare`
- `stock_screen`
- `sector_research`
- `distill_kol_logic`
- `review_thesis`
- `methodology_evolution`

其中 `methodology_evolution` 目前更偏内部演进链路，不是主要的手工输入入口。

## 目录结构

```text
Cognitrade/
├─ research_system/    # 主代码
├─ tests/              # 本地回归测试
├─ workspace/          # 运行数据、配置、taxonomy、runs、memory、Qdrant 本地库
├─ .env
└─ .env.example
```

`workspace/config/` 和 `workspace/taxonomy/` 是当前运行时真正读取的配置目录，不是包内静态配置目录。

## 环境变量

先准备根目录 `.env`。最小示例见 [`.env.example`](./.env.example)。

当前常用项：

```env
OPENAI_API_KEY=
OPENAI_BASE_URL=
OPENAI_MODEL=GPT-5.5
QDRANT_URL=
QDRANT_API_KEY=
QDRANT_COLLECTION_NAME=research_evidence

MEM0_API_KEY=
MEM0_BASE_URL=

ROUTER_REDIS_URL=
ROUTER_REDIS_ACTIVE_KEY=cognitrade:router:intent_rules:active
ROUTER_REDIS_KEY_PREFIX=cognitrade:router:intent_rules:
ROUTER_REDIS_LAST_GOOD_KEY=cognitrade:router:intent_rules:last_good
ROUTER_REDIS_TIMEOUT_SECONDS=0.2
ROUTER_RULES_CACHE_TTL_SECONDS=2.0
ROUTER_SEED_DEFAULTS=true

PROVIDER_PROBE_TIMEOUT_SECONDS=1.0
COGNITRADE_PROVIDERS_MARKET=
COGNITRADE_PROVIDERS_FUNDAMENTAL=
COGNITRADE_PROVIDERS_NOTICE=
COGNITRADE_PROVIDERS_SECTOR=
COGNITRADE_PROVIDERS_CAPITAL_FLOW=
COGNITRADE_PROVIDERS_SENTIMENT=

DEFAULT_TIMEZONE=Asia/Shanghai
LOG_LEVEL=INFO
LLM_REQUEST_TIMEOUT_SECONDS=25
RUN_TIMEOUT_SECONDS=120
EXTERNAL_WAIT_TIMEOUT_SECONDS=30
```

说明：

- `OPENAI_BASE_URL` 若填写兼容 OpenAI 的网关地址，代码会归一化到 `/v1`
- 不配置 `QDRANT_URL` 时，会优先使用本地文件模式
- `MEM0_*` 用于长期 memory 远端同步；未配置时仍会保留本地导出与本地读取 fallback
- `ROUTER_*` 用于 Redis 动态路由控制面；未配置时会退回本地 `router_defaults.yml`
- `COGNITRADE_PROVIDERS_*` 用于 provider chain 覆盖，优先级高于默认 `provider_policy.yml`

## 配置文件

当前运行时会自动 seed 并读取这些配置：

```text
workspace/config/
├─ retrieval_policy.yml
├─ methodology_manifest.yml
├─ review_policy.yml
├─ router_defaults.yml
├─ provider_policy.yml
└─ data_sources.yml

workspace/taxonomy/
├─ dimensions.yml
├─ dimension_aliases.yml
└─ dimension_patterns.yml
```

## 常用命令

在项目根目录执行。

### 运行一次研究

```bash
python -m research_system.cli.app run "帮我看下 000001 这个股票" --json
python -m research_system.cli.app run "分析茅台、宁德时代、比亚迪，谁更值得投资" --json
python -m research_system.cli.app run "最近创新药板块怎么样" --json
python -m research_system.cli.app run "请蒸馏这个雪球 statuses 文件 D:\\path\\to\\statuses.jsonl" --intent distill_kol_logic --json
python -m research_system.cli.app run "复核上次创新药判断" --intent review_thesis --run-id <target_run_id> --json
```

### 交互模式

默认 `chat` 是 line mode，交互体验更接近 `codex`，依赖终端原生滚动：

```bash
python -m research_system.cli.app chat
```

如果你明确要进入全屏 TUI：

```bash
python -m research_system.cli.app chat --ui tui
```

如果 `--ui tui` 在当前终端不可用，会自动回退到 line mode。

### chat 的线程和记忆模型

当前 `chat` 不是一次性输入壳，而是持久化 thread：

- 每个 chat 会话都有稳定 `thread_id`
- thread 会持久化 recent turns、summary history、archive 和 checkpoint
- thread 超过阈值会 auto compact，生成可复用摘要链
- 后续 query 会自动带入：
  - 当前 thread profile
  - recent turns
  - compact summary
  - recent chat research memory
  - query-aware relevant memory

当前大致分三层记忆：

1. `thread short-term memory`
   - 当前 chat thread 的 recent turns、summary、checkpoint
2. `chat long-term memory`
   - 跨会话的 chat research summary，优先走 mem0，失败时回本地导出
3. `methodology long-term memory`
   - 经审核沉淀的方法论记忆，用于 retrieval/evolution 辅助

### chat 常用指令

```text
/help
/exit
/new
/thread [thread_id]
/threads [limit]
/compact
/intent <intent|auto>
/run-id <run_id|clear>
/thinking <on|off>
/events <off|compact|full>
/stop
/run <query>
/review <run_id> <query>
/history [limit] [status]
/status
/doctor
/check
/replay <run_id|last>
/resume <run_id|last>
/cancel <run_id|last>
/cleanup
/reindex
/last
/clear
```

行为说明：

- 直接输入自然语言，默认会被当成 research query 或 basic chat
- 多轮 follow-up 会优先沿当前 thread 和最近研究上下文续接
- 如果上一轮停在 `needs_clarification`，下一句普通输入会自动作为补充信息继续
- `/new` 新建 thread
- `/thread <thread_id>` 切换到已有 thread，并恢复 checkpoint / memory
- `/threads` 查看最近 thread 列表、topic、intent 和 active marker
- `/compact` 手动压缩当前 thread
- `/last` 查看当前 session 默认值和最近 run
- `/clear` 清空当前 intent 和 review target 等默认值

### 其他 CLI 命令

```bash
python -m research_system.cli.app history --json
python -m research_system.cli.app replay <run_id> --json
python -m research_system.cli.app resume <run_id> --json
python -m research_system.cli.app cancel <run_id> --json
python -m research_system.cli.app status --json
python -m research_system.cli.app doctor --json
python -m research_system.cli.app check --json
python -m research_system.cli.app cleanup --json
python -m research_system.cli.app reindex-qdrant --json
```

## 运行产物

每次 `run` 会生成：

```text
workspace/runs/<run_id>/
├─ request.json
├─ task_plan.json
├─ run_state.json
├─ run_control.json
├─ checkpoint.json
├─ events.jsonl
├─ logs.jsonl
├─ blackboard.jsonl
├─ result.json
└─ summary.json
```

和 chat / memory 相关的持久化产物主要在：

```text
workspace/memory/
├─ exports/
└─ cache/
```

## 当前已实现能力

### stock_check

- 个股 market/fundamental/notice/sector 快照
- methodology 证据：`comps`、`dcf`、`cashflow_resilience`
- `Retriever` 去重、摘要和方法论 memory 注入
- `Reporter` 生成最终研究结论

### stock_compare

- 多标的对比入口
- 支持代码和常见股票名场景
- 对每只标的补方法论证据后做横向比较

### stock_screen

- 候选池构建与筛选
- top candidate 补采快照与轻量 review
- 新 snapshot 维度增强：
  - `lhb`
  - `capital_flow`
  - `sentiment`
  - `events`

### sector_research

- 板块识别与真实板块快照
- `HS300` 基准对照
- 相对强弱、广度、催化、政策与信号摘要

### distill_kol_logic

- `collector -> distiller -> critic -> retriever -> reporter`
- `logic_block / distilled_dimension / review_finding` 落 blackboard
- chunked distillation
- dimension normalization
- Qdrant 入库与回灌

### review_thesis

- `collector -> reviewer -> retriever -> reporter`
- 支持 `--run-id <target_run_id>` 复核旧 run
- 当前最小可复核 claim：
  - `direction`
  - `catalyst`
  - `time_window`

## 本地检查

```bash
python -m research_system.cli.app check --json
python -m unittest discover -s tests -p "test_*.py" -v
python -m compileall research_system tests
```

截至 `2026-07-01`，当前工作树已本地通过：

- `python -m unittest discover -s tests -p "test_*.py"` `398 tests OK`
- `python -m compileall research_system tests`

`doctor` 现在会额外报告：

- 路由配置与当前版本
- provider chain / network profile
- workspace/config/taxonomy 布局
- local Qdrant / memory 根目录可见性

## 当前边界

- `methodology_evolution` 仍是最小闭环，不是完整的自动演进系统
- `mem0` 已接远端优先 + 本地 fallback，但长期记忆策略还在继续收口
- 数据源链已支持 provider/fallback，但还不是最终的全量浏览器/搜索 fallback 方案
- `chat` 已有 thread、checkpoint、compact、query-aware recall，但还没完全等同于 OpenHands/LangGraph 的完整状态机
