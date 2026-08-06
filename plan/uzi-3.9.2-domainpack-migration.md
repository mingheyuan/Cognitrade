# UZI Skill 3.9.2 → Cognitrade Agent OS / DomainPack 迁移方案

## 1. 目标与不可妥协边界

本方案把 UZI Skill 的研究能力迁入 `finance_ashare` DomainPack，并新增真正的基金研究链。迁移目标不是把 UZI 的 CLI 嵌入 Cognitrade，而是保留其可验证的方法论、数据契约和评审规则，重新落到 Agent OS 的 Task Intake、Capability Graph、受控 Tool、ArtifactBoard、Quality Gate 与 Web 交付边界中。

必须长期满足以下约束：

1. **零 UZI 外部运行依赖**：生产环境不 clone、submodule、shell-out 或 import 上游仓库；上游只作为版本固定的迁移来源与审计依据。
2. **真实数据优先且禁止伪造**：数据源失败或字段缺失时输出 `data_gap`；显式模型假设只能标记 `assumption_only`，不能伪装成观测数据。
3. **敏感来源显式启用**：浏览器、登录态、Cookie、账号型和可能受限的数据源默认关闭，只有管理员明确配置后才可进入允许列表。
4. **不兼容旧运行边界**：不保留 UZI CLI、`.cache`、HTML 报告服务器、cloudflared、legacy pipeline 或旧图协议的兼容层；报告导出由 DomainPack 直接读取 ArtifactBoard 后独立渲染。
5. **可审计、不可静默遗漏**：每个上游采集器、命令、方法、评审席位都必须进入机器可读账本，并具有目标能力、迁移状态、差异说明和验收证据。

## 2. 精确基线

| 项目 | 基线 |
|---|---|
| 上游仓库 | `https://github.com/wbh604/UZI-Skill` |
| 迁移提交 | `fce996c33e70eddce8e375f53cd252b549eb3d7c` |
| 上游版本 | `3.9.2` |
| Cognitrade 适配版本 | `3.9.2-adapted` |
| 上一版迁移提交 | `775c76101e8890492d21c2e2827692456fcddb58` / `3.9.1` |
| 上游 3.9.2 差异 | 1 个提交、20 个文件、`+599/-216` |
| 数据采集器 | 22 个 |
| 数据源目录 | 70 个（ID、市场、维度、层级、访问方式与上游逐项一致） |
| 专项命令 | 20 个 |
| 投资者评审席位 | 66 个、9 个流派 |

### 2.1 方法数量口径纠偏

上游 README 宣传为“17 个首批方法 + 5 个 Tier‑1 = 22”。但其首批清单逐项计数实际为 18：

- 估值建模 5：DCF、Comps、三表、Quick LBO、并购增厚/摊薄；
- 研究工作流 7：首次覆盖、财报解读、催化剂日历、投资逻辑追踪、晨报、量化筛选、行业综述；
- 深度决策 6：IC Memo、Porter/BCG、DD、单位经济、VCP、组合再平衡。

再加 Tier‑1 的 AI Readiness、Earnings Preview、Model Update、Returns Attribution、逐持仓 Rebalance，共 **23 个不同实现语义**。其中“组合再平衡”和“逐持仓再平衡”不是同一个能力，Cognitrade 账本按 23 个实现单元验收，同时保留上游“22”的宣传口径注记。`segmental-model` 是上游额外命令级产品，虽未进入 23 方法宣传清单，也已作为独立 Capability 和 Artifact 落地，避免只核对方法清单时遗漏。

### 2.2 UZI 3.9.2 必须对齐的回归修复

1. `fetch_financials` 显式输出 OCF、OCF 历史和 OCF/净利润比；不得用 FCF 代替 OCF，也不得在缺失时默认比率为 `1.0`。
2. `industry=None` 时 peers 返回 self-only fallback 与原因，不生成虚构同行。
3. 估值行业数据缺失时可使用 CNInfo 市场加权 PE，但必须标注 fallback 来源和原因。
4. pipeline registry 字段形状与真实 provider 输出一致，避免结构误差制造假缺口。
5. `agent_analysis` 结构不合法时退回可验证脚本骨架，不让错误结构进入综合结论。
6. 基金/ETF 预检、组合/对比路径和报告后处理在 Cognitrade 中分别映射为 Task Intake、Capability Graph 与 Web Artifact，不迁 CLI 流程。
7. cloudflared 自动安装、直接打开浏览器和直接 HTML 输出不迁入生产边界。

## 3. 目标架构

```text
自然语言 / Web 表单
        │
        ▼
Finance Task Intake ── 股票/基金/板块/回看消歧
        │
        ▼
Agent OS Capability Planner
        │
        ├── Pack Tool Registry ── 真实 provider、权限、超时、审计
        │
        ├── Pack Skills ───────── 数据标准化与快照契约
        │
        ├── Methodology ───────── 纯计算、规则、证据覆盖率
        │
        ├── ArtifactBoard ──────── 快照/分析/报告/演示
        │
        └── Quality Gates ──────── available/degraded/data_gap/assumption_only
        │
        ▼
API / SSE / Web / Pack-owned UZI HTML/PDF（零上游运行依赖）
```

