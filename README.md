# Cognitrade

Cognitrade 是以 Agent OS、DomainPack 和 ArtifactBoard 为核心的 A 股研究 Web
应用。当前产品入口是 Web 与版本化 HTTP API；历史 CLI 和旧编排系统已删除。

## 功能

- 自然语言研究入口：个股体检、多股比较、股票筛选、行业研究与 Thesis Review。
- 可追踪运行：Turn/Task、ExecutionGraph、事件流、子 Agent、Artifact、指标和重跑。
- Finance Workspace：证券解析与数据库优先的 Point-in-Time 行情数据中心；覆盖、快照/证据、采集任务、Bundle 修订、归档订阅和 provider 能力均可审计。
- 策略工作台：`/strategies` 从零创建或复制系统模板，编辑、发布不可变版本、绑定基金、生成日报、预演四态信号、回测、验证和晋级；旧 `/methodology` 与 `/backtesting` 前端入口已删除。
- 报告与信号：基金 Bundle 固定十大持仓、顺序和披露权重，直接启动完整 Agent 的 canonical UZI 子报告，不依赖旧基金运行。Lite / Medium / Deep 只消费冻结 Bundle/Manifest，报告阶段不联网补数。新 V2 策略读取原生评级与综合作战计划；旧 V1 复合评分继续兼容，历史信号不改写。
- 基金回测：`/strategy-backtests/**` 是唯一公开回测入口，仅接受已发布的不可变策略版本。新 V2 默认 `holdings_portfolio / report_conditions_v1`，按报告条件收盘判断、次日真实开盘成交，不叠加 MA5/MA20 或基金综合评分；旧 V1 保留 `fund_only` 和旧均线模式。预检仅使用已落库行情，缺失返回补采范围。历史折叠不删除审计记录。
- 模拟交易：VeighNa PaperAccount 到价撮合，服务端现金/持仓账本、限价挂单、资金冻结、撤单、费用、T+1 和幂等订单；只有人工确认的 Verified 版本可晋级 Promoted 并通过策略链路下单。
- DomainPack：`finance_ashare` 与 `legal_contract` 通过声明式能力、工具、Gate 和报告合同接入。
- UZI 报告：研究工作台可从 ArtifactBoard 直接打开与上游 v3.9.4 分析及呈现层逐文件对齐的原生 HTML，或下载同源 PDF；旧 UZI 文件数据缓存不迁移、不回退、不作为新报告的事实源。
- 行情数据中心基金入口：基金只作为“前十大持仓股票分析”父入口；从冻结公开披露中固定解析精确 10 只 A 股，再为每只股票准备完整 22 槽 Bundle 和 canonical UZI 报告。支持失败节点重试、完整重跑、暂停/恢复、历史补跑与 late revision；同一股票同一日期跨基金复用 canonical UZI Artifact。ETF 联接只使用已取得的穿透持仓，不推断缺失数据。
- 治理：Bearer 鉴权、作用域隔离、高风险审批、质量 Gate、来源引用和分层记忆。

## 当前链路：冻结数据 → 原生报告 → 条件回放

数据中心负责证据，原生 UZI 负责研判，回测负责执行与核算。本轮仍固定 UZI 3.9.4，
新增 StrategyDocument **V2** 不等于升级上游或改写历史 V1。新回放仅使用原始评级许可与
`dashboard.battle_plan`，按原披露权重的 50% 首批预算、费用、整手及 T+1 执行，不自动加仓。
已成交仓位锁定建仓报告的退出条件。仅支持人民币 A 股未复权价格；口径或公司行动证明
不足时明确拒绝准确回放。V2 暂不接纸面委托，不会落入旧执行规则。

实现契约、使用步骤、迁移和限制见 [《UZI 冻结报告与条件回放 V2》](./docs/uzi-report-replay-v2.md)。

真实执行记录见 [《2026-09-06 E2E 验收》](./docs/uzi-report-replay-e2e-20260906.md)：
采集和冻结已验证，但样本股票报告仍受输入缺口阻断，不代表完整报告或成交链路已通过。
已有数据库先备份，再运行 Alembic `upgrade head`（当前 `20260906_0006`）；旧信号新增列保持 NULL。
数据可冻结、报告输入预检、最终原生自审及实际成交分别验收，不以全现金或降门禁制造通过。

## 2.0 初版升级回顾：可审计的策略回测闭环

以下保留 1.0 → 2.0 初版（StrategyDocument V1）的演进记录；当前新建 V2 的行为以上节为准。

1.0 已有独立的 A 股手工模拟盘，支持服务端现金/持仓账本、FIFO、T+1、费用、
整手校验和幂等订单；它不是一个版本化策略回测系统。2.0 在保留这条能力的基础上，
新增了从研究证据到策略模拟执行的完整治理链：

