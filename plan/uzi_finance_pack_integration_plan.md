# UZI 能力接入 Cognitrade Finance Pack 方案

## 1. 文档定位

本文定义 UZI Skills 接入 Cognitrade Agent OS 的目标架构、边界、迁移顺序和验收标准。

本文是实施方案，不是 UZI CLI 的使用说明。方案默认 UZI 当前代码位于本地临时目录：

```text
D:\Cognitrade\_tmp_uzi_skill
```

该目录只能作为能力盘点、原型验证和迁移参考，不能成为 Cognitrade 生产运行时的硬编码依赖。

## 2. 总体决策

UZI 不直接作为 Cognitrade 的第二套后端，也不由前端直接调用 `run.py` 或读取 UZI HTML。

UZI 的专业方法、数据采集器、评分规则和报告视觉应逐步收敛到 `finance_ashare` DomainPack；Agent OS 继续拥有唯一的任务入口、动态图、调度、Artifact、缓存治理、质量 Gate 和审计能力。

目标关系：

```text
用户请求
  ↓
Main Agent
  ↓
Finance Pack Task Intake
  ↓
Capability Planner
  ↓
动态 ExecutionGraph
  ├─ Finance Snapshot Skills
  ├─ UZI 方法论 Subagent
  ├─ 投资者 Panel Subagent
  ├─ 风险/杀猪盘 Subagent
  ├─ Quality Gate
  └─ Report Composer
        ↓
ResearchReport Artifact + ReportPresentation Artifact
        ↓
Cognitrade Workbench / HTML Exporter / SSE
```

核心原则：

- UZI 是 Finance Pack 的专业能力来源，不是 Agent OS 的替代运行时。
- `run.py` 是迁移期兼容入口，不是 Cognitrade 正常 API 路径。
- 前端只读取 Agent OS API 返回的结构化 Artifact，不解析 UZI 原始 HTML 或进程输出。
- 所有 UZI 计算结果必须具备来源、版本、生产节点、置信度和风险标识。
- UZI 的完整报告视觉可以保留为导出器，但语义数据必须先进入 ArtifactBoard。

### 2.1 唯一正常运行路径

所有进入 Finance Pack 的标准 intent 都只能使用一条 Agent OS 正常路径；包括
`stock_check`、`stock_compare`、`stock_screen`、`sector_research` 和
`review_thesis`。下面的个股研究链只是其中一种规划结果，不是其他 intent 的默认模板：

```text
Main Agent Core Loop
  -> Finance Pack intake
  -> Pack capability catalog / method contracts
  -> CapabilityPlanner
  -> ExecutionGraphRunner
  -> Finance Pack Subagent / Tool
  -> ArtifactBoard
  -> Main Agent synthesis
```

`finance.uzi.report.compose` 是 UZI 能力迁移后的 Finance Pack Composer；它与
`finance.report.compose` 不能在同一个正常请求中并行产出两份同类报告。若保留通用
`finance.analysis.evaluate`，必须在 Pack manifest 中明确它是一个独立、可复用的方法
能力（例如 `cashflow_resilience`），只能在用户明确请求该方法时进入候选选择，不能作为
任何默认研究 intent 的候选能力隐式装配。旧 Composer、
旧固定分析链或 UZI CLI 只能存在于显式兼容/等价测试入口，不能出现在正常候选能力集合中。

正常路径的验收必须同时提供静态依赖检查和运行 telemetry：正常 API、Chat、CLI、恢复
和后台任务对 legacy Composer、UZI CLI、UZI raw dict 和旧固定主链的调用次数均为零。

## 3. 当前 UZI 能力盘点

### 3.1 报告产物

UZI 当前主要生成四类产物：

```text
full-report.html       16:9 交互式完整报告
share-card.png         1080x1920 竖版分享卡
war-report.png         1920x1080 横版战报
one-liner.txt          一句话摘要
```

报告的核心信息层级为：

1. 标的、价格、综合评分和安全状态；
2. 一句话核心结论；
3. 趋势、价位、量能、筹码、新闻、风险、催化剂；
4. 多空观点对撞和 punchline；
5. 投资者 Panel 评分及评论；
6. 多维雷达和维度详情；
7. 风险清单、买入区间和行动计划；
8. 数据时间戳和免责声明。

### 3.2 运行 Profile

UZI 参考实现包含四档运行 Profile。Profile 是预算、最低交付物和能力标签的约束，
不是固定节点图；`quick-scan` 在 Cognitrade 中统一命名为 `quick`，避免同一概念
在 CLI、API 和 Pack manifest 中出现两个名称。

| 模式 | 设计范围 | 设计产物 | 当前运行注意事项 |
|---|---|---|---|
| `quick` | Cognitrade 自有快速交付档位；由 Pack policy 定义最小估值/风险快照 | 结构化摘要 | 不宣称与 UZI Profile 等价；不要求 UZI Panel |
| `lite` | 7 个核心维度、10 位评委、DCF | 完整 HTML | 默认 pipeline 当前未完全遵守维度过滤 |
| `medium` | 20 个原始 Profile 维度、完整 Panel、17 种方法 | 完整 HTML | 适合日常深度分析 |
| `deep` | medium 加多空辩论、Segmental 等 | 完整 HTML | 成本和等待时间最高 |

`quick` 是 Cognitrade 为短响应场景增加的产品档位，不是 UZI 原始
`analysis_profile.py` 的结果等价档位。结果级等价只对照 UZI 的 `lite`、`medium`
和 `deep`；如果 Pack policy 为 `quick` 选择少量 Panel 视角，那些视角只能标记为
Pack 快速方法论，不得在报告中称为 UZI Lite Panel。Profile 的参与数量、启用维度和
方法集合必须由同一个 Pack manifest policy 读取，文档、manifest 和 registry 不得各自
维护一套数量。

计数时必须区分：UZI 的 20 个原始 Profile 维度、22 个采集模块（其中包含 2 个辅助模块）
以及由原始维度计算出的估值模型、研究工作流和 Deep 方法结果。文档中的“22 维”只
可作为 UZI 产品口径，不能直接当作 22 个可采集 Snapshot；迁移验收使用逐项维度清单。

### 3.3 已发现的迁移阻碍

当前 UZI 的 Lite 不能直接作为 Cognitrade 的五分钟 SLA 依据：

- `lib/pipeline/collect.py` 当前按完整注册表遍历 fetcher，未将 `analysis_profile.fetchers_enabled` 作为采集过滤条件；
- legacy 路径虽然会过滤主采集维度，但 wave 3 仍会执行基金持仓和相似股票采集；
- fetcher 对外部数据源有 90、120、150 或 180 秒级别的超时；
- `resume` 会复用同一 ticker 的有效缓存，但实时基础行情仍会刷新；
- 部分缓存只保存原始数据，不包含 Cognitrade 需要的 Artifact 版本、来源和失效关系；
- 直接调用 UZI CLI 会绕过 Agent OS 的统一取消、预算、事件和审计协议。

迁移前必须把这些行为显式化，不能继续依赖“文档预计 1-2 分钟”的声明。

### 3.4 迁移基线与计数口径

UZI 的迁移比较必须绑定可复现的源版本，而不能使用 README、宣传文案或不同版本
之间的数量口径。当前参考工作树对应：

```text
source: D:\Cognitrade\_tmp_uzi_skill
commit: 775c76101e8890492d21c2e2827692456fcddb58
methodology_version: 3.9.1
license: MIT
```

该版本的计数必须区分：

- `fetch_*.py` 是 22 个采集模块；
- `analysis_profile._ALL_FETCHERS` 是 20 个参与标准 Profile 的原始维度 ID；
- 其余采集模块可能是辅助采集器，必须在映射表中明确归属，不能直接把模块数当作维度数；
- Panel 注册表有 66 个成员，但 Lite、Medium、Deep 的 Profile 参与数分别是 10、51、51；
- Finance Pack 的机构模型、研究工作流和 Deep 交叉检验是派生方法论产物，不计入原始外部维度。

结果等价测试以该 commit 的运行时注册表为准。若升级 UZI 版本，必须重新生成映射快照、
版本指纹和结果基线，不能只修改 `methodology_version` 字符串。

### 3.4.1 功能迁移与来源等价的区别

本方案把“功能性迁移”和“来源结果等价”分开验收：

- 功能性迁移：已安装的 `finance_ashare` 能通过 Agent OS 接受请求、动态选择能力、
  调用 Pack Tool/Skill/Subagent、将结果写入 ArtifactBoard、执行 Gate，并由 Main Agent
  生成可回放的中文 synthesis、报告和前端结构化视图；Provider 不可用时必须产出明确
  `data_gap`，不能伪造可用事实。
- 来源结果等价：某一 UZI 维度或方法与锁定 commit 的字段、公式、枚举或语义结果达到
  映射文件声明的 `exact`、`numeric_tolerance` 或 `semantic` 等级。Provider gap 或
  `not_equivalent` 只能阻止该条目的等价标记，不能阻止已经明确降级的功能性交付。

因此，功能性迁移完成不等于 22 个来源全部等价；但任何未等价能力都必须在 Artifact、
报告、前端和审计事件中保留真实的缺口、假设、来源状态和责任节点，不能用“已迁移”
掩盖“未验证”等价。

### 3.5 Lite、缓存与五分钟目标

“第一次先跑 Lite，之后直接读缓存”不是目标架构。正确策略是每次请求都由 Main Agent 和 Planner 根据用户目标、预算、Board 中 Artifact 的新鲜度以及数据质量决定当前图：