## 4. 基金研究链

### 4.1 Task Intake

- 使用真实基金目录识别基金代码和基金名称，避免把 `110011` 等基金误判为股票或债券。
- 支持显式 `fund_research`、基金类别、基金代码列表和自然语言基金关键词。
- 基金范围不明确时返回澄清字段 `fund_category_or_codes`。
- 股票代码与基金代码从 slot 层开始分离，基金代码不得混入股票 snapshot 链。

### 4.2 真实数据采集

受控工具 `fund_data_provider` 采集：

- 基金目录：EastMoney，经 AkShare `fund_name_em` 适配；
- 开放式基金排行：EastMoney，经 AkShare `fund_open_fund_rank_em` 适配；
- 场内基金排行：EastMoney，经 AkShare `fund_exchange_rank_em` 适配；
- 历史净值：EastMoney，经 AkShare `fund_open_fund_info_em` 适配；
- 定期披露持仓：优先 AkShare，必要时使用 EastMoney FundArchives 的公开响应。
- benchmark：蛋卷基金公开接口返回的基准定义、基金/基准区间收益和回撤；
- 基金经理任期：EastMoney 基金档案中的历史任职分段与任期回报；
- 单基金规模/份额：EastMoney 基金档案中的申购、赎回、总份额与净资产序列。

provider 失败时保留失败阶段和异常类型。前端只展示用户可理解的缺口，不直接泄漏网络异常原文。

### 4.3 分析方法

只在相同粗分类内比较；观察字段包括近 1 年收益、3 年年化收益、近 1 年基准超额收益、最大回撤、年化波动、费率与收益一致性。经理任期和规模/份额变化作为解释性证据，不在缺乏充分校准前直接加减分。缺失字段不补 0、不补中性值，只降低覆盖率并记录 `data_gap`。

推荐门槛：

- 同类可比较样本至少 2 只；
- 单只候选得分证据覆盖率至少 60%；
- 单基金可以做事实评估，但不能伪造横向排名；
- 推荐最多 3 只，必须能回溯到净值、排行和持仓来源。

### 4.4 交付

- Artifact：`fund_snapshot`、`fund_analysis`、`research_report`；
- Web：基金类别、代码、持有周期和候选池参数；
- 展示：候选分、覆盖率、收益、回撤、波动、集中度、最新披露持仓和数据缺口；
- 证据链：可跳转到对应 Agent OS run 和 ArtifactBoard。

## 5. UZI 能力迁移策略

### 5.1 数据维度

22 个 fetcher 均保留一一映射。默认开放公开、无账号、只读来源；登录态/浏览器类来源进入显式开关。provider 不可用时仍生成对应维度的 `data_gap` 快照，保证“能力存在”和“本次有数据”是两个不同事实。

### 5.2 评审团

- Quick：5 席；Lite：10 席；Medium/Deep：66 席；
- 66 个稳定 `member_id` 必须全部注册，不按旧版 51 席截断；
- 规则缺字段时跳过该规则并记录 `data_gap_rules`，不得以默认值判定通过；
- 每个 vote 必须标明它是方法论视角，不冒充对应人物的现实观点或实时持仓。

### 5.3 方法与命令

命令不作为 CLI 迁入。20 个命令映射到 Intent、requested methods 或专属 Web workflow；23 个方法映射到 Pack capability / methodology output。映射状态以 `uzi_3_9_2_capability_ledger.yaml` 为准，任何 `planned` 或 `partial` 都不能在 UI 中宣称完整等价。

## 6. 数据源治理

| 分级 | 默认状态 | 示例 | 要求 |
|---|---|---|---|
| Public read-only | 开启 | AkShare 适配的公开行情、财务、基金净值 | timeout、重试、source ref、缓存与错误脱敏 |
| Credentialed API | 配置后开启 | 需要 Token/Key 的专业数据 | 密钥只进环境变量，不写仓库；启动预检 |
| Sensitive browser/login | 默认关闭 | Cookie、登录站点、Playwright 浏览器采集 | 管理员显式开关、独立权限、审计和来源标识 |
| Unavailable | 禁止模拟 | 任意失败或无覆盖来源 | `data_gap`，不返回伪观测值 |

## 7. 分阶段落地与验收

### Phase A：基线、契约和回归修复

- [x] 固定 UZI `fce996c` / 3.9.2；
- [x] 更新 source mapping、插件与许可证指纹；
- [x] Medium/Deep 评审团恢复为 66 席；
- [x] OCF 与 FCF 分离，OCF 缺失规则不再默认通过；
- [x] 所有评审规则字段均进入证据门，lambda 默认值不再当作观测值参与投票；
- [x] 更新 replay fixture 版本与哈希；
- [x] 将上游 8 个 3.9.2 回归逐个映射为 Pack 原生测试或明确拒绝迁入的运行边界测试。

### Phase B：基金研究 MVP