```text
已发布的不可变策略版本
  + point-in-time 基金日报 / canonical UZI 持仓报告
  -> 同版本四态信号（ALLOW_BUY / WATCH / AVOID / BLOCKED）
  -> “检查并启动回测”内联预检
  -> Hikyuu 技术事件 + Cognitrade 确定性成交/费用账本
  -> 指标、基准、逐笔 lineage 与内容哈希
  -> 机器验证 + 人工确认 Verified
  -> 人工晋级 Promoted
  -> VeighNa 模拟交易（当前策略委托限 holdings_portfolio A 股持仓）
```

| 能力 | v1.0 | 2.0 |
| --- | --- | --- |
| 策略定义 | 没有独立策略业务域 | 草稿、发布不可变版本、版本 diff、归档和审计事件 |
| 回测 | 没有受治理的策略回测 | 单一基金回测入口；`fund_only` 默认，持仓组合为高级模式，支持异步运行、预检、取消和重试 |
| 数据时点 | 模拟下单时取服务端最新行情 | 日报、信号、行情/NAV 和持仓按 point-in-time 固化，禁止前视 |
| 执行模型 | A 股手工模拟订单即时成交 | Hikyuu 技术事件 + 基金/A 股确定性账本，完整计费和 T+1/确认日约束 |
| 可追溯性 | 订单和账户流水 | 每笔成交关联策略版本、内容哈希、信号、日报及市场数据哈希 |
| 上线门槛 | 手工模拟盘可直接下单 | Published → 回测 → 机器检查 → 人工 Verified → 人工 Promoted |

回测输出包括总收益、年化收益、最大回撤、Sharpe、换手率和总费用，并提供基金买入
持有、基金类别指数、期初披露持仓静态组合、沪深 300 或自定义指数基准。机器验证会
检查无前视、lineage 完整性、确定性复跑、样本/信号覆盖、费用完整性和绩效阈值；
机器通过后仍不能自动晋级。

同一租户、用户与输入快照哈希的 `queued` / `running` 请求，在单个回测服务实例内
会复用已有任务，防止连续点击产生并发副本；这不是跨进程唯一性保证。已完成任务仍可
再次运行以验证确定性，失败或取消任务也可重试；这些运行及其父子关系都作为原始审计
记录保留。

完整差异、两种回测模式、费用/基准、数据库与 API 变化、升级步骤和能力边界见
[《Cognitrade 1.0 → 2.0 中文升级报告》](./UPGRADE_REPORT_1.0_TO_2.0_CN.md)。
UZI 对齐细节见 [《Cognitrade 2.0 中文版本说明》](./RELEASE_NOTES_2.0_CN.md)。

## 2.0 数据中心：先采集、补采、确认冻结，再生成报告

正式研究数据已从 UZI 报告运行中抽离。SQLAlchemy 业务库是唯一事实源，
数据准备和报告执行是两个独立阶段：

当前生产基线固定为 UZI-Skill `3.9.4` / `b3665e85b8666477038e11157d8e8feb0e006b76`。
新采集只写 `uzi-3.9.4-b3665e8-v1`；升级前的 `uzi-3.9.4-1b5cdcb-v1`
Bundle 仍可只读物化和冻结 report manifest，但不会再被新任务写入。

```text
显式采集 Job / 显式启用的归档订阅
  -> 统一证券身份解析
  -> UZI 原生 provider 采集 22 维并按日写入可修订 Snapshot 草稿
  -> UZI Stage 1：原生 provider → MX/DDGS → Playwright 自动兜底
  -> 完整数据任务：研究原文发现 → 发布时间/历史版本核验 → 正文/PDF 按 hash 归档
  -> 用户按需定向重试缺口，复用其他合格数据
  -> 股票生成精确冻结候选；基金生成 1 个父槽 + 10 只 A 股 × 22 槽的组合候选
  -> 用户核对 proposal hash 与缺口披露后，显式确认发布不可变日级 Bundle
  -> 冻结单股/组合 report manifest
  -> Lite / Medium / Deep / 持仓子报告 / 日报只读执行
```

这里的 22 是“20 个编号 collector（`0_basic`–`19_contests`）+ 2 个采集
collector（`6_fund_holders`、`similar_stocks`）”。`20_valuation_models`、
`21_research_workflow`、`22_deep_methods` 是基于 Bundle 计算的 3 个报告派生结果，
不计入采集完整度。数量、顺序、required fields、freshness 和合法空值规则
均由 `GET /api/v2/finance/market-data/contract` 返回，客户端不应硬编码 `22`。

所有采集入口共享同一个类型化身份解析器，客户端可先通过
`POST /api/v2/finance/market-data/instruments/resolve` 预检。例如 `020464`
解析为 `otc_fund / CN_FUND`，不得
再按深市股票进入 22 槽采集。`CN_FUND` 的契约只有一个
`fund_top_holdings` 槽；该父快照/Bundle 只负责定义报告时点可见的
精确 10 只 A 股及披露权重，不是一份“基金 22 槽”报告。
行情数据中心、Bundle/report-manifest 以及公开持仓报告 API 均固定为 10。
已有 `StrategyDocument` 仍可能保存旧版 5–20 配置，但严格父 Bundle 执行只接受 10；
这些旧文档需要在再次执行前显式迁移，系统不会静默改写其历史内容。

