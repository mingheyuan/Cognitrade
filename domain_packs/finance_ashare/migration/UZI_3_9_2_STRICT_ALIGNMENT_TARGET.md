# UZI 3.9.2 严格对齐目标与验收矩阵

## 1. 文档状态

- 目标状态：完成；固定提交差异测试、全量回归与真实 provider smoke 均已由 GitHub Actions 验收
- 上游仓库：`https://github.com/wbh604/UZI-Skill`
- 唯一基准提交：`22e65f2e0cee2cb41cb40fdbe391c9600b7fe359`
- 上游版本：`3.9.2`
- 迁移边界：实现迁入 Finance DomainPack；生产环境不得依赖外部 UZI checkout、CLI、缓存目录或运行时
- 保留边界：Agent OS API/Web、ArtifactBoard、运行溯源与鉴权
- 公开方法边界：23 个 UZI 方法只能通过各自的 `finance.uzi.native.*` intent 触发；旧 `requested_methods`/`method_catalog` 入口已删除
- 扩展边界：仍由 Cognitrade 使用的非原生研究能力明确标记为 `cognitrade-extension-*`，不属于 UZI 等价声明，也不能作为 UZI Web 方法入口
- 验收原则：清单状态不是完成证据；只有上游差异测试、真实链路测试和产物审计共同通过后才能标记等价

## 2. 不可妥协规则

1. 相同输入、相同真实数据和相同显式假设下，字段形状、计算规则、命令语义、报告 DOM/CSS/JavaScript 必须与固定上游提交一致。
2. 允许差异仅限 Agent OS run/artifact ID、时间戳、ArtifactBoard 溯源和真实网络可用性。
3. 缺失数据保持缺失，不允许模拟市场、财务、持仓、新闻、来源或计算输入。
4. 仅允许 UZI 原生的定性推断兜底，且必须标记“基于公开信息推断，非一手数据”；数值和事实不得推断。
5. 敏感来源默认关闭，必须由用户显式启用并提供凭据；启用策略不得改变 UZI 的来源字段和结果结构。
6. 用户相关估值或交易假设必须显式选择并进入 ArtifactBoard；不得静默使用 `mid`。UZI 方法本身的固定常量保持原样。
7. `critical` 必须经过 `suggested_fix → 恢复 → 重算 → 自审` 循环清零；不设人为轮数上限，不允许绕过。
8. `warning` 允许生成报告，但必须使用 UZI 原生 warning banner；`completed_partial` 不能绕过 `critical` 门控。
9. HTML/PDF 使用同一份 UZI 原生可见报告；Cognitrade 元数据不得污染原生 DOM。

## 3. 基准资产与完整性锁

| 资产 | 上游路径 | 固定证据 | 目标 |
|---|---|---|---|
| 许可证 | `LICENSE` | SHA-256 `78bebb5232741327c31b0da4f5bbd570dc59ab3c3230582a4d1b4650238a7d58` | 保留 MIT notice 与来源提交 |
| 原生报告呈现层 | `skills/deep-analysis/assets/report-template.html` + 4 个 `lib/report` helper | v3.9.4 commit `0acf25122cabf493e7bc442faff62ceceb7f2f02`；模板 3615 行；SHA-256 `1984dbf7a5c07dc35e0bd553082c7fe2ab7dffa4c5eb2e23c942fcd4c98ad7c8` | 原样迁入并加逐文件哈希测试；分析方法仍固定在 3.9.2 |
| 来源注册表 | `skills/deep-analysis/scripts/lib/data_source_registry.py` | 73 个 source ID | 目标目录和运行注册表均为 73 项 |
| 命令目录 | `commands/*.md` | 20 个命令 | 20 个独立 Agent OS/API/Web 能力 |
| Fetcher | `skills/deep-analysis/scripts/fetch_*.py` | 22 个 fetcher | 调用链与结果契约逐项等价 |
| 方法 | 上游方法与命令实现 | 23 个实现单元 | 公式、字段、阈值和顺序逐项等价 |
| 自审 | `scripts/lib/self_review.py`、`review_stage_output.py` | `critical_count == 0` 才通过 | 不可绕过的运行状态机 |

## 4. 工作包 A：22 个 Fetcher