1. 冷启动时，如果用户只要求快速体检，Planner 选择 Lite 能力集合；如果用户明确要求深度研究，则可以直接选择 Medium 或 Deep，不强制先跑 Lite。
2. Lite 不是固定的第一层流程，而是一个有明确预算和最低交付标准的能力配置。它仍然由动态 Graph 物化，节点集合可以因输入目标和已有 Artifact 而变化。
3. 缓存命中时只复用仍然有效、来源和 Schema 版本兼容的 Artifact。市场价格等高波动数据按 TTL 刷新，过期、降级或来源不完整的 Artifact 不得静默复用。
4. 用户从 Lite 升级到 Medium 或 Deep 时，Planner 只补充 Board 中缺失或过期的能力，并对新增节点重新执行 Gate；不能把旧报告整份当作新事实。
5. 当某个数据源超时或预算不足时，Agent OS 应在硬截止时间内交付带有 `data_gap`、来源状态和降级原因的结果，并通过 SSE 继续暴露后续可选节点的状态。不能让单个慢 fetcher 阻塞整个会话。

因此，五分钟是 Lite 冷启动的产品目标，不是当前 UZI CLI 已经满足的事实。要达成该目标，必须先完成 Pack 内的维度白名单、每个 Tool 的独立超时、取消协议、并发采集和最低交付 Gate；在这些改动完成前，不能把 UZI `lite` 命令的运行时间写成 Cognitrade 的 SLA。

建议的运行预算如下，具体数值由 Agent OS Governance 配置最终确定：

| 运行档位 | 冷启动目标 | 硬截止时间 | 交付策略 |
|---|---:|---:|---|
| `quick` | 30-60 秒 | 90 秒 | 只交付核心快照、风险摘要和明确的数据缺口 |
| `lite` | 2-3 分钟 | 5 分钟 | 交付核心方法论、证据、风险和可审计报告 |
| `medium` | 按任务预算 | 按任务预算 | 增量补充同行、产业链、舆情或政策能力 |
| `deep` | 长任务 | 按任务预算并可人工审批 | 启用多轮研究、Panel 辩论和更严格 Gate |

上表是产品预算，不代表当前 UZI 实现已经达标。UZI 现有 CLI 只能在迁移验证期间作为隔离的参考实现或缓存导入源。

硬截止语义必须在 Governance 中固定：达到最低交付 Gate 后，系统要么取消剩余可选
节点并以 `completed_partial` 结束，要么把最低交付先作为独立交付、保持 Run 为
`running` 并继续后台执行；不能同时把 Run 标记为 completed 又声称后续节点仍在
运行。无论采用哪种策略，取消、部分交付、继续运行和最终补充都必须有事件、快照和
可恢复的状态转换。

## 4. Cognitrade 边界设计

### 4.1 Agent OS 固定职责

以下能力不得交给 UZI 或 Finance Pack 自行实现：

- Main Agent 首次路由和普通对话；
- Capability Planner 和动态 ExecutionGraph 生成；
- compound node 延迟展开；
- ready leaf 的统一并发调度；
- Tool/Skill 权限、沙箱、审批和幂等；
- Token、时间、费用、节点数和并发预算；
- 取消、暂停、恢复、重跑和安全点 patch；
- ArtifactBoard 版本链和失效传播；
- 统一事件、SSE、运行快照、回放和审计；
- 跨 DomainPack 的记忆和会话治理。

### 4.2 Finance Pack 专业职责

Finance Pack 只提供：

- 股票、行业和市场数据能力；
- UZI 采集器的 Pack-owned Skill/Tool 实现；
- 财务、估值、Panel、风险和行业方法论；
- 专业 Artifact Schema；
- Planner capability catalog 和深度档位策略；
- 专业质量 Gate 和评测样例；
- Finance Report Composer 和 UZI 风格 Presentation Model。

### 4.3 Core Tool 与 Pack Tool 的边界

文件读取、Board 查询、Memory 查询、Graph 查询、澄清、重规划建议等仍是 Agent OS Core Tool。

财务数据读取、估值计算、龙虎榜分析、杀猪盘检测和投资者 Panel 是 Finance Pack Tool/Skill。所有调用都必须经过统一 Tool Registry，不允许 UZI 脚本直接创建绕过治理的网络线程。

## 5. UZI 能力到 DomainPack 的映射

| UZI 组件 | Cognitrade 目标实现 | 主要产出 |
|---|---|---|
| `fetch_basic` | `market_snapshot_skill` | `market_snapshot` |
| `fetch_financials` | `fundamental_snapshot_skill` | `fundamental_snapshot` |
| `fetch_kline` | `market_snapshot_skill` 或独立技术 Skill | `technical_snapshot` |
| `fetch_valuation` | `valuation_data_skill` | `valuation_snapshot` |
| `fetch_governance` | `governance_snapshot_skill` | `governance_snapshot` |
| `fetch_events` | `events_snapshot_skill` | `events_snapshot` |
| `fetch_lhb` | `lhb_snapshot_skill` | `lhb_snapshot` |
| `fetch_peers` | `peer_snapshot_skill` | `peer_snapshot` |
| `fetch_chain`、`fetch_materials`、`fetch_futures` | 对应行业/供应链 Skill | 专业快照 Artifact |
| `fetch_research`、`fetch_sentiment`、`fetch_contests` | 研报/舆情/组合 Skill | 证据和风险 Artifact |
| DCF / Comps / LBO / 三表 | Methodology Subagent | `valuation_model` |
| 66 位注册投资者；按 Profile 参与 10/51/51 位 | Panel Subagent 或流派 Compound Node | `investor_panel_review` |
| 8 信号杀猪盘检测 | Risk Subagent | `risk_scan` |
| `agent_analysis.json` | Finance Pack 方法分析 Subagent | `financial_analysis`；主 Agent 最终合成统一使用 `main_agent_synthesis` |
| `assemble_report.py` | Presentation Renderer/Exporter | `report_presentation`、HTML/PNG |

UZI 旧脚本可以在迁移早期通过 adapter 被调用，但 adapter 必须输出统一 Artifact，不能把脚本返回的 raw dict 继续作为跨节点共享数据面。

### 5.1 Snapshot 事实模型（必须先定案）

每个可缓存、可重跑、可独立降级的维度必须成为 Board 中独立的 `snapshot`
Artifact。Cognitrade 的生产规范字段统一使用 `snapshot_type`；`dimension` 只允许
作为迁移期输入别名，进入 Board 前必须规范化为 `snapshot_type`。不再按维度注册
一组不同的 Artifact 类型，避免 Planner、缓存和前端出现两套事实协议。每个 Snapshot 至少
携带以下内容：

```text
snapshot_type
scope: symbol / sector / universe
provider / source_refs / retrieved_at
data_period
quality_status
missing_fields / degrade_reasons
facts
```

`stock_snapshot_bundle` 只能是引用这些 Snapshot 的派生索引，不能继续作为唯一事实
容器。它可以保留用于一次性聚合和历史兼容，但 Planner、缓存、质量 Gate 和方法论
输入必须读取独立 Snapshot 的引用和质量状态。这样才能实现按维度 TTL、局部重跑、
Lite 到 Medium 的增量扩展和单来源降级。

迁移验收必须证明：一个维度失败不会使其他维度被重复抓取；一个维度过期只会刷新
该维度；方法论可以声明所需维度而不是依赖整个 Bundle 的存在。

独立 Artifact 不能只停留在输出格式。若多个维度仍由一个批量 Collector 节点产生，
缓存层必须把每个输出按 `dimension/scope/symbol/provider` 独立持久化，并允许只读取、
刷新或失效其中一个输出；整批节点缓存不能作为跨 Run 的维度缓存。若缓存实现只能按
节点复用，则必须把维度拆成独立 Capability。`stock_snapshot_bundle` 的缓存只能
作为可重新构建的索引，不能阻塞单维度重跑。

### 5.2 Snapshot 引用与 Board 重挂载

方法论节点不得只通过 `artifact_type` 判断输入。Planner 必须为每个方法解析可用的
具体 Artifact 引用，并将引用写入节点输入和输出的父 Artifact 链：

```json
{
  "artifact_id": "01...",
  "artifact_type": "snapshot",
  "snapshot_type": "fundamental",
  "scope": "symbol",
  "symbol": "300750.SZ",
  "schema_version": "1",
  "quality_status": "available",
  "content_hash": "..."
}
```

跨 Run 缓存命中时，系统必须先把兼容的缓存 Artifact 重新挂载到当前 Run 的 Board，
再将其作为当前 Graph 的输入；只传递 `available_artifact_types` 不能证明消费了哪一
份事实。挂载操作必须记录来源 Run、缓存键、输入指纹和当前 Run 的父引用。无法解析
到具体引用时，Planner 必须重新生成采集节点，而不是按类型名假定缓存可用。

一个方法需要多个 Snapshot 时，Capability contract 必须声明维度、scope、质量策略
和最小字段；实现可以消费一个由 Runner 组装的受治理 `snapshot_set`，但不能恢复为
无来源的进程内 raw dict。`stock_snapshot_bundle` 只能作为这个集合的派生索引。

### 5.3 完整来源清单口径

迁移状态必须按“采集模块、原始 Profile 维度、派生方法”三层记录，不能用一个数字
代表三者。当前基线的 22 个 `fetch_*.py` 模块、20 个 `_ALL_FETCHERS` 维度和
派生的估值/工作流/Deep 结果必须逐项映射。特别是以下辅助模块必须单独登记，不能
被笼统归入 `data_gap`：

