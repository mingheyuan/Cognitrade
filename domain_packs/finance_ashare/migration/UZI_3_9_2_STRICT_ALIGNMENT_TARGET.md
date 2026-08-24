# UZI 3.9.4 严格对齐目标与验收矩阵

## 1. 文档状态

- 目标状态：实现与本地回归完成；当前 PR 的 GitHub Actions 与真实 provider smoke 待远端验收，历史 CI 仅作为迁移基线
- 上游仓库：`https://github.com/wbh604/UZI-Skill`
- 唯一基准提交：`b004d7a988ca53a4805bf36784af091fa1209f22`
- 上游版本：`3.9.4`
- 根采集基准：对已解析个股复现上游 route-specific 采集；单股默认执行 v3 pipeline，`UZI_LEGACY=1` 强制 legacy，preflight 或采集编排异常时整股回退 legacy；versus/portfolio 与上游专用 runner 一样始终逐股调用 v3，失败后读已有 cache 或跳过，不重试 legacy；单个 provider/fetcher 错误仍留在 v3 `DimResult`
- 迁移边界：实现迁入 Finance DomainPack；生产环境不得依赖外部 UZI checkout、CLI、缓存目录或运行时
- 保留边界：Agent OS API/Web、ArtifactBoard、运行溯源与鉴权
- 公开方法边界：23 个 UZI 方法只能通过各自的 `finance.uzi.native.*` intent 触发；旧 `method_catalog` 已删除，通用 `requested_methods` 字段仅保留快照选择兼容语义，不能分派 UZI 原生方法
- 扩展边界：未被业务入口引用的 `cognitrade-extension-*` 已全部删除；组合能力保留三个 UZI 原生方法入口及原生 `--portfolio` 批量研究
- 验收原则：清单状态不是完成证据；只有上游差异测试、真实链路测试和产物审计共同通过后才能标记等价

## 2. 不可妥协规则

1. 相同输入、相同真实数据和相同显式假设下，字段形状、计算规则、命令语义、报告 DOM/CSS/JavaScript 必须与固定上游提交一致。
2. 允许差异仅限 Agent OS run/artifact ID、时间戳、ArtifactBoard 溯源和真实网络可用性。
3. 缺失数据保持缺失，不允许模拟市场、财务、持仓、新闻、来源或计算输入。
4. 仅允许 UZI 原生的定性推断兜底，且必须标记“基于公开信息推断，非一手数据”；数值和事实不得推断。
5. 敏感来源默认关闭，必须由用户显式启用并提供凭据；启用策略不得改变 UZI 的来源字段和结果结构。
6. 用户相关估值或交易假设必须显式选择并进入 ArtifactBoard；不得静默使用 `mid`。UZI 方法本身的固定常量保持原样。
7. 单股报告及 aggregate runner 已载入的成分，`critical` 必须经过 `suggested_fix → 恢复 → 重算 → 自审` 循环清零；不设人为轮数上限，不允许绕过。
8. `warning` 允许生成报告，但必须使用 UZI 原生 warning banner；`completed_partial` 不能绕过已载入成分的 `critical` 门控。UZI 原生 versus/portfolio 会跳过不可用成分；这些成分必须进入 `assessment.failed`，且不计入已载入子集的 aggregate `critical_count`。
9. HTML/PDF 使用同一份 UZI 原生可见报告；Cognitrade 元数据不得污染原生 DOM。

## 3. 基准资产与完整性锁