每一行必须同时满足：上游 provider 顺序一致、参数和日期范围一致、字段名称/类型/嵌套一致、来源标记一致、缓存/重试/兜底一致、空值语义一致、固定 replay 差异测试通过、至少一个真实 smoke 场景通过。

| ID | 上游实现 | DomainPack 目标 | 当前基线 | 完成证据 |
|---|---|---|---|---|
| `0_basic` | `fetch_basic.py` | `market_snapshot_skill` | source_parity | exact invocation/object identity test |
| `1_financials` | `fetch_financials.py` | `fundamental_snapshot_skill` | source_parity | exact invocation/object identity test |
| `2_kline` | `fetch_kline.py` | `uzi_kline_snapshot_skill` | source_parity | exact invocation/object identity test |
| `3_macro` | `fetch_macro.py` | `uzi_macro_snapshot_skill` | source_parity | exact invocation/object identity test |
| `4_peers` | `fetch_peers.py` | `uzi_peers_snapshot_skill` | source_parity | exact invocation/object identity test |
| `5_chain` | `fetch_chain.py` | `uzi_chain_snapshot_skill` | source_parity | exact invocation/object identity test |
| `6_research` | `fetch_research.py` | `uzi_research_snapshot_skill` | source_parity | exact invocation/object identity test |
| `7_industry` | `fetch_industry.py` | `uzi_industry_snapshot_skill` | source_parity | exact invocation/object identity test |
| `8_materials` | `fetch_materials.py` | `uzi_materials_snapshot_skill` | source_parity | exact invocation/object identity test |
| `9_futures` | `fetch_futures.py` | `uzi_futures_snapshot_skill` | source_parity | exact invocation/object identity test |
| `10_valuation` | `fetch_valuation.py` | `uzi_valuation_snapshot_skill` | source_parity | exact invocation/object identity test |
| `11_governance` | `fetch_governance.py` | `uzi_governance_snapshot_skill` | source_parity | exact invocation/object identity test |
| `12_capital_flow` | `fetch_capital_flow.py` | `capital_flow_snapshot_skill` | source_parity | exact invocation/object identity test |
| `13_policy` | `fetch_policy.py` | `policy_snapshot_skill` | source_parity | exact invocation/object identity test |
| `14_moat` | `fetch_moat.py` | `uzi_moat_snapshot_skill` | source_parity | exact invocation/object identity test |
| `15_events` | `fetch_events.py` | `events_snapshot_skill` | source_parity | exact invocation/object identity test |
| `16_lhb` | `fetch_lhb.py` | `lhb_snapshot_skill` | source_parity | exact invocation/object identity test |
| `17_sentiment` | `fetch_sentiment.py` | `sentiment_snapshot_skill` | source_parity | exact invocation/object identity test |
| `18_trap` | `fetch_trap_signals.py` | `uzi_trap_snapshot_skill` | source_parity | exact invocation/object identity test |
| `19_contests` | `fetch_contests.py` | `uzi_contests_snapshot_skill` | source_parity | exact invocation + sensitive-source gate test |
| `fund_holders` | `fetch_fund_holders.py` | `uzi_fund_holders_snapshot_skill` | source_parity | exact invocation/object identity test |
| `similar_stocks` | `fetch_similar_stocks.py` | `uzi_similar_stocks_snapshot_skill` | source_parity | exact invocation/object identity test |

### 4.1 来源注册表

- UZI 固定提交实际登记 73 个来源，而不是旧迁移清单中的 70 个。
- 当前缺失且必须补齐：`yahoo_equity_screener`、`yahoo_fundamentals_timeseries`、`yahoo_fx_chart`。
- 默认 provider 顺序严格保持 `akshare → efinance → tushare → baostock`；Direct HTTP 仅按上游显式 fallback 或环境覆盖进入。
- Tier 1/2/3、market、dimension、access、health、base URL 必须逐字段与上游注册表一致。
- “来源已登记”不等于“Fetcher 已调用”；每个来源必须在上游实际调用位置具有对应的本地执行或显式不可用记录。

## 5. 工作包 B：20 个命令

命令以独立 Agent OS 能力暴露，同时保留上游输入、前置检查、执行阶段、所需维度、方法调用与输出语义；不得用一个通用 intent 冒充多个命令。