股票可用性与报告门严格跟随 UZI：provider 返回的原生 `DimResult`
决定该维是否有可用数据；本地字段、证据等级、过期与冲突检查独立披露，
不再把 UZI 认为有数据的维度改判为原生“不可用”。PIT 合法性仍是独立输入安全门槛。
`report_generatable` 表示 22 个结果槽均已有原生结果或明确缺口，可交给完整 Agent 恢复流程；
采集候选中的 `report_ready` 仅为数据阶段摘要；正式 Manifest 按实际档位生成输入诊断，
数据和研究引用缺口交给 Agent 分析，不额外作为启动硬门禁。六维定性研究按产出的证据条目检查，
同一 URL 可支持不同事实，不要求两个不同 URL；未知项须明确披露，不得编造引文。
冻结 hash、权限、撤销、执行版本及 PIT 检查仍可阻止不安全输入。最终是否能输出 HTML/PDF，仍由
UZI 完整 Agent 流程结束后的原生 self-review 决定：有 critical 阻断，
warning 允许出报告并披露。超时、未配置、无权限、解析失败或开放搜索
无命中会如实落为 `data_gap`，不会被填成中性分。

外部采集默认关闭：打开页面、访问任何 GET、查询 coverage 或创建报告都不会隐式
联网。用户可创建采集 Job，或同时显式开启单标的订阅和全局
`COGNITRADE_MARKET_DATA_AUTO_COLLECTION_ENABLED=true`。既有且合格的快照默认跳过；
这两个 opt-in 开关对股票和基金归档都生效。任务按标的 × 维度持久化
checkpoint；基金父快照一旦成功落库，重试会复用它并只补尚未就绪的
持仓股票槽，不会因某个子任务失败重采整个基金。
人工 Job 无论股票还是基金都不会自动发布 Bundle：provider 采集完成后，用户可先按需
启动 Agent 缺口补采，也可以直接审阅冻结候选；只有提交候选的精确
`proposal_hash`（降级候选还需明确确认披露）后才会冻结。订阅 Job 的双 opt-in
自动发布能力保持不变。Agent 补采只追加新 Snapshot，不改写既有行；已冻结 Job
不能重试或补采，需要新建 Job 形成新修订。
采集、Agent 与基金父任务的持久租约会定期续约，并以 owner token 阻止过期 worker
覆盖新 worker；Bundle 发布、revision 分配与 Snapshot 撤销同样由数据库锁协调。
后台每 10 秒扫描一次失联任务；心跳租约最长 3 分钟，因此进程异常退出后无需再次
重启服务，任务会自动回到可执行状态并从持久化 checkpoint 继续。
兼容 `GET /api/v2/finance/market-data/{symbol}` 中的 `refresh=true` 不再刷新上游；
`POST /api/v2/finance/market-data/report-manifests` 只冻结输入，不直接启动报告 run。
公开证券搜索与报价 GET 同样只读数据库；`refresh=true` 不会采集。只有显式提交
模拟订单或显式请求挂单重新撮合时，服务端才会走独立的实时成交报价路径，这条路径
不写入研究 Bundle，也不能被正式报告消费。

历史 PIT 不用当前值伪造。股票历史 Job 按字段/来源能力处理：行情、财报、K 线、估值、
宏观、研报、资金流、政策、事件、龙虎榜和日期化舆情等来源直接接收
`as_of_date/cutoff_at`，在 provider 侧限定查询并保存逐来源证明；实时热度、比赛排名等
没有历史档案的字段不会回填。每个槽明确区分 `available`、`partial`、
`verified_empty`、`retryable_failure` 与 `unavailable`：部分恢复保留 UZI 评分资格并披露
可选缺口，查证为空是合法观测，上游临时失败可定向重试，只有真正没有历史档案的字段
才记为不可恢复缺口；评分是否可用由原生 UZI 决定，本地不擅自改变权重。只要 22 个槽
都已有原生结果或明确缺口记录，就可形成 `quality=degraded` 的冻结候选；用户可先让
UZI Agent 尝试恢复并重新生成候选，也可确认缺口披露后冻结；`report_ready`、`report_generatable`
与 `audit_ready` 始终分别展示。

历史财报在冻结前会按 UZI 原生计算合同规范化：营业收入、净利润历史序列统一为“亿元”
数值，ROE 等比例序列统一为百分数值。上游页面的 `6.10亿`、`2991.87万`、`7.48%`
等展示字符串不会直接进入评分函数，避免在增长率计算中触发字符串减法并被误诊断为
数据缺口。不再以 `_excluded_from_scoring` 等本地历史标记把原生 critical 降为 warning；
PIT 合法性与原生交付门分别检查。