| 资产 | 上游路径 | 固定证据 | 目标 |
|---|---|---|---|
| 许可证 | `LICENSE` | SHA-256 `78bebb5232741327c31b0da4f5bbd570dc59ab3c3230582a4d1b4650238a7d58` | 保留 MIT notice 与来源提交 |
| 原生报告呈现层 | `skills/deep-analysis/assets/report-template.html` + 4 个 `lib/report` helper | v3.9.4 commit `b004d7a988ca53a4805bf36784af091fa1209f22`；模板 3615 行；SHA-256 `1984dbf7a5c07dc35e0bd553082c7fe2ab7dffa4c5eb2e23c942fcd4c98ad7c8` | 原样迁入并加逐文件哈希测试；分析方法仍固定在 3.9.4 |
| 来源注册表 | `skills/deep-analysis/scripts/lib/data_source_registry.py` | 73 个 source ID | 目标目录和运行注册表均为 73 项 |
| 命令目录 | `commands/*.md` | 20 个命令 | 20 个独立 Agent OS/API/Web 能力 |
| 独立技能方法论 | `skills/{investor-panel,lhb-analyzer,trap-detector}/**` | 17 个路径敏感资源；SHA-256 `8bc93074f001fa97154629dcd33cf5a6dfed653cf79d954fa883570be0a4f1ca` | 原样保留方法论作为静态契约；Agent OS 路由与 DomainPack 实现另行验收，不动态执行 Markdown |
| Fetcher | `skills/deep-analysis/scripts/fetch_*.py` | 22 个 fetcher | 调用链与结果契约逐项等价 |
| 方法 | 上游方法与命令实现 | 23 个实现单元 | 公式、字段、阈值和顺序逐项等价 |
| 自审 | `scripts/lib/self_review.py`、`review_stage_output.py` | `critical_count == 0` 才通过 | 不可绕过的运行状态机 |

## 4. 工作包 A：22 个 Fetcher

每一行必须同时满足：上游 provider 顺序一致、参数和日期范围一致、字段名称/类型/嵌套一致、来源标记一致、缓存/重试/兜底一致、空值语义一致、固定 replay 差异测试通过、至少一个真实 smoke 场景通过。结果契约按根路由分 lane 验收：默认 v3 lane 执行原始模块后提取 `data`、规范化、校验并生成带 `_pipeline` 的 `DimResult`；强制或整股回退的 legacy lane 保留 `run_fetcher` 原始 envelope。两者都属于固定上游 3.9.4 行为，不能用 legacy 对象身份替代 v3 结果契约。表内“根路由集成”证据由全 22-entry registry 的编排测试、`12_capital_flow` 真实 adapter exemplar、固定上游完整测试与逐字节校验共同组成；不把 fixture registry 测试误报成 22 个真实 adapter 的逐 lane 参数化测试。

| ID | 上游实现 | DomainPack 目标 | 当前基线 | 完成证据 |
|---|---|---|---|---|
| `0_basic` | `fetch_basic.py` | `market_snapshot_skill` | source_parity | pinned bytes + registry dispatch + root integration |
| `1_financials` | `fetch_financials.py` | `fundamental_snapshot_skill` | source_parity | pinned bytes + registry dispatch + root integration |
| `2_kline` | `fetch_kline.py` | `uzi_kline_snapshot_skill` | source_parity | pinned bytes + registry dispatch + root integration |
| `3_macro` | `fetch_macro.py` | `uzi_macro_snapshot_skill` | source_parity | pinned bytes + registry dispatch + root integration |
| `4_peers` | `fetch_peers.py` | `uzi_peers_snapshot_skill` | source_parity | pinned bytes + registry dispatch + root integration |
| `5_chain` | `fetch_chain.py` | `uzi_chain_snapshot_skill` | source_parity | pinned bytes + registry dispatch + root integration |
| `6_research` | `fetch_research.py` | `uzi_research_snapshot_skill` | source_parity | pinned bytes + registry dispatch + root integration |
| `7_industry` | `fetch_industry.py` | `uzi_industry_snapshot_skill` | source_parity | pinned bytes + registry dispatch + root integration |
| `8_materials` | `fetch_materials.py` | `uzi_materials_snapshot_skill` | source_parity | pinned bytes + registry dispatch + root integration |
| `9_futures` | `fetch_futures.py` | `uzi_futures_snapshot_skill` | source_parity | pinned bytes + registry dispatch + root integration |
| `10_valuation` | `fetch_valuation.py` | `uzi_valuation_snapshot_skill` | source_parity | pinned bytes + registry dispatch + root integration |
| `11_governance` | `fetch_governance.py` | `uzi_governance_snapshot_skill` | source_parity | pinned bytes + registry dispatch + root integration |
| `12_capital_flow` | `fetch_capital_flow.py` | `capital_flow_snapshot_skill` | source_parity | actual vendored v3 adapter exemplar + root/legacy integration |
| `13_policy` | `fetch_policy.py` | `policy_snapshot_skill` | source_parity | pinned bytes + registry dispatch + root integration |
| `14_moat` | `fetch_moat.py` | `uzi_moat_snapshot_skill` | source_parity | pinned bytes + registry dispatch + root integration |
| `15_events` | `fetch_events.py` | `events_snapshot_skill` | source_parity | pinned bytes + registry dispatch + root integration |
| `16_lhb` | `fetch_lhb.py` | `lhb_snapshot_skill` | source_parity | pinned bytes + registry dispatch + root integration |
| `17_sentiment` | `fetch_sentiment.py` | `sentiment_snapshot_skill` | source_parity | pinned bytes + registry dispatch + root integration |
| `18_trap` | `fetch_trap_signals.py` | `uzi_trap_snapshot_skill` | source_parity | pinned bytes + registry dispatch + root integration |
| `19_contests` | `fetch_contests.py` | `uzi_contests_snapshot_skill` | source_parity | registry dispatch + sensitive-source gate + root integration |
| `6_fund_holders` | `fetch_fund_holders.py` | `uzi_fund_holders_snapshot_skill` | source_parity | v3 dimension/top-level spill + legacy bonus integration |
| `similar_stocks` | `fetch_similar_stocks.py` | `uzi_similar_stocks_snapshot_skill` | source_parity | v3 same-name top-level spill + legacy bonus integration |