| 命令 | 目标 | 当前基线 | 特别验收点 |
|---|---|---|---|
| `analyze-stock` | 完整股票分析 | source_parity | 完整 Stage 1/2/3/4 与报告门 |
| `quick-scan` | 独立快速扫描 | source_parity | 仅 1/2/10/18 维、10 席、Markdown、无 HTML |
| `panel-only` | 独立评审团 | source_parity | 上游席位和输出契约 |
| `scan-trap` | 杀猪盘扫描 | source_parity | 8 个信号与风险等级 |
| `dcf` | DCF | source_parity | 上游参数和输出 |
| `comps` | 可比公司 | source_parity | 上游 peers 输入与输出 |
| `lbo` | Quick LBO | source_parity | 用户显式交易假设 |
| `segmental-model` | 分部模型 | source_parity | 披露分部与输出结构 |
| `initiate` | 首次覆盖 | source_parity | 原生产物结构 |
| `earnings` | 财报解读 | source_parity | 原生产物结构 |
| `catalysts` | 催化剂日历 | source_parity | 有日期证据的事件 |
| `thesis` | 投资逻辑追踪 | source_parity | 历史运行/证据绑定 |
| `screen` | 量化筛选 | source_parity | 原生候选输入和排序语义 |
| `dd` | 尽调清单 | source_parity | 原生清单字段 |
| `ic-memo` | 投委会备忘录 | source_parity | 不替代人工决策 |
| `ai-readiness` | AI readiness | source_parity | 缺失保持缺失 |
| `earnings-preview` | 业绩预览 | source_parity | 预测明确标记假设 |
| `model-update` | 模型更新 | source_parity | 禁止 demo 更新 |
| `returns` | 收益归因 | source_parity | 缺失收益不得补零 |
| `rebalance` | 持仓再平衡 | source_parity | 原生费率须用户显式接受 |

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
- 相同 fixture 与假设的结构化输出必须深度相等；仅浮点项可采用在测试中逐字段声明的上游数值容差。
- provider gap 不得通过代理值、零值、默认中性分或替代公式隐藏。

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
3. 强制 Playwright 重试适用页面并读取网络结果。
4. Web/DDGS 与可信官方来源补全。
5. `MX_APIKEY` 已配置时执行 MX。
6. 仅对允许的定性维度使用显式标注的推断；不得生成数值事实。
7. 重算 Stage 2、再次自审，直到通过或真实来源耗尽。

## 8. 工作包 E：UZI 原生报告

- 原样 vendor 上游 v3.9.4 `report-template.html` 和产生可见 DOM/SVG 的 4 个 `lib/report` helper，逐文件固定 SHA-256；不得只同步模板而留下旧 renderer。
- 迁入 `assemble_report.py` 及其 `lib/report/` 渲染依赖所需的纯能力，输入适配到 ArtifactBoard，但不执行外部 UZI runtime。
- 保持上游 DOM、CSS、JavaScript、19 维卡片、panel、机构模型、友好层、warning banner 与打印样式。
- HTML 与 PDF 使用相同 HTML；PDF 不得另建白底或简化版布局。
- Cognitrade run ID、artifact ID、来源链放在响应 header/sidecar artifact；仅上游已有插槽允许进入可见报告。
- PDF 采用受控 Chromium 进程池、队列、超时和缓存；并发失败不得通过无限启动浏览器规避。
- 模板哈希、DOM selector、关键文本、页面截图和 PDF 页面渲染均进入回归测试。

## 9. 验收测试矩阵