| 来源模块 | 归类 | 目标状态 |
|---|---|---|
| `fetch_fund_holders` | 辅助持仓证据 | 独立 Snapshot 或明确不纳入 Lite |
| `fetch_similar_stocks` | 辅助相似标的 | Peer/Screen 能力或明确缺口 |
| `fetch_trap_signals` | 风险原始信号 | `risk_scan` 的来源引用 |
| `fetch_macro` / `fetch_policy` / `fetch_industry` | 原始 Profile 维度 | 独立 Snapshot |
| `fetch_capital_flow` / `fetch_moat` | 原始 Profile 维度 | 独立 Snapshot |

每一项都必须有 provider、字段映射、质量策略、迁移状态和结果等价等级；“已注册但
只能输出 `data_gap`”不计入已迁移覆盖率。

## 5.4 UZI 基线与逐项映射文件

UZI 基线必须同时记录源仓库 URL、commit、版本文件 hash、许可证文件 hash 和脱敏 replay
fixture hash。`D:\Cognitrade\_tmp_uzi_skill` 只是本机工作副本，不能作为唯一的
可复现来源。完整映射必须落在仓库内的机器可读文件中，至少覆盖全部 22 个采集模块、
20 个 Profile 原始维度、66 个注册 Panel 成员和所有派生方法，并为每项记录：

```text
source_id / source_path / source_commit
target_skill_or_capability / target_artifact_type
field_mapping / provider / quality_policy
migration_status / result_equivalence / baseline_fixture
```

`migration_status` 使用受控值：`planned`、`pack_native`、`pack_native_with_provider_gap`、
`equivalent`、`not_equivalent`。其中 `pack_native` 只表示实现已进入 Pack，不能替代
`result_equivalence`；没有字段映射、来源策略或 replay 基线的条目必须保持为
`planned` 或 `pack_native_with_provider_gap`。Panel 映射还必须逐项记录注册成员、
参与 Profile、规则版本和跳过条件，不能只记录参与人数。

没有逐项映射、字段映射和 replay 证据的能力只能标记为 `planned` 或 `data_gap`，
不得计入迁移覆盖率。

映射文件的 source 级 `replay_fixture_sha256` 必须按 Fixture 路径索引所有基线文件；
契约测试必须重新计算仓库内 Fixture 的 SHA-256，并验证每个 mapping entry 的
`baseline_fixture` 都能解析到对应 hash。源仓库本身不随 Cognitrade 一起打包时，版本
文件和许可证 hash 作为外部来源指纹保存，不能假定本地临时目录存在。

该映射文件必须通过独立 Schema/契约测试；缺少任一字段、Panel 成员明细或基线
Fixture 的条目只能标记为 `planned`，不能使用 `pack_native` 代表已完成迁移。

## 6. 目标 Artifact 契约

### 6.1 `valuation_model`

```json
{
  "artifact_type": "valuation_model",
  "schema_version": "1",
  "methodology_version": "3.9.1-adapted",
  "profile": "lite",
  "primary_symbol": "300750.SZ",
  "methodologies": ["dcf", "comps"],
  "models": [
    {
      "model": "dcf",
      "symbol": "300750.SZ",
      "fair_value": 420.0,
      "market_price": 348.76,
      "margin_of_safety": 0.204,
      "assumptions": {"wacc": 0.085, "terminal_growth": 0.025, "forecast_years": 5},
      "sensitivity": [],
      "confidence": 0.61,
      "risk_flags": ["cashflow_assumption_driven"]
    }
  ]
}
```

### 6.2 `investor_panel_review`

```json
{
  "artifact_type": "investor_panel_review",
  "schema_version": "1",
  "methodology_version": "3.9.1-adapted",
  "profile": "lite",
  "scope": "lite",
  "panel_version": "uzi-adapted-1",
  "votes": [
    {
      "investor_id": "buffett",
      "school": "value",
      "signal": "watch",
      "score": 62,
      "reasoning": "引用具体财务和估值指标",
      "source_refs": []
    }
  ],
  "distribution": {
    "strong_buy": 1,
    "buy": 3,
    "watch": 4,
    "avoid": 2
  },
  "disagreement": {
    "exists": true,
    "summary": "估值与成长假设存在显著分歧"
  },
  "confidence": 0.58,
  "risk_flags": []
}
```

### 6.3 `risk_scan`

```json
{
  "artifact_type": "risk_scan",
  "schema_version": "1",
  "methodology_version": "3.9.1-adapted",
  "profile": "lite",
  "symbol": "300750.SZ",
  "risk_level": "attention",
  "signals_hit_count": 1,
  "signals": [
    {
      "signal_id": "promotion_disclosure_gap",
      "severity": "medium",
      "finding": "具体风险描述",
      "evidence_refs": []
    }
  ],
  "recommendation": "先核验来源和公告，不直接依据社交热度决策",
  "confidence": 0.55,
  "risk_flags": []
}
```

### 6.4 `report_presentation`

`report_presentation` 是语义报告到视觉报告之间的稳定契约。前端和 HTML Exporter 都读取它，不从 Markdown 或 HTML 反向提取数据。

```json
{
  "artifact_type": "report_presentation",
  "schema_version": "1",
  "theme": "uzi_terminal_light",
  "profile": "lite",
  "hero": {
    "symbol": "300750.SZ",
    "name": "宁德时代",
    "price": 348.76,
    "score": 61,
    "verdict": "观望优先",
    "safety": "attention"
  },
  "core_conclusion": "引用具体数字的核心结论",
  "dashboard": {
    "trend": {},
    "price": {},
    "volume": {},
    "chips": {},
    "news": {},
    "risks": [],
    "catalysts": []
  },
  "great_divide": {
    "bull": {},
    "bear": {},
    "punchline": "有证据的多空分歧"
  },
  "panel": {
    "tabs": [],
    "votes": []
  },
  "radar": [],
  "risk_list": [],
  "buy_zones": [],
  "source_artifact_ids": [],
  "generated_at": "2026-07-12T00:00:00Z",
  "data_gap": []
}
```

Presentation Artifact 不得添加语义数据或补全缺失事实。没有数据时显示 `data_gap`，并引用对应风险或来源状态。

### 6.5 Artifact envelope 与 content 的边界

上面的 JSON 示例展示的是 `ArtifactRecord` 的完整语义，不代表所有字段都写入
`content`。Agent OS 负责维护不可由 Pack 修改的 envelope：

```text
artifact_id
artifact_type
schema_version
artifact_version
run_id / thread_id / request_id
producer
created_at
content_hash
 lifecycle_status / supersedes_artifact_id
```

Pack 只负责 `content` 以及与内容直接相关的 `source_refs`、`parent_artifact_ids`、
`confidence` 和 `risk_flags`。正式 Schema 必须存放在
`domain_packs/finance_ashare/artifact_schemas/<artifact_type>.json`，并同时注册到
`domain.yaml.artifact_types`。所有 Capability 的 `produces` 必须引用已注册的
Artifact 类型和版本；Presentation 只能读取 active Artifact，不能把事实写回其他
Artifact。

Artifact 的生命周期状态和内容质量状态必须分离：

```text
lifecycle_status: active / superseded / needs_review / rejected
quality_status:   available / degraded / data_gap / assumption_only
```

`active` 不等于数据可用，`available` 也不等于所有方法字段齐全。质量状态应写入
统一的内容契约字段，不能只依赖某个 Pack 自定义的 `degraded` 布尔值。Bundle 的
质量是由其子 Snapshot 按方法需求计算出的索引结果，不得覆盖子 Snapshot 的质量。

### 6.5.1 正式 Schema、输入字段和质量校验

本节中的 JSON 片段只用于解释语义；正式契约以
`domain_packs/finance_ashare/artifact_schemas/*.json` 为准，示例字段不得与正式
Schema 产生第二套命名。每个正式 Schema 必须在契约测试中验证必填字段、类型、单位、
来源引用和质量状态。特别是：

- `valuation_model` 必须保留 `methodology_version`、`profile`、`models`、
  `primary_symbol` 和 `methodologies`，模型明细中的公允价值、假设和敏感性表属于
  `models[]` 的内容；
- `risk_scan` 必须保留 `signals_hit_count`、`signals`、`recommendation`、
  `confidence` 和 `risk_flags`；
- `report_presentation` 必须保留 `profile` 和 `data_gap`，且不能把缺失数据伪装成
  空的正常图表；
- 所有 `snapshot` 的生产内容必须使用 `snapshot_type`，并包含 `scope`、
  `quality_status` 和 `facts`。

`MethodDefinition.required_inputs` 不再是无法验证的字符串清单。每个方法必须声明
可解析的字段要求：

```yaml
required_inputs:
  - artifact_type: snapshot
    selector: {snapshot_type: fundamental, scope: symbol}
    required_fields:
      - path: facts.free_cash_flow
        type: number
        unit: CNY_100M
        period: latest_available
    allowed_quality_statuses: [available]
```

Planner 负责按 Artifact 引用、scope、period 和质量状态选择输入；Runner/Quality
Gate 负责按 `required_fields` 校验字段存在、类型、单位和来源。若只有代理字段，必须
将方法结果标记为 `assumption_only`；若没有可验证字段，必须是 `data_gap`，不能把
节点标记为成功事实产出，也不能通过结果等价验收。

### 6.6 `research_profile` 契约