### 4.1 来源注册表

- UZI 固定提交实际登记 73 个来源，而不是旧迁移清单中的 70 个。
- 当前缺失且必须补齐：`yahoo_equity_screener`、`yahoo_fundamentals_timeseries`、`yahoo_fx_chart`。
- 默认 provider 顺序严格保持 `akshare → efinance → tushare → baostock`；Direct HTTP 仅按上游显式 fallback 或环境覆盖进入。
- Tier 1/2/3、market、dimension、access、health、base URL 必须逐字段与上游注册表一致。
- “来源已登记”不等于“Fetcher 已调用”；每个来源必须在上游实际调用位置具有对应的本地执行或显式不可用记录。

## 5. 工作包 B：20 个命令

命令以独立 Agent OS 能力暴露，同时保留上游输入、前置检查、执行阶段、所需维度、方法调用与输出语义；不得用一个通用 intent 冒充多个命令。固定上游只为部分独立命令声明隐式 depth：`quick-scan/panel-only=lite`、`initiate/ic-memo=deep`；Agent OS 对这四条使用确定性 collection profile，其他独立命令继续采用上游通用 profile resolver（未显式选择时默认为 medium），不自行发明命令档位。

| 命令 | 目标 | 当前基线 | 特别验收点 |
|---|---|---|---|
| `analyze-stock` | 完整股票分析 | source_parity | 完整 Stage 1/2/3/4 与报告门 |
| `quick-scan` | 独立快速扫描 | source_parity | 隐式 lite；仅 1/2/10/18 维、10 席、Markdown、无 HTML |
| `panel-only` | 独立评审团 | source_parity | 隐式 lite；0/1 基础输入、上游评分、完整 panel 与 synthesis 输出契约 |
| `scan-trap` | 杀猪盘扫描 | source_parity | 8 个信号与风险等级 |
| `dcf` | DCF | source_parity | 上游参数和输出 |
| `comps` | 可比公司 | source_parity | 上游 peers 输入与输出 |
| `lbo` | Quick LBO | source_parity | 保留固定函数默认交易假设；外层披露 assumptions 并标 degraded |
| `segmental-model` | 分部模型 | source_parity | 采集 research/moat/policy 等所需上下文；披露分部与输出结构 |
| `initiate` | 首次覆盖 | source_parity | 隐式 deep；完整 22-entry raw 输入与原生产物结构 |
| `earnings` | 财报解读 | source_parity | 原生产物结构 |
| `catalysts` | 催化剂日历 | source_parity | 有日期证据的事件 |
| `thesis` | 投资逻辑追踪 | source_parity | 历史运行/证据绑定 |
| `screen` | 量化筛选 | source_parity | 原生候选输入和排序语义 |
| `dd` | 尽调清单 | source_parity | 覆盖原生清单所需的财务、估值、行业、治理、事件与风险证据 |
| `ic-memo` | 投委会备忘录 | source_parity | 隐式 deep；完整 22-entry raw 输入；不替代人工决策 |
| `ai-readiness` | AI readiness | source_parity | 缺失保持缺失 |
| `earnings-preview` | 业绩预览 | source_parity | 完整 22-entry raw 输入；预测明确标记假设 |
| `model-update` | 模型更新 | source_parity | 保留固定函数 `updates=None` 的 demo mode；外层披露 assumptions 并标 degraded |
| `returns` | 收益归因 | source_parity | 保留固定函数“缺失收益按 0 贡献并列入 missing_return_tickers”的行为，外层标 degraded |
| `rebalance` | 持仓再平衡 | source_parity | 保留固定函数等权目标与原生固定费率；外层披露 target mode、fee schedule 并标 degraded |