基金历史 Job 会优先复用已落库、且能证明在 cutoff 前可见的快照/Bundle；缺失时只走
历史持仓专用重建路径。该路径按目标日筛选官方定期报告，实际下载并哈希 PDF，
再把归档表中的报告期、代码、名称和权重逐项与官方文件核对。受支持的直接持股
权益基金需要一条持仓证据；单目标 ETF 联接基金需要父基金目标配置和目标 ETF
持仓两条证据，全部满足
`published_at <= cutoff` 才以 B 级快照落库。真实 `observed_at` 始终保留，绝不把
当前页面倒填到历史日；临时网络/PDF 故障可重试，无匹配公告或证据不一致则明确失败。
普通多目标 FOF 的历史穿透尚不在该适配器覆盖范围内。
完整方案与已知边界见
[《Point-in-Time 数据中心实施方案》](./plan/market-data-report-decoupling-plan.md)及
[《历史 PIT 误降级修复计划与验收记录》](./plan/historical-pit-recovery-fix-plan.md)。
Agent OS 正式研究可用 ISO 日期约束 `analysis_as_of_date=YYYY-MM-DD` 精确选择历史
Bundle；未解决槽、无 Bundle 或 cutoff 晚于评估时点仍会失败，安全降级槽则随
Manifest 冻结并进入报告数据缺口区，绝不回退当前值，也不自行把维度权重置零。
报告的普通节点重试继续绑定原 attempt 的不可变 Manifest。补采完成并确认冻结后，可在
日报任务点击“使用已补采数据重试”，或显式调用
`POST /api/v2/finance/report-jobs/{id}/retry`，传入
`{"symbols":["688012"],"refresh_inputs":true,"confirmed":true}`。
这会在排队前固定选定股票的新 Manifest；未选股票的 Bundle、证据集合及基金持仓保持
原值，输入/版本匹配的合格结果继续复用。未提供 `symbols` 时仅选择失败股票。
没有新输入返回明确 409；普通分析重试、输入修订和 `full_rerun` 不再混为同一操作。
恢复同一 attempt 不会重新选择“最新”。运行代码/模型已升级时，应发布新策略版本并创建
新报告，不能将新版执行标为旧版重放。

`GET /api/v2/finance/report-jobs/{id}/repair-plan` 提供逐股补采清单、历史能力限制及
可直接提交的数据中心 `collection_request`；读取不会启动采集。显式补采只刷新选定维度，
不重采基金持仓，完成后仍需确认冻结。旧“查无记录”快照若未保留原生响应，补缺时会
重新获取有历史能力的来源；不会凭零条标记编造原生字段，也不会改写已冻结快照。
引用不足不再等同于输入缺失：有冻结来源时定向修复研究输出，无来源时才转补采；
引用不完整的组不进入成功缓存，合格组继续复用。原生最终自审要求不变。
历史研报补采按股票裸代码和截止日查询，保留原始文档 ID、发布日期与 PDF 链接；
不会把当前滚动盈利预测当作历史研报事实。旧适配器产生的零条结果会在显式补缺时重新
核验。基金持有人接口仅有“持仓截止日期”时，不能作为历史公开披露日期；缺少历史版本
证明会明确披露，不能判成“历史无持仓”。研报目录获取也不等同于正文归档完成。
基金报告冻结时，10 个股票 Bundle 之外还会把父基金 Bundle ID、
Bundle manifest hash 以及父快照 hash 写入 report manifest 依赖和最终 hash；
父披露变更或失效时，原报告输入不会被静默复用。每个持仓子 Agent 启动前仍会重验父
Manifest hash、Bundle 引用和基金父依赖，但只物化当前股票的 22 个 Snapshot，
避免把十股 Manifest 完整扫描十次；这只是校验粒度优化，不放宽撤销、冲突或完整性门禁。
新 Manifest 还固定证据 ID、原文 hash、引用关系及实际模型/提示词/投影版本。
完整输入只写一次，批次状态保存引用与 hash；进度刷新不反复序列化全部原文。
升级环境需在备份后
显式执行 Alembic `20260820_0003_market_data_point_in_time.py` 和
`20260820_0004_fund_holding_inputs.py`；API 进程启动不会隐式改库。
仅对早期 `create_all`/auto-bootstrap 生成、没有 `alembic_version`，且经逐表、
逐列和约束对比确认与 0003 完全一致的旧本地库，才可在备份后先执行
`alembic stamp 20260820_0003`，再 `alembic upgrade head`。普通已版本化数据库禁止
盲目 `stamp`；生产部署仍必须使用显式“备份 → migrate”流程。

## 快速部署

完整部署脚本会检查/安装 Docker、初始化 `.env`、构建镜像，显式执行“迁移前备份 → Alembic → 旧对象只读索引 → 工作区迁移”，再启动 API 与前端：