- [x] 基金代码/名称 Task Intake；
- [x] 受控 `fund_data_provider`；
- [x] 排行、净值、风险和披露持仓采集；
- [x] 同类评分、覆盖率门槛与无填充规则；
- [x] 基金 Artifact、质量门和专属 Web 页面；
- [x] 加入基金 benchmark、基金经理任职分段、规模与份额变化并升级为 v2 方法。

### Phase C：23 个方法完整能力化

- [x] 按账本逐个实现 Pack-native capability；
- [x] 每个方法补齐 input contract、quality gate、fixture 和等价性级别；
- [x] Tier‑1 的逐持仓 rebalance 已与旧组合再平衡语义拆开；旧组合再平衡仍单独保留在账本；
- [x] Tier‑1 五方法全部提供专属 Capability、Artifact、质量门、测试与 Web 视图；
- [x] 新增 `portfolio_research`，组合收益缺失不补零、费用模型不硬编码、等权目标标记为策略假设；

### Phase D：20 个命令的 Web/Intent 等价交付

- [x] 快速扫描、面板、陷阱、DCF/Comps/LBO 等转为 requested methods；
- [x] 首次覆盖、财报、催化剂、thesis、DD、IC Memo 等转为独立 Artifact 交付链；
- [x] 组合、对比、归因与两类再平衡接入 Agent OS 与现有 Web 边界；
- [x] 不迁入 UZI CLI、缓存、HTML 服务或临时兼容入口；原生报告版式以 Pack-owned 模板迁入，并从 ArtifactBoard 导出 HTML/PDF。
- [x] 股票权益基金可从冻结的 `fund_snapshot` 显式创建持仓股票报告父批次；普通基金使用直接披露、ETF 联接使用已审计穿透持仓，最多十个独立 `stock_check` 子运行按专用默认 5 并发执行。父批次保留重试、取消、续跑、历史版本与 PDF ZIP，子报告带基金来源上下文且不参与股票评分；基金级持仓综合 HTML/PDF 不在本阶段生成。

### Phase E：Provider 完整性与敏感源开关

- [x] UZI 3.9.2 的 70 个来源登记逐项固化，测试锁定完整 ID 集；登记只表示候选来源，实际可用性由每次 `source_trace` 报告；
- [x] 行情链补齐 AkShare、eFinance、TuShare、BaoStock 与 EastMoney/Sina/Tencent/Yahoo/Stooq 直连回退，并支持 A/H/US 路由；
- [x] 财务链补齐 MX API、AkShare、TuShare、BaoStock 与 yfinance；TuShare 账号无接口权限时不会阻断零 Key 回退；
- [x] 事件、舆情、政策、宏观、产业链、研报、行业、原材料、期货、治理、护城河、风险与比赛来源按 UZI 语义聚合，并输出逐源状态；
- [x] 为 22 个维度提供显式串行 provider smoke；输出仅保留标准缺口、错误类型和 source ref，不泄漏原始异常或原始观测；
- [x] 登录态源默认关闭，并要求总开关、来源 allowlist 和 Cookie 三项同时配置；
- [x] Playwright 及 Chromium 纳入本地安装说明和后端部署镜像，但浏览器抓取仍受显式开关约束；
- [x] 端到端验证 API、SSE、ArtifactBoard 与 Web 一致；基金 v2 字段已覆盖 API 运行、Artifact、Replay、事件和前端渲染测试。

## 8. 完成定义

只有同时满足以下条件才可将 UZI 迁移标记为完成：

1. 账本中 22 fetchers、20 commands、23 methods、66 panel seats 均无未解释缺项；
2. 所有 `equivalent` 条目有自动测试；所有 `adapted` 条目有差异说明；
3. 生产代码不依赖 UZI checkout、CLI、缓存目录或报告服务器；
4. 任意 provider 断网时不产生模拟数据或默认通过的研究结论；
5. 基金与股票代码不会串路由，基金推荐满足同类样本和证据覆盖门槛；
6. 前端不显示原始 JSON、原始网络异常或未经 gate 的推荐；
7. migration mapping、fixture hash、DomainPack manifest 与 UI 版本标识一致。

## 9. 审计入口

- 上游映射：`domain_packs/finance_ashare/migration/uzi_source_mapping.yaml`
- 全能力账本：`domain_packs/finance_ashare/migration/uzi_3_9_2_capability_ledger.yaml`
- DomainPack 清单：`domain_packs/finance_ashare/domain.yaml`
- 基金方法：`domain_packs/finance_ashare/subagents/fund_methodology.py`
- UZI 方法：`domain_packs/finance_ashare/subagents/uzi_methodology.py`
- UZI 独立方法产品：`domain_packs/finance_ashare/subagents/uzi_method_products.py`
- UZI Tier‑1 与组合方法：`domain_packs/finance_ashare/subagents/uzi_tier1_methodology.py`
- UZI 规则：`domain_packs/finance_ashare/subagents/uzi_panel_rules.py`
- 22 维真实源核验：`python -m research_system.runtime.diagnostics.status.uzi_provider_smoke --symbol 600519 --sector 白酒`
- 70 源精确目录：`research_system/adapters/vendors/uzi_source_catalog.py`
- 运行时来源与凭据健康：`research_system/runtime/diagnostics/status/provider_health.py`