## 6. 工作包 C：23 个方法

| 分组 | 方法 | 当前基线 | 完成证据 |
|---|---|---|---|
| valuation | `dcf` | source_parity | 直接与上游函数输出深度相等 |
| valuation | `comps` | source_parity | 直接与上游函数输出深度相等 |
| valuation | `three_statement` | source_parity | 直接与上游函数输出深度相等 |
| valuation | `quick_lbo` | source_parity | 直接与上游函数输出深度相等 |
| valuation | `accretion_dilution` | source_parity | 直接与上游函数输出深度相等 |
| workflow | `initiation_report` | source_parity | 直接与上游函数输出深度相等 |
| workflow | `earnings_review` | source_parity | 直接与上游函数输出深度相等 |
| workflow | `catalyst_calendar` | source_parity | 直接与上游函数输出深度相等 |
| workflow | `thesis_tracker` | source_parity | 直接与上游函数输出深度相等 |
| workflow | `morning_note` | source_parity | 直接与上游函数输出深度相等 |
| workflow | `quantitative_screen` | source_parity | 直接与上游函数输出深度相等 |
| workflow | `sector_review` | source_parity | 直接与上游函数输出深度相等 |
| decision | `ic_memo` | source_parity | 直接与上游函数输出深度相等 |
| decision | `porter_bcg` | source_parity | 直接与上游函数输出深度相等 |
| decision | `dd_checklist` | source_parity | 直接与上游函数输出深度相等 |
| decision | `unit_economics` | source_parity | 直接与上游函数输出深度相等 |
| decision | `value_creation_plan` | source_parity | 直接与上游函数输出深度相等 |
| decision | `portfolio_rebalance` | source_parity | 直接与上游函数输出深度相等 |
| tier1 | `ai_readiness` | source_parity | 直接与上游函数输出深度相等 |
| tier1 | `earnings_preview` | source_parity | 直接与上游函数输出深度相等 |
| tier1 | `model_update` | source_parity | 直接与上游函数输出深度相等 |
| tier1 | `returns_attribution` | source_parity | 直接与上游函数输出深度相等 |
| tier1 | `holding_rebalance` | source_parity | 直接与上游函数输出深度相等 |

### 6.1 方法验收规则

- 输入 schema、输出 artifact schema、公式、分母、四舍五入、阈值、排序和缺失值分支逐项差异测试。
- 上游固定常量保留；增长率、折现率、交易价格、杠杆、费率、目标权重等用户相关假设由 API/Web 显式收集。
- 上游函数已经声明的可选默认值原样保留，包括 `thesis_tracker.direction=long`、`quantitative_screen.style=quality`、三表/LBO/并购条款默认值、Model Update demo mode、等权再平衡和 Unit Economics/VCP 原生代理；只有调用形状错误或调用方显式传入非法值时才拒绝。
- 独立命令的采集 scope 必须覆盖固定函数实际读取的全部维度；缺少护城河、龙虎榜、资金面、舆情、同行或产业链时仍调用固定函数并保留原生结果，但 wrapper 必须把缺失观测、warning、代理或默认值列入 `data_gap` 并将质量降为 `degraded`，不得伪装为完整输入。
- 相同 fixture 与假设的结构化输出必须深度相等；仅浮点项可采用在测试中逐字段声明的上游数值容差。
- provider gap 不得被 wrapper 通过代理值、零值、默认中性分或替代公式隐藏；固定 UZI 函数自身明确实现的 fallback/default 保持不变，并由 wrapper 显式降级标注。