```bash
./scripts/deploy.sh --yes
```

常用命令：

```bash
./scripts/deploy.sh status
./scripts/deploy.sh token
./scripts/deploy.sh logs all
./scripts/deploy.sh runtime-check
./scripts/deploy.sh migrate
./scripts/deploy.sh restart
./scripts/deploy.sh down
```

完整部署会安装 API、数据源、Playwright Chromium、VeighNa PaperAccount 和
Hikyuu，不把模拟盘或回测留成运行时可选项。镜像构建阶段会先执行一次无网络运行环境
检查；`deploy`、`start`、`restart` 完成后还会以实际容器用户再次检查 Python 依赖图、
API 导入、Chromium、VeighNa/Hikyuu 原生模块、策略数据库迁移状态以及对象存储读写权限。任一关键能力
缺失都会让命令返回非零状态。

部署镜像内可显式核验 UZI 3.9.4 的 22 类真实数据链；该独立 smoke 检查不会在启动时自动执行，
数据源无返回会记录为 `data_gap`，不会生成模拟数据：

```bash
COGNITRADE_PROVIDER_SMOKE_SYMBOL=600519 \
COGNITRADE_PROVIDER_SMOKE_SECTOR=白酒 \
./scripts/deploy.sh provider-smoke
```

可用 `COGNITRADE_PROVIDER_SMOKE_DIMENSIONS=market,kline,events` 只检查指定维度。
来源目录与 UZI 3.9.4 固定提交逐项对齐，共 73 个候选来源；“已登记”不等于本次
“已取到”，运行结果以各 Snapshot 的 `source_trace` 为准。雪球组合来源仍需同时显式
开启敏感来源、加入 `xueqiu` allowlist 并配置 Cookie；淘股吧、同花顺模拟盘等公开比赛
证据可以独立降级运行。
该 smoke 全量检查固定串行执行，避免多个 AkShare 路径并发初始化其进程级
JavaScript 运行时；数据中心的显式采集 Job 是独立路径，逻辑任务默认并发 5。

默认访问地址是 `http://127.0.0.1:8080`。浏览器 Token 可通过
`./scripts/deploy.sh token` 获取。

单独升级已有环境时显式执行：

```bash
./scripts/deploy.sh migrate
```

迁移先在 `workspace/backups/strategy_migrations/` 生成一致性 SQLite 与工作区索引备份，
再执行 Alembic、旧 Artifact/回测引用索引及 Run 状态规范化。`deploy` 命令显式调用这条
链；普通 `start`、`restart` 和 API 进程启动不会隐式改库。

## 本地开发

后端：

```bash
python3.11 -m venv .venv
.venv/bin/pip install -r requirements.txt
.venv/bin/python -m playwright install chromium
COGNITRADE_API_ALLOW_UNAUTHENTICATED=true \
COGNITRADE_API_HOST=127.0.0.1 \
.venv/bin/python -m uvicorn research_system.api.app:app --host 127.0.0.1 --port 8000
```

首次安装会下载 VeighNa、Hikyuu 及其量化/绘图依赖，体积明显大于纯 API
依赖。VeighNa 的 headless 撮合状态固定写入
`<workspace>/paper_trading/veighna_runtime/.vntrader`；基金回测的 Hikyuu 技术信号在受控
子进程和 `<strategy_object_store>/.hikyuu_runtime` 隔离目录运行。两者都不会依赖运行
用户的主目录，Hikyuu 也不会自动下载远程策略仓库。旧 `<workspace>/backtests/*.json`
仅作为只读历史文件保留，不再由 API 生成或列出。

前端：

```bash
cd frontend
npm install
npm run dev
```

Vite 开发环境默认连接 `http://127.0.0.1:8000/api/v1`。也可以在 Web 顶栏配置
API 地址和 Bearer Token；Token 只保存在当前标签页的 `sessionStorage`。

## 必要配置

部署模式必须设置：

```dotenv
COGNITRADE_API_TOKEN=<high-entropy-token>
```

策略业务域本地单实例默认使用 SQLite；生产多实例使用 PostgreSQL，并给对象存储配置所有
API/worker 都可访问的持久化 POSIX 路径：

```dotenv
COGNITRADE_DATABASE_URL=postgresql+psycopg://cognitrade:<password>@<host>:5432/cognitrade
COGNITRADE_DATABASE_AUTO_BOOTSTRAP=false
COGNITRADE_OBJECT_STORE_ROOT=/mnt/cognitrade/strategy_objects
```

Compose 的默认 SQLite 与对象目录位于 `cognitrade_workspace` 命名卷；外部 PostgreSQL
需要由平台执行快照/备份策略，外部对象目录也必须显式挂载进容器。生产预检会拒绝未迁移
数据库、不可写对象目录或 `COGNITRADE_DATABASE_AUTO_BOOTSTRAP=true`。

