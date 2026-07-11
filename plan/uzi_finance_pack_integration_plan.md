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

### 3.2 深度档位

UZI 声明了三种深度，但当前实现需要区分“配置目标”和“真实运行行为”。

| 模式 | 设计范围 | 设计产物 | 当前运行注意事项 |
|---|---|---|---|
| `quick-scan` | 4 个核心维度、Top 10 评委、杀猪盘检测 | Markdown | 不生成完整 HTML |
| `lite` | 7 个核心维度、10 位评委、DCF | 完整 HTML | 默认 pipeline 当前未完全遵守维度过滤 |
| `medium` | 22 个维度、完整 Panel、17 种方法 | 完整 HTML | 适合日常深度分析 |
| `deep` | medium 加多空辩论、Segmental 等 | 完整 HTML | 成本和等待时间最高 |

### 3.3 已发现的迁移阻碍

当前 UZI 的 Lite 不能直接作为 Cognitrade 的五分钟 SLA 依据：

- `lib/pipeline/collect.py` 当前按完整注册表遍历 fetcher，未将 `analysis_profile.fetchers_enabled` 作为采集过滤条件；
- legacy 路径虽然会过滤主采集维度，但 wave 3 仍会执行基金持仓和相似股票采集；
- fetcher 对外部数据源有 90、120、150 或 180 秒级别的超时；
- `resume` 会复用同一 ticker 的有效缓存，但实时基础行情仍会刷新；
- 部分缓存只保存原始数据，不包含 Cognitrade 需要的 Artifact 版本、来源和失效关系；
- 直接调用 UZI CLI 会绕过 Agent OS 的统一取消、预算、事件和审计协议。

迁移前必须把这些行为显式化，不能继续依赖“文档预计 1-2 分钟”的声明。

### 3.4 Lite、缓存与五分钟目标

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
| 65/66 位投资者评分 | Panel Subagent 或流派 Compound Node | `investor_panel_review` |
| 8 信号杀猪盘检测 | Risk Subagent | `risk_scan` |
| `agent_analysis.json` | Analyst Synthesis Subagent | `financial_analysis` / `research_synthesis` |
| `assemble_report.py` | Presentation Renderer/Exporter | `report_presentation`、HTML/PNG |

UZI 旧脚本可以在迁移早期通过 adapter 被调用，但 adapter 必须输出统一 Artifact，不能把脚本返回的 raw dict 继续作为跨节点共享数据面。

## 6. 目标 Artifact 契约

### 6.1 `valuation_model`

```json
{
  "artifact_type": "valuation_model",
  "schema_version": "1",
  "model": "dcf",
  "symbol": "300750.SZ",
  "fair_value": 420.0,
  "market_price": 348.76,
  "margin_of_safety": 0.204,
  "assumptions": {
    "wacc": 0.085,
    "terminal_growth": 0.025,
    "forecast_years": 5
  },
  "sensitivity": [],
  "source_refs": [],
  "confidence": 0.61,
  "risk_flags": ["cashflow_assumption_driven"]
}
```

### 6.2 `investor_panel_review`

```json
{
  "artifact_type": "investor_panel_review",
  "schema_version": "1",
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
  "symbol": "300750.SZ",
  "risk_level": "attention",
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
  "generated_at": "2026-07-12T00:00:00Z"
}
```

Presentation Artifact 不得添加语义数据或补全缺失事实。没有数据时显示 `data_gap`，并引用对应风险或来源状态。

## 7. 动态 Graph 设计

### 7.1 Core Loop

普通问题、解释报告、询问“刚刚研究了什么”等不进入 Finance Pack，不创建完整 Graph，由 Main Agent 直接处理或调用 Core Tool。

### 7.2 Lite Graph

Lite 不是一条固定硬编码流程，而是 Planner 在预算约束下从能力目录选择的最小可交付图：

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

Lite 的固定部分是治理预算和最低交付要求，不是节点名称顺序。若输入只要求 DCF 或只要求风险扫描，Planner 应生成更小的子图。

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

生产运行只写 Agent OS RunStore、ArtifactBoard 和统一缓存层。UZI cache 只作为一次性导入源或开发调试源，不作为正常运行的事实数据库。

### 8.3 Cache Key

缓存键至少包含：

```text
domain_id
symbol
artifact_type
provider
schema_version
data_period
```

同一 symbol 的 Lite 和 Medium 可以共享兼容 Artifact，但不同 provider、时间区间或 schema 版本不能静默覆盖。

## 9. API 与前端接入

### 9.1 后端 API

现有 Agent OS API 保持唯一入口：

```text
POST /api/v1/chat/threads/{thread_id}/runs
POST /api/v1/runs
GET  /api/v1/runs/{run_id}
GET  /api/v1/runs/{run_id}/events
GET  /api/v1/runs/{run_id}/artifacts
```