| 编号 | 证明对象 | 必须提供的证据 | 状态 |
|---|---|---|---|
| A1 | 22 fetcher 完整性 | 上游/本地 ID 集合完全相等 | 已通过；`test_uzi_source_parity`，CI run `31265934582` |
| A2 | 73 sources 完整性 | 注册表逐字段深度相等 | 已通过；`test_uzi_source_parity`，CI run `31265934582` |
| A3 | fetcher 结果 | 22 组调用参数与返回对象身份差异测试 | 已通过；`UziFetcherInvocationParityTests`，CI run `31265934582` |
| A4 | provider 行为 | 可用、失败、超时、空响应、敏感源关闭的 smoke | 已通过；22/22 完成、18 available、4 data_gap、来源链与雪球关闭策略均通过 |
| B1 | 20 commands 完整性 | 命令 ID、schema、执行图、产物逐项等价 | 已通过；`test_uzi_392_commands`，CI run `31265934582` |
| B2 | quick-scan | 1/2/10/18、10 席、Markdown、无 HTML | 已通过；`test_uzi_quick_scan`，CI run `31265934582` |
| C1 | 23 methods 完整性 | ID/公式/字段/阈值/缺失分支差异测试 | 已通过；23 个上游函数直接输出比较，CI run `31265934582` |
| C2 | 显式假设 | API、Web、ArtifactBoard 往返及无 silent-mid 测试 | 已通过；非法/缺失 profile 与假设均拒绝，CI run `31265934582` |
| D1 | suggested_fix | 每种 issue 被执行并触发重算/自审 | 已通过；`test_uzi_strict_recovery`，CI run `31265934582` |
| D2 | critical 门控 | critical 永不生成 HTML/PDF，且不能被 completed_partial 绕过 | 已通过；API、持仓批次与导出三层门控，CI run `31265934582` |
| D3 | warning | 原生 banner 可见且报告成功 | 已通过；固定原生模板渲染测试，CI run `31265934582` |
| D4 | 无三轮上限 | 超过三轮仍继续，来源耗尽才阻断 | 已通过；`round_limit: null` 与 >3 轮测试，CI run `31265934582` |
| E1 | 模板完整性 | vendor 文件哈希等于上游固定值 | 已通过；SHA-256 固定测试，CI run `31265934582` |
| E2 | HTML 可见等价 | DOM/CSS/JS selector 与安全转义回归 | 已通过；固定模板精确渲染，CI run `31265934582` |
| E3 | PDF 可见等价 | 同 HTML 渲染和 PDF 头/页面回归 | 已实现；真实 Chromium smoke 已生成 26,814 字节 PDF |
| E4 | PDF 稳定性 | 并发 10 次全部成功且资源回收 | 已验证；10/10 成功，2 个 render slot，缓存命中一致 |
| F1 | 零外部依赖 | 生产导入/运行不读取 UZI checkout | 已通过；vendored in-process runtime 边界测试，CI run `31265934582` |
| F2 | 完成审计 | 本文每一行具有代码、测试和运行证据 | 已完成；见下方 GitHub Actions 与脱敏 Artifact 证据 |

## 10. GitHub Actions 最终证据

- 验收提交：`542ea6ccc3b231d81d63634b266648aa8309b292`。
- CI 运行：[31265934582](https://github.com/mingheyuan/Cognitrade/actions/runs/31265934582)，四个 job 全部成功。
- Finance chain and scoring contracts：138 项后端契约测试通过。
- Complete backend suite：509 项完整后端测试通过。
- Frontend lint/tests/build：13 个测试文件、33 项测试通过，lint 与生产构建通过。
- UZI real-provider smoke：22/22 维完成，`contract_status=passed`，18 个维度当次可用，4 个维度按真实超时记录为 `data_gap`，未填充模拟数据。
- 敏感源证据：CI 显式关闭敏感来源且不配置 Cookie；`19_contests` 的雪球审计为 `configured_enabled=false`、`observation_present=false`、`source_mode=cognitrade_policy_disabled`、`policy_ok=true`。公开比赛来源可独立提供结果，不代表启用了雪球。
- 脱敏运行产物：GitHub Actions artifact `uzi-provider-smoke-542ea6ccc3b231d81d63634b266648aa8309b292`，保留 14 天，不含原始市场观察、Cookie 或 provider 原始错误正文。
- 后端测试执行边界：按项目约定仅由 GitHub Actions 执行；本地只做静态语法、diff 与文档检查。

## 11. 完成定义

只有同时满足以下条件才能将迁移清单中的 `not_equivalent`、`semantic`、`adapted` 或 `removed` 改为 `equivalent`：

1. 对应上游源码已固定并有文件/符号级映射。
2. 实现已进入 DomainPack/Agent OS 正式执行路径。
3. 上游差异测试覆盖成功、缺失、失败和恢复分支并通过。
4. 真实 provider smoke 证明执行链可用，或明确记录当次真实来源不可用而不伪造数据。
5. API/Web 能触发并读取完整产物。
6. 报告相关能力通过 HTML/PDF 视觉和并发验证。
7. 本文验收矩阵填写实际测试命令、产物路径和提交证据。

在全部条件满足前，项目不得声明“与 UZI 3.9.2 完全对齐”。