模型能力使用官方 Python Codex SDK（`openai-codex==0.154.0`，随包运行时同版本）。
旧 API-key Provider 已删除；`OPENAI_API_KEY`、`OPENAI_BASE_URL`、`OPENAI_MODEL`
不再用于生成模型，不会在 Codex 失败时切回旧 API。配置如下：

```dotenv
PROVIDER_NAME=codex
CODEX_MODEL=gpt-5.6-luna
CODEX_REASONING_EFFORT=low
UZI_LLM_REQUEST_TIMEOUT_SECONDS=480
UZI_LLM_MAX_CONCURRENCY=2
UZI_LLM_TRANSIENT_RETRIES=2
UZI_LLM_RETRY_BASE_SECONDS=2
UZI_LLM_RETRY_MAX_SECONDS=60
```

先在实际运行后端的环境中完成 Codex 的 ChatGPT 登录。宿主机登录不会自动传入 Docker；
容器凭据由 Codex 管理并保存在独立 `cognitrade_codex` 卷，不打进镜像、不复制桌面 token：

```bash
# 新镜像构建后，用同一个非特权服务用户完成设备登录（按终端提示操作）
docker compose run --rm --no-deps api python scripts/codex_login.py
docker compose run --rm --no-deps api python scripts/codex_login.py --status
# 显式真实模型探针；输出文件必须是未使用过的新路径
docker compose run --rm --no-deps api python scripts/probe_codex_provider.py \
  --allow-model-call --output workspace/reports/codex-sdk-first-probe.json
```

本地直接运行时将上述 `docker compose run --rm --no-deps api python` 换成 `.venv/bin/python`。
`CODEX_MODEL` 必须是该登录账户实际有权使用的模型，探针会检查模型列表及真实返回。
SDK 不是旧 Responses API 的参数别名：不传 `temperature`、`service_tier` 或 HTTP 存储开关；
`CODEX_REASONING_EFFORT` 是默认值，原有 UZI 阶段/档位显式推理强度优先，并记录每次实际值。
会话设为 ephemeral，Codex 自身仍管理认证与运行日志。每次调用使用独立会话、禁用浏览器、
搜索、Shell、MCP、个人 Skills 和记忆；业务工具仍由 Agent OS 校验与执行。结构化结果保留
原 Pydantic 校验；开放字典使用 JSON 提示输出，不能强行关闭字典键而丢掉维度。

六维定性研究通过受控动态工具读取本次 Manifest 已物化的资料：先分页查看目录，再检索、
按字符位置分段读取（单次最多 6,000 字符），不再把所有原文拼成一次巨型请求。
这些工具只能读取当前股票的固定输入，不能联网、读取任意路径或写文件；正文按内容 hash
寻址，缓存绑定完整资料集，原生研究问题及输出校验不变。宿主记录工具调用与实际读取范围，
不会把“资料已归档”表述为“模型已通读全文”。这只修复输入方式，不保证模型内容自审通过。
实现和验收范围见 [冻结资料读取修复方案](plan/uzi-frozen-reader-repair-20260914.md)。

新策略/Manifest 固定 SDK、运行时、适配器 hash、模型及推理档位，旧报告输入不原地改写。
Codex 不提供 Embedding，本链路显式使用词法检索，不用哈希向量冒充语义向量。
基础 SDK 探针通过不代表 medium 报告自审或真实回测已通过，须分别查看 E2E 验收工件。

UZI 报告不再给所有请求套同一个 480 秒上限：Lite、Medium、Deep 的结构化阶段
分别使用 240、600、900 秒；内容较短的四个买入区间始终最多使用 300 秒。
`UZI_LLM_REQUEST_TIMEOUT_SECONDS` 仅作为未归属档位的兼容回退。`similar_stocks`
在原生 UZI 中是 bonus fetcher，但在数据中心契约中是 22 个原始 collector 之一。
直接/legacy 路径的单次 HTTP 默认 5 秒，v3 spawn 路径由上游进程调度器执行
30 秒硬终止；当前日超时会如实记录为可重试 `data_gap` 并阻止严格 Bundle 发布；
历史查询穷尽后保留具体缺口披露，不伪造同行数据或自行排除评分。

十大持仓批次的外层等待同样按档位区分：Lite 60 分钟、Medium 120 分钟、Deep 180 分钟；
多日历史补跑仍至少使用独立的 6 小时单日批次预算。外层超时只负责终止真正失控的批次，
不会再用固定 30 分钟提前取消仍在正常执行的 Deep 任务。

正式的 Lite、Medium、Deep 个股分析统一执行 UZI `SKILL.md` 的完整 Agent 工作流：
20 个编号维度逐项审阅、66 席 persona 角色评审、宏观/政策、行业/事件、原料/期货三组
六维定性研究，以及机械自审通过后才允许报告交付。档位仍控制数据预算、推理强度、
机构方法和 Deep 独有多空辩论；“快速模式”只由独立 `quick_scan` 意图进入，不会再把
正式 Lite/Medium 报告静默降级成规则引擎骨架。