不新增“UZI 专属运行 API”。如需调用 UZI 风格导出，使用报告导出操作读取已完成的 `research_report` 和 `report_presentation` Artifact。

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

### Phase 1：UZI Lite 语义能力

- 将 7 个核心维度映射到 Finance Pack Skills；
- 迁移 DCF 和基础评分；
- 增加 `valuation_model`、`risk_scan`、`investor_panel_review` Schema；
- Lite Panel 使用 10 位代表性评审；
- 实现 freshness、timeout、fallback 和 source_refs；
- 建立 Lite Graph 的端到端测试。

交付标准：不调用 UZI CLI，也能从 Agent OS 生成可审计的 Lite `research_report`。

### Phase 2：UZI 风格 Presentation View

- 增加 `report_presentation` Artifact；
- Cognitrade Workbench 实现 Hero、Dashboard、Methodology、Risk 和 Panel 视图；
- 使用真实 Artifact 数据，不直接嵌入 UZI HTML；
- 支持缺失数据和降级状态；
- 完成桌面和移动端截图回归。

交付标准：Cognitrade 前端能展示 UZI 的主要信息层级，但数据来源完全来自 Agent OS。

### Phase 3：Cache 和中断恢复

- 实现 UZI cache 一次性导入 Adapter；
- 将有效维度转换为 Board Artifact；
- 为实时数据、季度数据和低频研究数据设置不同 TTL；
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
- 缺失、timeout、provider error 必须生成可识别的质量状态；
- 每个 Artifact 必须包含 producer、source_refs、confidence 和 risk_flags；
- 报告只能引用 Board 中 active Artifact。

### 11.2 结果级测试

- Lite 核心指标与 UZI Lite 结果进行数值容差比对；
- DCF 假设、现金流、估值区间逐项比对；
- Panel 的投票分布和跳过规则逐项比对；
- 杀猪盘八信号的风险等级和证据引用进行语义比对；
- UZI 缺失数据时，Cognitrade 必须保持缺失，不得用中性值补齐。

### 11.3 运行级测试

- Lite 首次运行；
- Lite 第二次缓存命中；
- Lite 升级 Medium 的增量运行；
- Provider 超时后的降级报告；
- 节点取消、暂停、恢复和局部重跑；
- Planner 根据 Board 缺口增加或移除子图；
- 质量 Gate 阻止不完整的高风险交付物；
- SSE、Chat Thread 和前端报告读取同一个 Run 状态。

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
| 66 位评审成本过高 | Lite/Medium/Deep 分级，默认不展开完整 Panel |
| 报告视觉掩盖证据缺失 | Presentation Renderer 只能读取 Artifact，缺失必须显式展示 |
| 旧缓存污染新结果 | Cache key 加 schema/provider/period，旧缓存只读导入 |
| 许可证和来源不清 | 锁定 MIT 版本、保留版权和来源信息，迁移代码逐项登记 |
| 专业方法论生成投资建议 | 强制风险、置信度、来源和免责声明 Gate |

## 13. 验收标准

方案完成需要满足：

- Cognitrade 正常运行不依赖 `D:\Cognitrade\_tmp_uzi_skill`；
- Finance Pack 可以在不启动 UZI CLI 的情况下生成 Lite 研究报告；
- Agent OS 能按任务目标和预算选择 Lite、Medium 或 Deep 能力；
- 同一标的的缓存命中、刷新和失效均可审计；
- UZI 方法论结果全部通过 ArtifactBoard 共享；
- 报告结论、评分、风险和 Panel 都有来源和责任节点；
- Lite → Medium 只补缺失能力，不重复执行有效节点；
- 前端能展示 UZI 风格的信息层级，但不依赖 HTML 字符串解析；
- 导出 HTML/PNG 失败不会破坏语义报告和运行审计；
- 任意高风险或证据不足报告不能绕过专业 Gate；
- 第二个非金融 DomainPack 不需要修改 Agent OS Core。

## 14. 当前下一步

下一步只做 Phase 1 的最小闭环：

1. 固化 Lite capability catalog 和预算约束；
2. 增加 `valuation_model`、`investor_panel_review`、`risk_scan` Schema；
3. 将 UZI Lite 的 7 个核心维度接入 Finance Pack；
4. 生成真实 `report_presentation` Artifact；
5. 用宁德时代和两个已有测试标的完成结果级、缓存级和前端展示回归；
6. 验证通过后，再进入 Medium/Deep 和 HTML/PNG 导出。

在 Phase 1 和 Phase 2 完成前，不把 UZI 的完整 22 维、66 评委和 CLI 运行链直接接入 Cognitrade 正常 API。