`quick`、`lite`、`medium` 和 `deep` 是规划约束，不是预先写死的节点列表。
请求通过 `constraints.research_profile` 传入用户约束，Main Agent 可以根据目标、
预算、数据新鲜度和风险把它升级或降级，但必须在 Graph metadata 和事件中记录决定。

```json
{
  "requested": "lite",
  "selected": "lite",
  "selection_reason": "用户要求快速个股体检",
  "max_duration_seconds": 300,
  "max_cost_class": "medium",
  "required_deliverables": ["research_report", "main_agent_synthesis"],
  "minimum_evidence": {"source_refs": 1, "confidence": 0.0},
  "allowed_capability_tags": ["profile:lite"],
  "stop_conditions": ["minimum_deliverables_ready", "hard_deadline"]
}
```

Profile 只规定预算、允许的能力类别、最低产物和 Gate；最终节点、依赖和并发必须
由当前请求、Pack capability catalog、Board 状态和数据缺口动态生成。Profile 不能
只作为写入 Graph metadata 的标签，Planner 必须将它转换为可验证的 required outputs
和 capability 选择约束。用户明确要求某个方法时，该方法的 capability 可以成为
required deliverable，而不能被 Profile 的默认列表静默跳过。

`main_agent_synthesis` 是 Graph 完成后的 Core Agent Loop 交付产物，不是 Pack
Subagent 的替代输出。它是唯一正式的主 Agent 合成 Artifact；没有该 Artifact 时，运行
不能以“最终回答已就绪”结束；模型不可用时只能进入显式的
`synthesis_unavailable` 部分交付状态。

部分交付采用唯一的运行状态语义：

```text
completed       必需产物、阻塞 Gate 和 main_agent_synthesis 均完成
completed_partial 已满足最低交付 Gate，剩余可选节点已取消或明确延期
running         仍有节点在后台执行，尚未形成最终或最低交付
gated           被阻塞 Gate/审批阻止，不能宣称已交付
```

`synthesis_unavailable` 是交付状态，不是把 Run 伪装成 `completed` 的附加标签。若
最低交付已经形成但最终合成失败，Run 必须为 `completed_partial`；若后台仍会继续
尝试合成，Run 保持 `running`，并通过恢复/重试事件完成状态转换。API、SSE、Chat、
Workbench 和恢复逻辑必须使用同一套状态，不得各自解释。

Run 状态和交付状态是两个不同字段，必须固定为：

```text
run.status = completed_partial
delivery.status = synthesis_unavailable
```

`main_agent_progress` 是可回放事件，不是 Pack Artifact；`assistant_message` 是 Core
交付记录，必须引用 `main_agent_synthesis` 或明确的部分交付 Artifact。方案、API、SSE
和 Chat 不得再使用未注册的 `research_synthesis` 作为第三种合成产物名称。

`synthesis_unavailable` 不是 `RunStatus`，不能加入 Run 状态转换表。若系统仍在后台
重试合成，Run 保持 `running`，交付状态为 `synthesis_unavailable`，直到受治理重试
成功或按部分交付规则终止。

输出选择采用以下优先级：用户明确指定的 `required_artifact_types` 或
`requested_methods` 覆盖 Profile 默认产物；没有明确指定时才使用 Profile 默认产物；
Main Agent 的聊天交付始终独立于 Pack `research_report`。因此“只做 DCF”可以只生成
`valuation_model` 加 `main_agent_synthesis`，而不会因为 Finance Pack 的默认完整报告
要求被迫创建 Panel、Risk 或 Report Composer。

三个 Finance Pack 专用 intent 不直接套用“完整个股体检”的默认产物集合，而是使用
独立的 intent Profile 约束：

| intent | 默认 Profile | 允许升级 | 必需专业产物 | 最低交付 |
|---|---|---|---|---|
| `stock_screen` | `quick` | `medium` | `screening_result` | 候选、筛选依据、缺口和 `main_agent_synthesis` |
| `sector_research` | `lite` | `medium` / `deep` | `sector_analysis` | 板块证据、热力图/组成、风险和 `main_agent_synthesis` |
| `review_thesis` | `quick` | `medium` | `thesis_review` | 原判断对照、证据变化、风险和 `main_agent_synthesis` |

专用 intent 的 Profile 默认提供成本、延迟、并发和硬截止约束，但不自动加入
`valuation_model`、Panel 或完整 `report_presentation`；只有用户明确请求完整研究交付
或方法时，Planner 才追加这些产物。该规则必须在 Pack manifest 中以
`intent_profiles`、`profile_output_intents` 和 `intent_outputs` 表达，并写入 Graph
metadata 与 Plan decision。

### 6.6.1 输出解析优先级

Planner 只能按以下顺序解析输出，不能让多个 manifest 字段隐式叠加：

1. 用户明确的 `required_artifact_types` 或 `requested_methods`；
2. 当前 intent 的必需输出；
3. 当前 `research_profile` 的默认输出；
4. Pack report contract 只作为 Gate 和完整报告请求的约束，不能把 `research_report`
   强行加入一个明确的单方法请求。

`requested_methods=["dcf"]` 的结果只能包含 `valuation_model`、必要 Snapshot、质量
Gate 和 `main_agent_synthesis`；完整 `research_report`、Panel、Risk 和 Presentation
只有在用户请求完整研究交付或 Profile 默认输出仍适用时才加入。输出来源、缩窄原因和
最终 required outputs 必须写入 Graph metadata、Plan decision 和事件。

### 6.7 Capability contract

每个迁移后的 UZI 能力必须在 Pack manifest 中声明并通过契约测试：

```text
capability / capability_version
subagent_type / implementation
consumes / produces / selector
tools / permissions / side_effect_level
parallel_safe / max_concurrency
cost_class / latency_class / estimated_tokens / estimated_cost
reliability / requires_llm / failure_mode / fallback_capabilities
replayable / idempotency_key_template
freshness_ttl_seconds / data_period_policy
methodology_version / policy_version
```

`freshness_ttl_seconds`、方法论版本和输入 Artifact 指纹必须参与缓存判断；超时、
取消、重试、fallback 和部分成功必须通过 Agent OS Runner 记录，Pack 实现不得自行
创建绕过 Tool Registry 的线程或网络请求。

职责边界固定为：Main Agent 是唯一主 Agent；PlanAgent 只是 Main Agent 在 Graph Mode
中的内部规划角色，不是第二个自主 Agent；CapabilityPlanner 是无独立对话状态的确定性
图编译器/受治理工具；Runner 只负责调度、执行和状态持久化。任何模型提出的图 Patch
都必须先经过 CapabilityPlanner 和 Governance 校验。

Pack manifest 中的 `candidate_capabilities` 只是候选能力和规划先验，不是固定执行
顺序。职责必须拆分为：Main Agent 作为唯一面向用户的主 Agent，负责 Core Loop、
是否进入 Graph Mode 和最终交付；PlanAgent 是 Main Agent 内部唯一可以提出图决策的
规划角色；CapabilityPlanner 是受治理的确定性编译器，负责把已注册的 intent、method
和 capability contract 编译为合法 Graph；Runner 只负责调度和执行。模型可以提出
新的图 Patch，但不能直接创建未注册的 Capability、Artifact 类型、Tool 或权限。
所有 Patch 都必须经过 Schema、依赖无环、预算、权限、最大节点数和 Quality Gate
校验后才能在安全点应用。

CapabilityPlanner 必须在候选能力之间执行可解释选择，至少综合：必要输入是否满足、
Artifact 新鲜度和质量、权限、成本、延迟、可靠性、并发能力、方法论版本和 fallback
关系。每次选择都要在 Plan decision 中保存候选集、淘汰原因、最终能力和评分；能力
列表顺序不能隐式决定最终节点。必须有测试证明改变 Board 状态、预算或来源健康度
会得到不同的图。

### 6.8 方法目录与请求契约

所有用户可请求的方法必须由 DomainPack 声明 `method_id`、版本、必要输入、允许
的质量状态、输出 Artifact、对应 capability 和结果等价规则。该声明必须通过统一的
`MethodDefinition` Schema 解析，Planner 在选图前校验 required inputs 和允许的
quality status，不能只把 `method_catalog` 当作任意 YAML 元数据。Core API 只校验通用
形状，不能在 Core 中硬编码 Finance 方法名或 Snapshot 维度名；不存在 method contract
的请求必须被拒绝或转入澄清，不能返回“已支持”但只生成 `data_gap`。Finance Pack 至少
登记：

```text
dcf / comps / cashflow_resilience
three_statement / lbo / ma_accretion / segmental
panel / risk / report
```

没有注册 capability、Artifact Schema 和输入契约的方法不得被 API 接受为“已支持”；
只返回 `data_gap` 的维度也只能标记为能力缺口，不能计入迁移覆盖率。

### 6.9 数据可用性与字段映射契约

Snapshot 的 `quality_status` 只描述该事实 Artifact 自身的来源、时效和数据完整性，
不能随消费它的方法而改变。方法是否具备所需字段必须由方法输入校验单独记录在节点
metadata 和方法产物中。Planner 和方法论 Subagent 必须区分：

```text
available       Snapshot 来源、Schema、时效和自身完整性满足声明
degraded        Snapshot 有来源，但质量、时效或完整性低于默认要求
data_gap        Snapshot 没有可验证来源或缺少事实字段
assumption_only Snapshot 只能支持明确标注假设的代理输入
```