## 7. 工作包 D：恢复闭环与质量门

### 7.1 目标状态机

`collect → integrity_check → self_review → execute_suggested_fix → recollect/recompute → self_review`

- `critical_count > 0`：继续循环；真实来源耗尽时停在 `quality_gate_blocked`，不得生成 HTML/PDF。
- `warning_count > 0 && critical_count == 0`：允许完成，并将 warning 与 acknowledgement 注入 UZI 原生 banner。
- `critical_count == 0`：进入报告装配。
- 每条 issue 保存 severity、dimension/field、message、suggested_fix、attempt、provider trace、结果和耗尽原因。
- 恢复执行器逐条解释并执行上游 `suggested_fix`，不得只创建 `pending recovery_task`。
- 重跑必须使所有受影响派生方法、评分、panel 和报告输入失效并重新计算。
- 不设置固定三轮或其他人为次数上限；仅依据来源耗尽、取消或成功清零终止。
- `UZI_SKIP_REVIEW=1` 仅允许测试/开发代码路径，生产 API 与 Web 不得暴露。

### 7.2 上游恢复顺序

1. 原始 provider 与完整性检查。
2. 按 suggested_fix 定位字段和维度。
3. 逐条按 UZI suggested_fix 的原生优先级执行 browser/MX/Web 动作。
4. 六个定性维度优先使用已配置的 `MX_APIKEY`，再使用 DDGS/可信官方来源。
5. 当前研究档位启用浏览器恢复时，使用 Playwright 重试适用页面并读取网络结果。
6. 仅对允许的定性维度使用显式标注的推断；不得生成数值事实。
7. 重算 Stage 2、再次自审，直到通过或真实来源耗尽。

## 8. 工作包 E：UZI 原生报告

- 原样 vendor 上游 v3.9.4 `report-template.html` 和产生可见 DOM/SVG 的 4 个 `lib/report` helper，逐文件固定 SHA-256；不得只同步模板而留下旧 renderer。
- 迁入 `assemble_report.py` 及其 `lib/report/` 渲染依赖所需的纯能力，输入适配到 ArtifactBoard，但不执行外部 UZI runtime。
- 保持上游 DOM、CSS、JavaScript、19 维卡片、panel、机构模型、友好层、warning banner 与打印样式。
- HTML 与 PDF 使用相同 HTML；PDF 不得另建白底或简化版布局。
- `--versus` 使用原生 `versus_runner._render_html`，限制 2–4 只；`--portfolio` 使用原生权重归一化、健康度与 `_render_html`。
- 单股报告同时恢复 `share-card.png`、`war-report.png` 与 `one-liner.txt` 的原生选择器、分辨率和文案语义。
- `one-liner.txt` 必须使用上游 `panel.long_active` 口径（当字段缺失时按多头 mandate 的信号分布求和），不得残留旧版 50/51 席硬编码。
- Cognitrade run ID、artifact ID、来源链放在响应 header/sidecar artifact；仅上游已有插槽允许进入可见报告。
- PDF 采用受控 Chromium 进程池、队列、超时和缓存；并发失败不得通过无限启动浏览器规避。
- 模板哈希、DOM selector、关键文本、页面截图和 PDF 页面渲染均进入回归测试。

## 9. 验收测试矩阵