历史报告不会用当前网页补造目标日证据。六类定性研究的输入引用缺口作为 Agent 提示及
手动数据中心补采清单，不在报告阶段联网，也不因输入 URL 数量不足提前拒绝分析。
未知事实保留缺口；最终原生 critical 不会被自动改成 warning。
仅有旧日期 URL、当前内容 hash 或报告期不足以证明历史版本。成功回溯生成的 HTML
会披露报告截止日及实际生成日期，不冒充当日实际发布的报告。

新输入使用 `uzi-dim-result-v3`，保留采集器实际返回的 `data/error/_timeout/source`，
不因本地可用性标签清空原始结果，也不凭标签伪造原生错误字段。旧 Manifest 保留 v1/v2
投影及原输入 hash；旧任务的研究预检失败可在读取时重新解释，但不会原地更换执行版本。
已经固定旧模型/提示词/投影版本的任务须使用对应运行环境，或新建报告修订。
本次对齐范围与验证结果见 [预检修复记录](docs/uzi-native-preflight-alignment.md)。

完整 Agent 阶段使用冻结快照并按 `UZI_LLM_MAX_CONCURRENCY` 调度：角色评审与六维研究
同波并行，维度评论按 5 维一组并发，四类买入区间并发。失败恢复刻意优于原生 CLI 的
整轮重跑：角色评审按分组、六维研究按分组、维度评论按批次、买入区间按派系分别缓存；
机械校验失败时只清除并重试受影响阶段，已经验证的真实证据和模型输出继续复用。
角色评审的市场身份以 UZI root 的 `full/market/code` 为准，避免无后缀 `ticker` 把 A 股
误识别为美股；有冻结证据的 Agent 覆盖会同步重算 `consensus_valid/hollow_*`。恢复循环
按问题类别、维度和正文判重，证据描述变化不会再触发无限重跑；每轮完整 critical 诊断
保存在 `recovery_history.critical_issues`。角色票的证据校验覆盖 headline、reasoning、comment
与 verdict；`skip/insufficient_evidence` 只接受明确弃权或缺口说明。分组名允许省略展示用中文
后缀，但 investor membership 仍必须逐席精确匹配，避免把格式差异误报为整组缺失。

日报重试/恢复开始时会清空本次日期上一 attempt 的错误和完成时间；历史报告集与 attempt
仍完整保留供审计，但旧超时不会继续污染新 attempt 的状态、错误摘要或前端提示。

未完成 Codex 登录时仍可运行确定性数据能力，但需要模型的阶段会明确报告登录缺失，
不能把降级答复当作原生报告成功。`COGNITRADE_API_ALLOW_UNAUTHENTICATED=true` 只允许
回环地址的隔离本地开发，生产部署脚本始终启用认证。

可选真实数据源配置：

```dotenv
TUSHARE_TOKEN=<runtime-secret>
MX_APIKEY=<runtime-secret>
COGNITRADE_MARKET_DATA_AUTO_COLLECTION_ENABLED=false
COGNITRADE_MARKET_DATA_COLLECTION_CONCURRENCY=5
COGNITRADE_FUND_HOLDING_REPORT_CONCURRENCY=2
```

三组并发互不传导：数据中心采集股票 Pipeline 并发范围为 1–5、默认 5；持仓报告 worker 默认 2，
可调到 5；模型请求继续由独立的 `UZI_LLM_MAX_CONCURRENCY` 限流，默认 2。
提高持仓 worker 不会把采集或模型并发一并放大；provider 内部仍可以有更严格的独立限流。

2026-08-18 同一真实 provider、同一股票的两轮小样本基准如下；这是启用全档位完整
Agent 工作流前的历史基线，不能直接当作当前版本 SLA/P95：

| 档位 | 平均时间 | 两次范围 | 结果说明 |
| --- | ---: | ---: | --- |
| Lite | 5.39 分钟（墙钟） | 4.20–6.58 分钟 | 两次均可交付 |
| Medium | 15.86 分钟（墙钟） | 11.98–19.73 分钟 | 均被真实资金流缺口质量门拦截 |
| Deep | 42.01 分钟（执行图） | 11.21–72.81 分钟 | 第二次触发 900 秒模型阶段超时，方差很高 |

同环境另跑 5 只真实 A 股的 Lite 冷批次，持仓 worker=5、模型上限=4，峰值确实达到
5，`5/5` 报告可交付，批次墙钟 25.29 分钟，折合吞吐约每 5.06 分钟一份；单股从批次
开始到交付平均 24.24 分钟。该基线中 2 份报告的 narrative 证据路径被机械门拒绝并
按 Lite 规则降级，随后已增加只重试 `narrative_core` 的定向修复。十大持仓在冷缓存、
相同 provider 状态下应按约 45–60 分钟规划，不能把单股均值直接除以 5。