方法级输入状态另行使用 `satisfied`、`degraded`、`data_gap` 和 `assumption_only`。
例如，同一个 `fundamental` Snapshot 可以对一个只要求营收的方法为
`method_input_status=satisfied`，对要求完整现金流的方法为 `method_input_status=data_gap`；
不能通过修改 Snapshot 的全局 `quality_status` 来表达这种差异。

至少建立以下字段映射，并在缺失时阻止“等价迁移”标记：

| 方法 | 必要输入 | 允许的代理 | 结果状态要求 |
|---|---|---|---|
| DCF | FCF、现金、总债务、股本、预测假设 | 代理 FCF | `assumption_only`，不得伪装为完整 DCF |
| 三表 | 营收、利润率、营运资本、Capex、资产负债表基期 | 保守假设 | 明确记录每项假设 |
| LBO | EBITDA、净债务、入口/退出倍数 | EBITDA 代理 | `assumption_only` |
| 并购增厚 | EPS、股本、溢价、协同 | EPS 由净利润/股本推导 | 记录推导链 |
| Segmental | 已披露分部收入及增速 | 不允许凭空补分部 | 缺失即 `data_gap` |

`data_gap` 不得增加 coverage，也不得满足需要真实来源的 Quality Gate。所有来源
字段必须同时保留 provider、data period、source refs、fallback 状态和单位。
方法论输入必须使用 capability 自己声明的质量策略；不能通过一个全局
`allow_degraded_artifacts` 开关让所有方法无条件接受降级事实。

### 6.10 结果等价等级

每个迁移方法的 `result_equivalence` 必须声明以下等级之一：

| 等级 | 适用场景 | 完成条件 |
|---|---|---|
| `exact` | 纯规则、枚举和结构化字段 | 固定输入下结构化结果完全一致 |
| `numeric_tolerance` | DCF、Comps、财务模型 | 公式、单位、舍入和数值容差逐项一致 |
| `semantic` | 风险信号、Panel 解释、研究摘要 | 结论类别、证据引用、缺失状态和风险等级一致 |
| `not_equivalent` | 仅有代理或来源不可复现 | 只能标记迁移能力，不得计入等价完成 |

每个方法还必须登记 baseline fixture、输入 hash、`as_of`、单位转换、舍入规则、容差
和允许的差异。`assumption_only`、`data_gap` 或外部来源缺失不能通过 UZI 等价验收，
只能作为显式降级结果。

### 6.11 方法级 Quality Gate

Quality Gate 不能只挂在 `research_report` 上。每个可单独请求的专业产物都必须有
对应的 Gate，至少包括：

```text
snapshot_quality_gate       Snapshot 字段、来源、期间和质量状态
valuation_input_gate        DCF/Comps 等模型的必需字段和假设完整性
risk_evidence_gate          风险信号的来源和缺口语义
panel_lineage_gate           Panel 规则版本、参与数量和证据链
screening_quality_gate       候选池、筛选规则、排名依据和数据缺口
sector_analysis_gate         板块组成、热力图来源、政策/催化证据和缺口
thesis_review_gate           被复核判断、逐项证据对照、反证和风险
research_report_gate        完整报告的来源、风险和免责声明
delivery_synthesis_gate     Main Agent synthesis 与 assistant message
```

用户只请求 DCF、风险扫描或 Panel 时，也必须执行对应方法级 Gate；不能因为没有
`research_report` 就跳过治理。用户请求筛选、板块研究或 Thesis Review 时，专用
Artifact 的 Gate 也是阻塞 Gate；不能只依靠最终 `research_report` 的 Gate。`data_gap` 可以通过 Gate 生成“缺口交付”，但不能
通过“事实结论交付”或增加迁移 coverage。完整报告还必须要求
`report_presentation` 与语义报告引用同一组 active Artifact。

`delivery_synthesis_gate` 属于 Agent OS Core 的交付治理；其余专业 Gate 由 Finance
Pack manifest 注册。Gate 结果必须同时写入 Run snapshot、事件和最终交付状态。

## 7. 动态 Graph 设计

### 7.1 Core Loop

普通问题、解释报告、询问“刚刚研究了什么”等不进入 Finance Pack，不创建完整 Graph，由 Main Agent 直接处理或调用 Core Tool。

### 7.2 Lite Graph

Lite 不是一条固定硬编码流程，而是 Planner 在预算约束下从能力目录选择的最小可交付图。
下图只是“完整个股体检”请求的规划示例，不是固定节点清单；方法目标、Board 状态、
数据健康度和预算可以删除、替换或追加其中任意节点：

```text
query_normalize
  ↓
parallel:
  market_snapshot
  fundamental_snapshot
  technical_snapshot
  valuation_snapshot
  governance_snapshot
  events_snapshot
  lhb_snapshot
  ↓
valuation_dcf ───────┐
panel_lite ───────────┼─→ quality_gate → analysis_synthesis → report_compose
risk_scan ────────────┘
```

Lite 的固定部分是治理预算和最低交付要求，不是节点名称顺序。Profile selector
必须将“完整体检”“只做 DCF”“只做风险扫描”等任务转换为不同的 required
deliverables；若输入只要求 DCF 或只要求风险扫描，Planner 应生成不包含无关
Panel/Report 节点的更小子图。只有在用户要求完整研究交付时，才选择同时消费
valuation、panel 和 risk 的完整报告 Composer。

### 7.3 Medium Graph

Medium 在 Lite 的基础上按数据缺口和用户目标增加同行、产业链、研究、舆情、政策等能力，并按流派或方法论并发展开。没有证据价值的维度不得因为“完整报告”名义强行抓取。

### 7.4 Deep Graph

Deep 才启用高成本能力：

- 多流派 Panel Compound Node；
- Bull/Bear 结构化辩论；
- Segmental Revenue Build-Up；
- 多轮定性研究；
- 更严格的来源和质量 Gate；
- 人工审批或交付前复核。

### 7.5 缓存命中后的重规划

Planner 每次读取 Board 的 Artifact freshness、quality、source_refs 和 risk_flags：

- 有效且未过期的历史 Artifact 直接复用；
- 市场价格等 volatile Artifact 按 TTL 刷新；
- 缺失或降级 Artifact 触发对应 Subagent；
- 用户切换深度时只扩展缺失能力，不重复执行有效节点；
- 数据过期但预算不足时输出降级报告并记录原因；
- `--no-resume` 等价操作只能通过明确的 Run constraint 触发，不能由普通前端请求隐式触发。

缓存判断发生在 Graph 物化前和每个安全点，而不是只发生在请求开始时。Planner 必须能够根据新的 Board 状态：

- 复用已验证的快照并跳过对应采集节点；
- 仅刷新过期的 volatile Artifact；
- 在结果缺少 DCF、Panel 或风险证据时追加相应子图；
- 在某个节点失败后切换兼容的 fallback capability 或局部重跑；
- 在达到最低交付 Gate 后结束运行，而不是为了填满报告模板继续抓取无证据价值的数据。

Planner 判断 Artifact 是否可用时，必须使用完整可用性谓词，而不是只检查
`artifact_type`：

```text
active
and schema_version compatible
and not expired
and quality not degraded (unless the request explicitly allows degraded input)
and source scope/provider compatible
and required fields present
```

过期、降级、被 supersede 或缺少必要字段的 Artifact 只能作为重规划输入，不能直接
满足同类型 required output。`selected_profile`、`selection_reason` 和实际降级原因
必须同时写入 Graph metadata、Plan decision 和运行事件。

### 7.6 Main Agent 最终交付

Pack 的 `research_report`、方法论 Artifact 和风险 Artifact 是结构化事实，不是
最终聊天回答。Graph 完成或达到最低交付 Gate 后，固定使用以下交付顺序：

```text
Pack ArtifactBoard facts
  -> Main Agent synthesis
  -> main_agent_synthesis Artifact
  -> assistant_message Artifact
  -> assistant_message_ready SSE event
  -> API Response / Chat message
```

Main Agent synthesis 必须读取当前 Board 的 active Artifact、来源、风险和数据缺口，
以中文生成结论、解释和后续研究动作；不得使用固定行业句型替代事实，也不得把 Pack
原始摘要作为最终回答。SSE 在 `assistant_message_ready` 持久化前不能宣告运行完成；
模型不可用时必须显式标记 `synthesis_unavailable`，不能伪装成动态结论。

### 7.7 Main Agent 过程进度

Main Agent 不只在 Graph 结束时生成回答。每个 Tool、Subagent 或安全点重规划完成后，
Core Loop 应读取最新 Board、已完成节点、失败/降级状态和下一执行前沿，生成并持久化
一条中文 `main_agent_progress` 更新：

```text
completed: 当前完成的节点及实际产出
finding: 当前发现或数据缺口
next_action: 下一步准备执行的能力或重规划动作
graph_revision / artifact_ids / source_refs
```

这是对运行状态的可审计摘要，不是暴露模型内部思维链。原始 Tool 事件可以很多，但
公开的模型摘要必须按“新执行前沿、Subagent 结果、Graph Patch、Gate/交付状态”合并，
不能为每个低层事件无条件触发一次模型调用。每个 Run 的 progress 模型调用必须受独立
的次数、Token、最小间隔和超时预算约束；达到限制、模型失败或输出不符合 Schema 时，
立即使用基于真实事件的结构化 fallback。该策略必须记录 `progress_source`、合并的事件
范围和预算消耗。

这是对运行状态的可审计摘要，不是暴露模型内部思维链。该更新通过 SSE 和 Chat
Thread 复用，刷新页面后仍能从持久化事件恢复；没有模型可用时可退回结构化状态摘要，
但不能伪造未发生的发现或结论。最终 `assistant_message_ready` 只能引用已经持久化
的 `main_agent_synthesis` 和 Board Artifact。