| 编号 | 证明对象 | 必须提供的证据 | 状态 |
|---|---|---|---|
| A1 | 22 fetcher 完整性 | 上游/本地 ID 集合完全相等 | 本地回归通过；当前 PR CI 待验收 |
| A2 | 73 sources 完整性 | 注册表逐字段深度相等 | 本地回归通过；当前 PR CI 待验收 |
| A3 | fetcher 结果与根路由 | 固定源码逐字节校验、全 22-entry fixture 编排、`12_capital_flow` 真实 adapter exemplar、legacy envelope、强制/整股回退及 provider error 不回退测试 | 本地回归通过；当前 PR CI 待验收 |
| A4 | provider 行为 | 逐维 direct provider 可用、失败、超时、空响应、敏感源关闭的 smoke；不冒充完整 v3 root 实网运行 | 历史基线通过；当前 PR real-provider job 待验收 |
| B1 | 20 commands 完整性 | 命令 ID、schema、执行图、产物逐项等价 | 本地回归通过；当前 PR CI 待验收 |
| B2 | quick-scan | 1/2/10/18、10 席、Markdown、无 HTML | 本地回归通过；当前 PR CI 待验收 |
| C1 | 23 methods 完整性 | ID/公式/字段/阈值/缺失分支差异测试 | 本地回归通过；当前 PR CI 待验收 |
| C2 | 显式假设 | API、Web、ArtifactBoard 往返及无 silent-mid 测试 | 本地回归通过；当前 PR CI 待验收 |
| D1 | suggested_fix | 每种 issue 被执行并触发重算/自审 | 本地回归通过；当前 PR CI 待验收 |
| D2 | critical 门控 | 单股及 aggregate 已载入子集的 critical 永不生成 HTML/PDF，且不能被 completed_partial 绕过；未载入成分进入 `assessment.failed` | 本地回归通过；当前 PR CI 待验收 |
| D3 | warning | 原生 banner 可见且报告成功 | 本地回归通过；当前 PR CI 待验收 |
| D4 | 无三轮上限 | 超过三轮仍继续，来源耗尽才阻断 | 本地回归通过；当前 PR CI 待验收 |
| E1 | 模板完整性 | vendor 文件哈希等于上游固定值 | 本地回归通过；当前 PR CI 待验收 |
| E2 | HTML 可见等价 | DOM/CSS/JS selector 与安全转义回归 | 本地回归通过；当前 PR CI 待验收 |
| E3 | PDF 可见等价 | 同 HTML 渲染和 PDF 头/页面回归 | 历史 Chromium 基线通过；当前 PR CI 待验收 |
| E4 | PDF 稳定性 | 并发 10 次全部成功且资源回收 | 本地回归通过；当前 PR CI 待验收 |
| F1 | 零外部依赖 | 生产导入/运行不读取 UZI checkout | 本地回归通过；CI 仅在测试 job checkout 固定上游 |
| F2 | 完成审计 | 本文每一行具有代码、测试和运行证据 | 实现和本地证据完成；当前 PR CI/真实 provider 证据待回填 |

## 10. 历史基线 CI 与当前 PR 验收