TuShare 配置 Token 只表示客户端可以鉴权；具体 `daily`、`fina_indicator`、`moneyflow`、
`top_list` 等接口仍取决于账号积分与权限。接口无权限时会记录脱敏的
`provider_unavailable` 并继续 AkShare/eFinance/BaoStock/直连 HTTP 回退，不会生成假数据。
密钥只放在被 Git 忽略的 `.env` 或部署密钥系统中。

镜像源可覆盖：

```dotenv
COGNITRADE_PYTHON_IMAGE=python:3.11-slim-bookworm
COGNITRADE_NODE_IMAGE=node:22-alpine
COGNITRADE_NGINX_IMAGE=nginx:alpine
COGNITRADE_PIP_INDEX_URL=https://pypi.tuna.tsinghua.edu.cn/simple
COGNITRADE_NPM_REGISTRY=https://registry.npmmirror.com
COGNITRADE_DEBIAN_MIRROR=https://mirrors.tuna.tsinghua.edu.cn/debian
COGNITRADE_DEBIAN_SECURITY_MIRROR=https://mirrors.tuna.tsinghua.edu.cn/debian-security
```

包与 Debian 镜像仅影响构建；不配置时使用 PyPI、npm 与 Debian 官方源。量化依赖集中声明在
`requirements-quant.txt`，并由主 `requirements.txt` 强制包含，因此容器部署与本地
`pip install -r requirements.txt` 安装的是同一套完整能力。

完整变量见 [`.env.example`](./.env.example)。

## GitHub Pages 前端

仓库包含 `.github/workflows/pages.yml`。在 GitHub Pages 选择 GitHub Actions 作为来源，并
设置仓库变量 `COGNITRADE_API_BASE_URL=https://api.example.com/api/v1`；推送 `main` 后会构建
前端、自动采用仓库子路径，并复制 `index.html` 为 `404.html`，因此直接访问
`/<repo>/strategies` 也能恢复 SPA 路由。

GitHub Pages 只托管静态前端，后端仍需单独部署为 HTTPS 服务。后端
`COGNITRADE_API_CORS_ORIGINS` 必须包含实际 Pages Origin，例如
`https://<owner>.github.io`；Bearer Token 和数据库密码不得写入仓库变量或 Vite 构建变量。
Token 仍由用户在页面顶栏输入并仅保存在当前标签页 `sessionStorage`。

## API

- Swagger UI：`/docs`
- OpenAPI：`/openapi.json`
- v1 文档：[`api-doc/api-v1.md`](./api-doc/api-v1.md)
- v2 Finance 文档：[`api-doc/api-v2.md`](./api-doc/api-v2.md)

业务接口只有 `/api/v1` 与 `/api/v2`。未版本化 `/api/*` 业务路由不再提供。
新增不兼容合同使用新的 API 主版本，不向旧主版本静默加入破坏性行为。

## 项目结构

```text
research_system/
  api/                    # FastAPI、鉴权、SSE 与版本路由
                            # VeighNa 模拟撮合与 Hikyuu 隔离回测适配
  market_data/            # PIT 契约、快照/证据、采集任务、Bundle 与报告输入
  strategies/             # 策略合同、Repository、日报/信号/回测/验证/晋级
  runtime/                # Agent OS、Turn、Graph、Tool、Pack、Memory
  schemas/                # Pydantic 协议
  services/methodologies/ # 当前被 Pack 调用的方法论计算
domain_packs/
  finance_ashare/
  legal_contract/
frontend/                 # React + TypeScript + Vite
scripts/                  # 部署、迁移和校验
migrations/               # Alembic 生产 schema 迁移
workspace/                # 运行数据（生产由命名卷持久化）
  paper_trading/          # SQLite 模拟盘账本
  backtests/              # Hikyuu 回测结果与隔离运行目录
  strategy_objects/       # 内容寻址的报告与策略回测大对象
```

当前架构说明见 [`plan/current-architecture.md`](./plan/current-architecture.md)。

## 验证

```bash
.venv/bin/pip install pytest
.venv/bin/python -m pytest -q tests
.venv/bin/python -m compileall -q research_system domain_packs scripts

cd frontend
npm run lint
npm test
npm run build:pages
```

部署后可执行：

```bash
./scripts/deploy.sh status
curl -fsS http://127.0.0.1:8080/
```

## 数据真实性

行情、财务、研究 Artifact 和 Agent 结论不得由前端伪造。上游不可用时，API 会返回
明确的质量状态或 `data_gap`，前端显示可读说明。数据中心的 provider
“测试”仅检查依赖和配置是否就绪，不发出网络请求，不能当作数据可获得性证明。少数尚无后端合同的交互使用显式
“本地预览”，只处理浏览器已加载的数据或文件名/大小，不会上传材料或冒充研究结果。