### 7.8 Graph Patch 与硬截止协议

PlanAgent 对当前 Graph 的任何新增、删除、替换、重连或局部重跑都必须提交版本化
`GraphPatch`，至少包含：

```text
base_graph_revision
reason / trigger
operations
affected_node_ids
invalidated_artifact_ids
requested_capabilities
governance_budget_delta
```

Runner 只在安全点校验并应用 Patch。校验失败时保留原 Graph，记录拒绝原因，并由
Main Agent 选择继续、澄清、降级交付或结束；不能让模型直接修改运行状态。Patch
应用后必须递增 `graph_revision`，将受影响旧 Artifact 标记为 `superseded` 或
`needs_review`，并重新计算 ready frontier。

Profile 的 `max_duration_seconds` 是硬截止，不是建议值。达到截止时只能执行两种
受治理动作：取消剩余可选节点并交付 `completed_partial`，或先交付最低成果并保持
Run 为 `running` 继续后台执行。不能同时把 Run 标记为 `completed` 又声称还有节点
在运行；两种动作都必须通过事件、快照和恢复接口可回放。

## 8. UZI Cache 到 ArtifactBoard 的迁移

### 8.1 迁移期兼容

可以提供 `UziCacheImportAdapter`：

1. 读取 UZI `.cache/<ticker>/raw_data.json`；
2. 校验 ticker、market、fetched_at 和维度质量；
3. 将每个有效维度转换为对应 Snapshot Artifact；
4. 为 Artifact 增加 `source_refs`，标记 `source_system: uzi_cache`；
5. 将旧 JSON 的缺失、fallback、timeout 转换为 `risk_flags` 和 `data_quality`；
6. 不把未验证的 UZI synthesis 或 HTML 文案直接写入长期 Memory。

### 8.2 生产期目标

生产运行只写 Agent OS RunStore、ArtifactBoard 和统一缓存层。UZI cache 只作为一次性导入源或开发调试源，不作为正常运行的事实数据库。统一缓存层必须支持跨 Run 的有效 Artifact/节点输出复用，并由 Runner 根据 TTL、数据周期、来源质量和输入指纹决定命中或失效；`cache_scope: run` 只能用于明确声明不可跨 Run 复用的临时结果。

### 8.3 Cache Key

缓存键至少包含：

```text
domain_id
symbol
artifact_type
snapshot_type
provider
schema_version
data_period
profile
capability_version
methodology_version
policy_version
input_artifact_fingerprint
assumption_fingerprint
universe_or_scope
```

这些字段不是建议字段，而是缓存实现的最小契约。对 Snapshot，`artifact_type` 和
`snapshot_type` 都必须参与键计算；对方法论产物，`artifact_type` 必须参与键计算。
迁移输入中的 `dimension` 必须在进入缓存键计算前规范化为 `snapshot_type`，不能产生
第三个正式字段名。
`provider`、`assumption_fingerprint`
和 `universe_or_scope` 不能仅依赖未定义的 `content_hash` 间接覆盖；缓存层必须生成
规范化的 fingerprint，并在命中事件中保存命中前后的指纹。无法证明兼容时必须拒绝命中。

基础行情等事实快照可以在 Lite 和 Medium 之间共享，但 DCF、Panel、风险结论和
报告必须同时验证 Profile、方法论版本、假设和输入 Artifact 指纹；不兼容时只能
重新规划，不能静默复用。

缓存分为两个明确层次：Pack 内部可以保留 Provider 原始响应缓存，用于减少外部
数据源请求；Agent OS 的 Artifact cache 才是跨 Run 的事实复用层。Provider 原始缓存
不能直接满足 Graph 输入，也不能绕过 ArtifactBoard、来源挂载、质量校验和审计。所有
跨 Run 复用必须由 Runner 将兼容 Artifact 重新挂载到当前 Run 的 Board；如果两层缓存
的 TTL、版本或质量语义冲突，以 Artifact cache 和 Agent OS Governance 为准。

## 9. API 与前端接入

### 9.1 后端 API

当前已实现的 Agent OS API 保持唯一入口：

```text
POST /api/v1/chat/threads/{thread_id}/runs
POST /api/v1/runs
GET  /api/v1/runs
GET  /api/v1/runs/{run_id}/replay
GET  /api/v1/runs/{run_id}/events
GET  /api/v1/runs/{run_id}/events/stream
GET  /api/v1/runs/{run_id}/steps
GET  /api/v1/runs/{run_id}/metrics
GET  /api/v1/runs/{run_id}/trace
GET  /api/v1/runs/{run_id}/recovery
```

`GET /api/v1/runs/{run_id}` 和 `GET /api/v1/runs/{run_id}/artifacts` 当前已经实现，
属于 Agent OS 通用契约。Artifact 查询必须继续遵守 active/superseded 过滤和身份
范围；需要分页时扩展通用接口，不新增“UZI 专属运行 API”。报告导出操作只读取已
完成的 `research_report` 和 `report_presentation` Artifact，并记录 run_id、
graph_revision、Artifact id 和版本。

当前仍存在的 `/api/runs`、`/api/runs/{run_id}/*` 和 `/api/domain-packs` 是迁移期
兼容/工作台接口，不属于新的规范入口。前端、Chat、CLI、后台恢复和测试必须统一使用
`/api/v1`；兼容路由必须有调用计数、静态引用检查和独立回归集，待调用归零后删除或
明确保留为只读兼容层。方案验收不能把“`/api/v1` 已存在”表述为“旧路由已经消失”。

### 9.2 Workbench 首屏

前端报告视图按以下顺序渲染：

1. Hero：标的、价格、评分、定调、安全等级；
2. Core Conclusion：一句话结论和证据摘要；
3. Dashboard：趋势、估值、量能、筹码、新闻、风险、催化剂；
4. Methodology：DCF、Comps、现金流韧性等可追溯方法论表；
5. Great Divide：有真实证据支撑的多空分歧；
6. Panel：按流派或深度档位展示评审；
7. Radar：只展示已产生的维度及数据质量；
8. Risks/Next Steps：具体风险、来源和下一步动作；
9. Audit：Graph、Subagent、Artifact、来源和事件。

前端不得为了填满 UZI 的视觉布局而制造中性填充值、假评委、假雷达或没有来源的买入区间。

### 9.3 SSE

SSE 继续从 Agent OS `execution_events.jsonl` 和运行状态读取事件。事件可增加：

- `presentation_section_ready`；
- `panel_school_started`；
- `methodology_artifact_committed`；
- `report_quality_gate_completed`。

事件只描述状态和 Artifact 引用，不发送未经持久化的临时分析结论作为唯一事实。

## 10. 分阶段实施计划

### Phase 0：能力盘点和运行隔离

- 锁定 UZI 版本、许可证和来源；
- 记录 fetcher、方法论、Panel、renderer 的输入输出；
- 禁止生产代码引用 `D:\Cognitrade\_tmp_uzi_skill`；
- 明确 UZI CLI 只作为迁移期手工验证入口；
- 增加 UZI 版本和 adapter 版本到来源元数据。

交付物：能力映射表、依赖清单、许可证记录、迁移期 Adapter 设计。

Phase 0 的退出门槛还包括：维度/采集模块计数口径已固定、字段映射已登记、
`available/degraded/data_gap/assumption_only` 语义已有契约测试，且不存在把降级输入
误报为 available 的覆盖逻辑；Snapshot/Bundle 的事实模型、生命周期/质量状态字段、
正式 Schema 与方法级输入字段契约、Quality Gate 绑定关系、Profile 命名和硬截止状态机
已经定案。结果等价测试必须使用固定的 Provider replay
fixture 或脱敏数据快照，并记录 `as_of`、输入 hash、单位转换、舍入规则和数值容差，
不能直接依赖实时网络结果。

### Phase 1：UZI Lite 语义能力

- 先完成 `research_profile`、Capability contract 和 Artifact envelope/content 契约；
- 将 7 个核心维度映射到 Finance Pack Skills，并完成独立 Snapshot Artifact 与派生 Bundle 索引；若维度需要独立并发或独立降级，必须拆成独立 Capability，否则明确由 Bundle 内部并发；
- 迁移 DCF 和基础评分；
- 在 Pack manifest 注册方法目录和方法输入契约，Core API 不再维护 Finance 专属方法白名单；
- 增加 `valuation_model`、`risk_scan`、`investor_panel_review` Schema；
- 为 DCF、Comps、Panel、Risk 和完整报告注册方法级 Quality Gate；
- Lite Panel 使用 10 位代表性评审；
- 实现 freshness、timeout、fallback 和 source_refs；
- 建立 Lite Graph 的端到端测试。

交付标准：不调用 UZI CLI，也能从 Agent OS 生成可审计的 Lite `research_report`；
Lite 的真实来源、10 位 Panel 参与规则和 DCF 结果均通过固定 commit 加 Provider
replay fixture 的结果基线验证；每个维度的质量状态、TTL、失败和局部重跑均可回放。

### Phase 2：UZI 风格 Presentation View

- 增加 `report_presentation` Artifact；
- Cognitrade Workbench 实现 Hero、Dashboard、Methodology、Risk 和 Panel 视图；
- 为 `stock_screen`、`sector_research` 和 `review_thesis` 分别完成
  `screening_result`、`sector_analysis` 和 `thesis_review` 的真实 Artifact 交付链；