- 历史基线提交：`542ea6ccc3b231d81d63634b266648aa8309b292`；它不包含当前语义对齐改动。
- 历史 CI 运行：[31265934582](https://github.com/mingheyuan/Cognitrade/actions/runs/31265934582)，仅证明当时基线。
- Finance chain and scoring contracts：138 项后端契约测试通过。
- Complete backend suite：509 项完整后端测试通过。
- Frontend lint/tests/build：13 个测试文件、33 项测试通过，lint 与生产构建通过。
- UZI real-provider smoke：22/22 个 direct fetcher 逐维完成，`contract_status=passed`，18 个维度当次可用，4 个维度按真实超时记录为 `data_gap`，未填充模拟数据。该 job 验证 provider 可用性与错误分类，不经过 `collect_symbol → pipeline.collect`，因此不作为完整 v3 root 实网证据；根编排由固定 checkout、上游完整测试与本地 root-router 集成测试验收。
- 敏感源证据：CI 显式关闭敏感来源且不配置 Cookie；`19_contests` 的雪球审计为 `configured_enabled=false`、`observation_present=false`、`source_mode=cognitrade_policy_disabled`、`policy_ok=true`。公开比赛来源可独立提供结果，不代表启用了雪球。
- 脱敏运行产物：GitHub Actions artifact `uzi-provider-smoke-542ea6ccc3b231d81d63634b266648aa8309b292`，保留 14 天，不含原始市场观察、Cookie 或 provider 原始错误正文。
- 当前 PR 新增固定上游 checkout：先按 relocation mapping 对声明的 vendor 生产树逐文件、逐字节比较，再将本地 vendor overlay 到该 checkout 运行上游测试；提交后以该次 Actions、完整后端/前端 job 与 real-provider artifact 作为最终证据。

## 11. 完成定义

只有同时满足以下条件才能将迁移清单中的 `not_equivalent`、`semantic`、`adapted` 或 `removed` 改为 `equivalent`：

1. 对应上游源码已固定并有文件/符号级映射。
2. 实现已进入 DomainPack/Agent OS 正式执行路径。
3. 上游差异测试覆盖成功、缺失、失败和恢复分支并通过。
4. 真实 provider smoke 证明执行链可用，或明确记录当次真实来源不可用而不伪造数据。
5. API/Web 能触发并读取完整产物。
6. 报告相关能力通过 HTML/PDF 视觉和并发验证。
7. 本文验收矩阵填写实际测试命令、产物路径和提交证据。

在全部条件满足前，项目不得声明“与 UZI 3.9.4 完全对齐”。

## 12. Agent OS 运行边界

- 已解析个股的完整研究由 `Uzi392FetcherRuntime.collect_symbol` 以整股为 lane 单位复现固定上游采集分支。单股 `stock_check/uzi_analyze_stock` 默认先执行 `_preflight_guards` 再走 `lib.pipeline.collect.collect`；`UZI_LEGACY=1` 进入 `legacy_forced`，preflight 或 pipeline 采集编排抛出异常才进入 `legacy_fallback`。`stock_compare/uzi_portfolio_batch` 对齐专用 `versus_runner/portfolio_runner`：始终逐股调用 v3，忽略根 CLI 的 `UZI_LEGACY`，异常时不重试 legacy。上游只有在 raw+synthesis 等完整报告 cache 可读时才复用；当前采集适配器只有 raw cache，不能伪装完整，因此把该股标为整股 route failure，从所有 Agent OS recovery/LLM 阶段排除，并由多股 composer 跳过。单个 provider/fetcher 异常由 `BaseFetcher` 转成 v3 error `DimResult`，不触发 legacy。`_uzi_execution` 只记录 lane 和脱敏后的异常类型。
- v3 lane 对所有 lite/medium/deep 都遍历完整 22-entry registry：wave 1 运行 `0_basic`；wave 2 以最多 6 个 spawn 子进程执行 17 个非依赖 fetcher（包括 `6_fund_holders`、`similar_stocks`）；wave 3 以单 worker 顺序执行依赖集合 `13_policy/3_macro/7_industry/9_futures`。上游默认单 fetcher 硬超时 120 秒、每波硬超时 300 秒，超时子进程会 terminate/kill；Agent OS 仅通过同一个 `ProcessJob.timeout_sec` 把可选 `similar_stocks` 收紧为 30 秒，核心维度仍保持上游限制。全局限制可分别由 `UZI_PIPELINE_FETCHER_TIMEOUT` 与 `UZI_PIPELINE_WAVE_TIMEOUT` 调整。mini-racer fetcher 进入串行组，禁用时 valuation 走 `main_safe`，其余返回缺失结果。每个 adapter 提取上游 `data`、规范化空值、校验并返回带 `_pipeline` 的 `DimResult`。lite 的 `fetchers_enabled` 仍为上游 7 个 expected/self-review 维度，但 ArtifactBoard `snapshot_types` 必须承载全 22 项结果，不能把 lite 误当成 v3 采集裁剪。
- legacy forced/fallback lane 使用 `run_real_test.collect_raw_data`：basic 始终刷新；随后是 profile-scoped numbered wave（最多 6 workers，300 秒 completion classification window）；最后并发执行 `fund_holders/similar_stocks` bonus wave（2 workers，180 秒 classification window）。这些窗口会标记 unfinished 结果，但 executor 中的 Python 线程不能被强制取消，因此不是整条运行的硬超时；legacy 保留 `run_fetcher` envelope、mini-racer lock/disable/force/crash-sentinel 语义。
- `raw_data` 是 lane 相关契约：v3 具有 `ticker/market/code/full/dimensions`，保留 `6_fund_holders` 维度，把 `fund_managers` 放到可选顶层，并因同名 spill 只在顶层保留 `similar_stocks`；legacy 具有 `ticker/market/fetched_at/dimensions`，没有 `6_fund_holders` 维度，两个 bonus 结果都在顶层。快照用 `_uzi_raw_result` 保存原始维度 envelope、`_uzi_root_fields` 保存 lane-specific 根字段，并用 `_uzi_top_level_fields.{fund_managers|similar_stocks}={present,value}` 严格区分 absent、`None` 与 `[]`；在没有 recovery 写入时，展示别名不得恢复进原生 data，`build_uzi_raw` 按原 lane 无损重建。若上游 suggested-fix/MX/DDGS/Playwright recovery 确实补到缺失证据，则新增字段与 `recovery` 元数据是显式变更，不再宣称 byte-identical raw。Snapshot schema 的 `source_trace` 和 Artifact `source_refs` 同时保留原始与恢复 provider lineage。ArtifactBoard 业务快照名保持 `fund_holders`；基金统计档位 lite/medium/deep 分别计算前 5/20/100 家。
- 对齐边界是“个股数据采集”，不是原样嵌入交互式 CLI：legacy forced/fallback 会复用 pinned 中文名解析与证券类型分类，确保 ETF/LOF/基金/可转债不会被当成个股评分；候选确认、ETF 持仓循环和命令行退出码不在服务端重放，由 Agent OS intake/专用基金能力承接。采集后的评分、Agent roleplay、ArtifactBoard 报告和渲染由 Agent OS 原生 UZI 阶段负责，因此后处理失败不会像根 `run.py` 那样重新执行一遍 legacy stage1/stage2。该边界必须在验收中显式披露，不能称为 CLI 全流程对象身份等价。
- resume 同样按 lane 保留上游语义：v3 只复用通过 `_is_resume_valid` 且带数值 `fetched_at`、仍在对应 fetcher `cache_ttl_sec` 内的维度，包括 `0_basic`；无时间戳旧缓存会重抓。单股请求的 `no_resume=true` 映射为 v3 `resume=False`。v3 不直接读取进程变量 `UZI_NO_RESUME`；versus/portfolio 与固定 runner 一样始终 `resume=True`，公开 API 拒绝给多股 intent 传 `no_resume`。legacy 的 `collect_raw_data` 自己保留 `UZI_NO_RESUME`、`0_basic` 和两个 bonus 始终刷新等上游规则。Agent OS 的 `requests` 默认 20 秒仍覆盖直接/legacy UZI 线程；v3 spawn worker 由上游进程级硬超时约束。请求级 Xueqiu gate 会显式传入 `19_contests` 子进程，不影响其他 DomainPack。
- 固定版完整 `generate_panel()` 不读取 profile，lite/medium/deep 实际都遍历 66 个 registry seat；`analysis_profile.py` 中 10/51/51 只作为陈旧声明元数据保留审计，不能用于截断生产 panel。独立 `/quick-scan` 命令仍按其命令契约显式选择 Top 10。
- `_uzi_execution.lane` 表示本次活跃编排器，不承诺每个复用维度都由同一 lane 首次生成：pinned v3 与 legacy 本来就能互相读取符合各自有效性检查的历史 dimension envelope。Artifact 中保留每个 dimension 自身的 envelope 与 `_pipeline` 元数据，不能仅凭 active lane 重写其来源。
- UZI 3.9.4 自审的两个字符串检查会在 JSON `null` 上触发 `NoneType is not iterable`；Agent OS 只在自审副本中恢复函数声明的空字符串默认值，原始证据、完整性检查与报告数据均不改写。
- UZI 3.9.4 DCF 生产者输出 `intrinsic_per_share`，而自审读取旧名 `intrinsic_value_per_share`；Agent OS 只在自审副本中提供同义键。真实值为 `None/0` 时原生 warning 继续保留，不生成替代估值。