- 为三个专用 Artifact 注册阻塞 Quality Gate，并为每个 intent 注册默认 Profile、
  可升级 Profile、预算和硬截止策略；
- Overview 候选列表、板块图、Methodology 和 Screening 视图只能读取对应的
  Agent OS Artifact，不能继续使用静态演示数据；
- 为三个 intent 分别建立输入解析、并发采集、专业分析、报告合成和结果等价的端到端测试；
- 使用真实 Artifact 数据，不直接嵌入 UZI HTML；
- 支持缺失数据和降级状态；
- 完成桌面和移动端截图回归。

交付标准：Cognitrade 前端能展示 UZI 的主要信息层级以及三个 Finance Pack 专用
intent 的真实结果，数据来源完全来自 Agent OS；任何静态 fallback 都只能用于空状态，
不能作为候选、板块、方法论或筛选结果。

### Phase 3：Cache 和中断恢复

- 实现 UZI cache 一次性导入 Adapter；
- 将有效维度转换为 Board Artifact；
- 为实时数据、季度数据和低频研究数据设置不同 TTL；
- 在统一缓存记录中保存 `created_at`、`expires_at`、Profile、方法论版本、来源质量
  和输入 Artifact 指纹，由 Runner 在命中前执行 TTL/质量校验；
- 支持 Run 中断后从 Artifact 和节点状态恢复；
- 验证 Lite → Medium 增量扩展不会重复抓取有效数据。

交付标准：同一标的第二次 Lite 运行只刷新 volatile 数据和过期 Artifact，并且运行可审计。

### Phase 4：Medium/Deep 方法论

- 按流派拆分 Panel Compound Node；
- 迁移 Comps、LBO、三表、Segmental 和其他方法；
- 增加 Bull/Bear 辩论和冲突解释；
- 增加专业质量 Gate 和人工复核；
- 为每种方法建立结果等价或语义等价测试。

### Phase 5：HTML/PNG 导出

- 从 `report_presentation` 生成独立 HTML；
- 增加横版战报和竖版分享卡导出；
- 导出文件记录 report artifact id、graph revision、数据时间戳和版本；
- 导出器失败不能影响 Agent OS 语义报告交付。

## 11. 测试策略

### 11.1 契约测试

- 每个 UZI adapter 都必须通过 Tool/Skill 输入输出 Schema 校验；
- UZI 映射文件必须通过 Schema 校验，覆盖 22 个采集模块、20 个原始 Profile 维度、
  66 个 Panel 注册成员和所有派生方法；缺少字段映射、provider、quality policy、
  migration status 或 replay fixture 的条目必须被标记为未完成；
- Source 版本文件、许可证文件和每个 Provider replay Fixture 必须有 SHA-256 指纹，
  契约测试必须重新计算仓库内 Fixture 并校验 mapping entry 的引用；
- 缺失、timeout、provider error 必须生成可识别的质量状态；
- 独立 Snapshot、Bundle 索引和方法论结果必须验证生命周期状态与质量状态不会混淆；
- 单个 Snapshot 的降级、过期和失败必须能被 Planner 识别，并且不能让整个 Bundle
  静默变成 available；
- 每个 Artifact 必须包含 producer、source_refs、confidence 和 risk_flags；
- 报告只能引用 Board 中 active Artifact。
- Profile 选择、Capability 评分、预算、freshness、TTL、缓存指纹和 stop condition 必须可验证；
- `completed`、`completed_partial`、`running` 和 `gated` 的 Run 状态转换，以及
  `synthesis_unavailable` 交付状态，必须在 Runner、API、SSE、Chat 和恢复接口中保持一致；
- Artifact envelope 字段不能由 Pack content 覆盖，Schema 版本不兼容必须拒绝复用。

### 11.2 结果级测试

- Lite 核心指标与 UZI Lite 结果进行数值容差比对；
- 比对必须基于固定 Provider replay fixture、输入 hash 和明确的 `as_of` 时间；
- DCF 假设、现金流、估值区间逐项比对；
- Panel 的投票分布和跳过规则逐项比对；
- 杀猪盘八信号的风险等级和证据引用进行语义比对；
- UZI 缺失数据时，Cognitrade 必须保持缺失，不得用中性值补齐。
- 代理模型必须与完整来源模型分开标记；`assumption_only` 不能通过等价结果测试。
- 原始维度 coverage 必须与源 Profile 的 enabled dimension 集合一致，派生 Artifact 不得重复计数。
- `stock_screen`、`sector_research` 和 `review_thesis` 必须分别验证候选、板块分析和
  Thesis Review 的结构化结果、来源引用、缺失状态及报告交付，不得只验证 HTTP 200。

### 11.3 运行级测试

- Lite 首次运行；
- Lite 第二次缓存命中；
- Lite 升级 Medium 的增量运行；
- Provider 超时后的降级报告；
- 节点取消、暂停、恢复和局部重跑；
- Planner 根据 Board 缺口增加或移除子图；
- Planner 根据候选能力评分、预算、来源健康度和 Board 状态选择不同能力；
- 用户只请求单个方法时，不能隐式生成无关的 Profile 默认产物；
- Planner 不能创建未注册的 Capability/Artifact/Tool，且第二个非金融 Pack 能在不修改
  Core 的情况下完成同类 Graph 编译；
- 单个 Snapshot 的跨 Run 缓存命中、过期和局部重跑不能触发无关维度重复采集；
- 缓存命中必须先重挂载具体 Artifact 引用到当前 Board，不能只按 Artifact 类型复用；
- 质量 Gate 阻止不完整的高风险交付物；
- synthesis 失败、部分交付、取消和继续后台运行的状态转换及重试；
- 每个安全点都有可恢复的 `main_agent_progress` 更新，且刷新页面后内容一致；
- 低层事件合并、progress 模型调用次数/Token/间隔/超时预算及结构化 fallback 可回放；
- 用户可见的进度和交付摘要必须按请求 locale 输出，机器事件名可以保持稳定英文；
- SSE、Chat Thread 和前端报告读取同一个 Run 状态。
- Graph Patch 的版本冲突、拒绝、局部失效和安全点应用必须可回放；硬截止必须验证
  `completed_partial` 与后台继续运行两种状态不能混淆。

### 11.4 视觉测试

- Hero、Dashboard、Methodology、Panel、Radar、Risk 在桌面视口可读；
- 移动视口不发生文本、表格和图表重叠；
- 数据缺失时显示明确的 gap 状态；
- 报告导出 HTML、横版 PNG、竖版 PNG 与同一 `report_presentation` 一致。

## 12. 风险与控制

| 风险 | 控制措施 |
|---|---|
| UZI CLI 长时间阻塞 | Agent OS Tool timeout、取消协议、单 fetcher 隔离；禁止同步阻塞主请求 |
| Lite 配置与实际采集不一致 | Planner 以 Pack capability catalog 为准，并测试实际节点集合 |
| UZI raw JSON 绕过 ArtifactBoard | 只允许 Import Adapter 进入 Board，禁止跨节点传 raw dict |
| 66 位评审成本过高 | Lite/Medium/Deep 分级，按 10/51/51 参与；默认不展开 66 人全量 Panel |
| 报告视觉掩盖证据缺失 | Presentation Renderer 只能读取 Artifact，缺失必须显式展示 |
| 旧缓存污染新结果 | Cache key 加 schema/provider/period，旧缓存只读导入 |
| 许可证和来源不清 | 锁定 MIT 版本、保留版权和来源信息，迁移代码逐项登记 |
| 专业方法论生成投资建议 | 强制风险、置信度、来源和免责声明 Gate |
| 关键财务输入缺失 | 进入 `data_gap` 或 `gated`，禁止生成买入区间、行动指令或高置信度结论 |
| Panel 被误解为真人意见 | 使用方法论视角标识、版本和来源，禁止伪造真人独立判断 |
| Profile 约束被默认图绕过 | Graph metadata、事件和审计记录必须保存 requested/selected profile 及选择原因 |

## 13. 验收标准

方案完成需要满足：

- Cognitrade 正常运行不依赖 `D:\Cognitrade\_tmp_uzi_skill`；
- Finance Pack 可以在不启动 UZI CLI 的情况下生成 Lite 研究报告；
- 所有已安装并声明的 Finance Pack intent，包括 `stock_screen`、`sector_research` 和
  `review_thesis`，都能通过 Agent OS 生成对应的结构化交付物；
- Agent OS 能按任务目标和预算选择 Lite、Medium 或 Deep 能力；
- “功能性迁移完成”按 3.4.1 定义验收：请求、动态图、Pack 能力、Board、Gate、中文
  Main Agent synthesis、结构化报告和前端展示全链路可回放；来源未等价项必须显式降级；
- 同一标的的缓存命中、刷新和失效均可审计；
- 每个可复用维度都有独立 Snapshot Artifact，Bundle 只是派生索引；
- UZI 方法论结果全部通过 ArtifactBoard 共享；
- 报告结论、评分、风险和 Panel 都有来源和责任节点；
- Lite → Medium 只补缺失能力，不重复执行有效节点；
- 前端能展示 UZI 风格的信息层级，但不依赖 HTML 字符串解析；
- 导出 HTML/PNG 失败不会破坏语义报告和运行审计；
- 任意高风险或证据不足报告不能绕过专业 Gate；
- 第二个非金融 DomainPack 不需要修改 Agent OS Core。
- Planner 不是按 manifest 列表顺序选能力，而是依据可解释选择结果生成动态图；
- 用户请求的方法都能从已安装 Pack 的方法目录解析到 capability、输入契约和输出 Artifact；
- 运行过程中的 Main Agent 进度摘要、最终 synthesis 和 assistant message 均可从同一 Run 回放；
- Pack 原始摘要不能替代 Main Agent synthesis；没有 synthesis 时必须显式呈现未完成状态；
- synthesis 不可用时 Run 不得伪装为完整交付，且必须支持受治理的重试；
- 正常运行路径对 UZI CLI、UZI raw dict、UZI HTML 和 `_tmp_uzi_skill` 的依赖为零；
- Adapter 仅可用于显式 cache import 或隔离等价测试，并且有调用计数和删除门槛。
- Graph Patch、硬截止、局部重跑和跨 Run Artifact 重挂载均有可回放证据；

## 14. 当前下一步

当前代码已经具备 Finance Pack 内的 UZI 风格 DCF、Lite/Medium/Deep Panel 规则、风险
扫描、机构模型、研究工作流、Deep 交叉检验、`report_presentation`、Agent OS API
查询、三个专用 intent 的 Pack Artifact 交付和前端结构化读取路径。22 个采集模块、20
个原始 Profile 维度、66 个 Panel 成员及派生方法也已经进入机器可读映射；这些条目中
仍有 provider gap 或 `not_equivalent` 的，必须继续保持显式降级，不能标记为来源等价。

本阶段功能性迁移的收尾项已经完成：

1. 三个专用 Artifact 的阻塞 Quality Gate、默认 Profile、预算和硬截止契约已进入 Pack；
2. 版本、许可证和 replay Fixture 的 SHA-256 指纹已进入映射 Schema 与契约测试；
3. Graph Patch、硬截止、synthesis 失败/重试和 progress 摘要限制已有运行级回归；
4. 宁德时代/固定 Fixture 的 UZI 方法、三个专用 intent、Agent OS API/SSE 和前端
   Artifact 读取路径均已通过回归；
5. 正常 API、Chat、CLI、恢复路径不引用 UZI runtime、旧 Composer、raw dict 或旧固定主链，
   独立 legacy 兼容集也已通过。

以下是功能迁移后的持续硬化项，不阻断本阶段交付，但不能被误标为来源等价：

- Provider gap 条目的真实数据源补齐和 UZI `not_equivalent` 方法的逐项等价验证；
- Finance Pack 维度级 Artifact cache 的跨 Run 重挂载和更细的局部失效压测；
- HTML/PNG 导出增强及真实浏览器截图回归；当前环境只完成前端构建、Dashboard 契约和
  API Artifact 回放验证；
- progress 模型的独立 Token/网络超时预算实现，当前已实现调用次数、最小间隔和结构化
  fallback。

显式的 cache import 工具可以继续作为迁移工具存在，不属于正常运行时依赖。

本阶段验收证据（2026-07-12）：

```text
Agent OS 全量回归：files=171 failures=0
Legacy 兼容回归：files=1 failures=0
Frontend lint/build：通过
Python compileall、git diff --check：通过
Finance Pack 专用 API 回放：stock_screen / sector_research / review_thesis 均完成
screening_result / sector_analysis / thesis_review + research_report + main_agent_synthesis
```

UZI 的 66 人注册表是版本基线，不能被 UI 或报告误称为当前每次运行都执行了 66 人 Panel；
实际运行人数必须由 Profile、预算和来源质量决定，并写入 Artifact 和审计事件。

## 15. 实现状态矩阵和删除门槛

每次阶段提交必须更新下表，不能只以“代码已存在”作为完成依据：

| 能力 | 当前状态 | 目标 Artifact | 正常路径 | 测试证据 | Adapter 删除条件 |
|---|---|---|---|---|---|
| 7 个核心快照 | Pack-native 独立 Snapshot、Bundle 索引、并发采集和 Provider raw cache 已完成；维度级 Artifact cache 跨 Run 硬化仍待补充 | 对应 Snapshot Artifact + Bundle index | Agent OS Tool Registry | provider/质量传播/降级/并发/局部重跑测试 | 无 UZI 调用 |
| DCF / 基础评分 | Pack-native DCF 已实现并有固定 Fixture 回放；完整 UZI 来源等价尚未完成 | `valuation_model` | Pack Subagent | 数值容差、字段映射和缺失输入测试 | 等价测试通过 |
| Panel | Lite/Medium/Deep 规则已注册并运行；逐成员来源映射和完整等价尚未完成 | `investor_panel_review` | Pack Subagent/Compound | 投票、跳过、成员规则和来源测试 | 无 persona 脚本依赖 |
| 风险扫描 | Lite 已注册并运行；外部搜索仍为 data gap | `risk_scan` | Pack Subagent | 风险等级和 Gate 测试 | 无 UZI raw rule 依赖 |
| Presentation | 已注册并由 Agent OS API/前端读取 | `report_presentation` | Pack Composer | API/SSE/视觉测试 | 不读取 UZI HTML |
| Main Agent synthesis/progress | 成功、失败、重试、中文过程摘要、调用次数限制和结构化 fallback 已完成；独立 Token/网络超时预算待补强 | `main_agent_synthesis` + `main_agent_progress` + `assistant_message` | Agent OS Core | 成功、失败、进度回放和 SSE 交付测试 | 不回退为最终 Pack 摘要 |
| 机构模型 | Medium/Deep 已迁移首批纯计算方法 | `institutional_analysis` | Pack Subagent | DCF/三表/LBO/并购/分部数据缺口测试 | 无 UZI CLI 依赖 |
| 研究工作流 | Medium/Deep 已迁移 | `research_workflow` | Pack Subagent | 覆盖、业绩、催化剂、Thesis、Screen 测试 | 无原始 workflow JSON 依赖 |
| Deep 交叉检验 | Deep 已迁移首批方法 | `deep_analysis` | Pack Subagent | IC/DD/竞争/多空和缺口测试 | 无原始 compute 脚本依赖 |
| UZI 原始来源 | 22 个采集模块、20 个 Profile 维度和 66 个 Panel 成员已完成机器可读功能映射；provider gap 和 `not_equivalent` 已显式保留 | 对应 Snapshot Artifact | Pack Tool/Skill | 每个来源的 provider/降级/等价测试 | 无 legacy fetcher 依赖 |
| Panel 规则 | 当前为 10 个 Pack 方法论视角；目标为 Medium/Deep 51 人规则 | `investor_panel_review` | Pack Subagent/Compound | 规则覆盖、跳过和来源测试 | 无 persona 脚本依赖 |
| Cache import | 已实现为显式、迁移专用导入工具；不属于正常运行路径 | Snapshot Artifact | 显式导入命令 | schema/freshness/lineage/来源隔离测试 | 仅导入入口可见 |
| Capability selector/method catalog | 结构化方法目录、专用 intent Profile、Profile 输出、输入选择和安全点重规划已实现 | Plan decision + registered method contracts | Agent OS Planner + Pack manifest | 候选淘汰、预算、质量、fallback、Patch 和 API 解析测试 | 不依赖固定列表顺序 |
| 方法字段与质量契约 | `required_fields`、单位、质量策略和三个专用 Artifact Gate 已进入 Pack manifest；少数 provider 字段仍有 gap | MethodDefinition + input requirements + method Gate | Planner/Runner/Governance | 缺字段、代理字段、降级、data gap、单位和 Gate 测试 | 所有声明方法的 Gate 通过 |
| Snapshot 规范化 | 生产内容已 canonical 为 `snapshot_type`；`dimension` 仅保留为迁移输入别名 | `snapshot_type` canonical Snapshot | Pack adapter + ArtifactBoard | 别名规范化、缓存键和局部重跑测试 | 正常路径不依赖双协议 |
| API 入口收敛 | `/api/v1` 与 `/api/*` 兼容路由并存 | `/api/v1` canonical API | API/Chat/CLI/SSE | 调用计数、静态依赖和兼容回归测试 | 正常调用归零 |
| `stock_screen` | Pack intent、真实候选池、专用 Artifact、Agent OS 报告链和 API 回归已完成；来源等价和视觉回归仍需持续 | `screening_result` | Agent OS + Finance Pack | 候选筛选、来源、降级、Gate 和前端 Artifact 回归 | 无静态候选数据 |
| `sector_research` | Pack intent、板块快照、分析、专用 Artifact、Agent OS 报告链和 API 回归已完成；来源等价和视觉回归仍需持续 | `sector_analysis` | Agent OS + Finance Pack | 板块证据、热力图、降级、Gate 和前端 Artifact 回归 | 无静态板块数据 |
| `review_thesis` | Pack intent、Thesis Review、专用 Artifact、Agent OS 报告链和 API 回归已完成；来源等价和视觉回归仍需持续 | `thesis_review` | Agent OS + Finance Pack | 输入解析、证据引用、风险、Gate 和报告回归 | 无固定 Thesis 文案 |

删除 UZI runtime adapter 的硬门槛：正常 API、Chat、CLI 和后台恢复路径的静态依赖检查
为零；运行 telemetry 在完整回归中为零；映射文件 Schema、所有列出的结果级和运行级
测试通过；`stock_screen`、`sector_research` 和 `review_thesis` 的正常交付已验证；并且
cache import 已能独立运行。Cache import 是迁移工具，不是正常运行时依赖；它不能阻止
正常路径删除 UZI runtime adapter，但必须与正常路径隔离并单独测试。未满足正常路径门槛
时，只能保留隔离 adapter，不能把迁移标记为完成。
