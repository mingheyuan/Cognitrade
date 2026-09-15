# 历史 PIT 误降级修复计划与验收记录

状态：已完成（真实 `020464@2026-09-01` 报告与首个可回放交易日回测均已验收）  
基线：UZI-Skill `3.9.4` / `b3665e85b8666477038e11157d8e8feb0e006b76`  
范围：A 股 22 个采集维度、基金前十大持仓展开、数据中心任务与前端诊断

## 1. 问题结论

此前“降级很多”的主要原因不是数据源都没有数据，而是系统把四种不同情况压成了
同一个 `degraded/data_gap`：

1. 历史 Job 在调用 provider 前按“整个维度 current-only”跳过，K 线、财报、估值、
   公告、龙虎榜等本可按日期查询的数据没有获得执行机会。
2. provider 即使返回带日期记录，`as_of_date/cutoff_at` 也没有贯穿 Skill、Adapter 和
   实际请求；事后裁剪无法证明请求本身没有读到未来数据。
3. UZI 原始数据已存在但扩展字段或证据审计未满时，被当成与“上游超时/断连”相同的失败。
4. 前端只依据 `degraded` 显示“历史不可恢复、排除评分”，无法表达部分可用、查证为空、
   可重试失败和真正无历史档案。
5. 历史财报投影保留了上游展示字符串（例如 `6.10亿`、`2991.87万`、`7.48%`），而
   UZI 评分函数按数值序列直接计算同比，最终以字符串减法异常中断。中断发生在 Agent
   之前，后续角色评审、定性研究和报告产物都不存在，于是界面看起来像“很多维度同时缺失”。
6. Bundle 门已经把明确 `_excluded_from_scoring` 的历史安全缺口降为 warning，但 UZI
   profile 自审又把空维度升级回 critical；同时报告完整重跑错误复用了上一 attempt 的
   manifest，导致即使补采并发布了新 Bundle，报告仍反复读取旧错误输入。
7. 十大持仓的外层批次仍使用固定 30 分钟超时。Deep 的单模型阶段允许 900 秒，五只并发
   的第一波尚在正常生成时，日报编排已到达外层截止时间并级联取消剩余持仓；这与数据
   是否可用无关，却在前端表现成大量 cancelled/failed。
8. 历史 Bundle 的宏观、政策、行业、事件、原料和期货已有目标日结构化数据，但来源快照
   没有 HTTP URL。完整 Agent 的“两条引用”预检因此必然失败；若仍按当前日报告门禁，
   就会把不能事后恢复的网页引用误报为可重试 critical，反复执行模型也不会增加证据。
9. UZI `stock_features` 只按 `raw.ticker` 后缀推断市场，而 root transport 会合法保存
   `ticker=688041`、`full=688041.SH`、`market=A`，使 A 股游资组被误导为美股；修正身份后，
   该组对“证据不足”等明确中文缺口的校验词表又过窄，导致 24 席输出整组作废。外层恢复
   还把每轮变化的 evidence 文本算入问题签名，使同一个 roleplay critical 反复重算。
10. 角色票校验只读取 headline，忽略 reasoning/comment/verdict 中的真实证据或明确弃权；
    分组中文展示后缀也被当成身份合同，导致合法 `skip` 和成员完整的 `G+H+I` 短标签整组失败。
11. 回测虽接收 `as_of_date`，但通过 Finance 兼容层读取行情时没有继续传递，实际总在解析
    当前日 Bundle；即使命中正确 Bundle，回测又只识别旧 `kline.bars`，遗漏数据中心原生
    `kline.ohlcv`，因此把已经落库的目标日收盘行情误报为缺失。
12. 基金批次的每个股票子 Agent 启动前，都通过父级十股 Manifest 重新物化全部 220 个
    Snapshot 并逐槽查询冲突；10 个子任务把完整校验重复 10 次，形成平方级数据库放大。
    因此即使测试 Agent 只执行 5ms，批次仍可能超过 5 秒，并在测试目录清理时留下仍在写入
    的后台线程。这不是数据中心采集慢，而是报告信任边界使用了错误的校验粒度。

这会产生两个相反的错误：一方面把能恢复的数据错误排除；另一方面若只给当前 provider
结果补一个历史日期，又会造成前视污染。修复必须同时解决“误降级”和“伪历史”。

## 2. 目标语义

每个标的、日期、维度独立结算为以下一种结果：

| 结果 | 含义 | 报告/评分 | 重试 |
| --- | --- | --- | --- |
| `available` | 目标日结构化数据完整可用 | 由 UZI 原生门禁使用 | 否 |
| `partial` | UZI 可用 payload 已取得，可选字段或扩展审计不完整 | 保留评分资格并披露缺口 | 否 |
| `verified_empty` | 有界查询成功，窗口内确实为零条 | 合法空值，不算失败 | 否 |
| `retryable_failure` | 超时、断连、限流、临时解析失败 | 本轮不可用 | 是 |
| `unavailable` | 已确认来源只提供当前状态，且没有可证明的历史档案 | 排除评分并披露 | 否 |

股票报告门继续执行 UZI 3.9.4 原生数据完整性和 profile-aware self-review：缺失字段先进入
原生恢复输入；Medium/Deep 覆盖率过低或启用维度异常为空才形成 critical。Cognitrade 的
PIT、证据和字段合同仍作为独立审计，不能把 UZI 可用结果再次改判为来源失败。

## 3. 实施步骤

- [x] 把 `as_of_date` 与时区感知 `cutoff_at` 从 Collection Coordinator 贯穿到股票
  Snapshot Collector、各 Skill 和 Provider Adapter。
- [x] 建立字段级 PIT 能力表；历史 Job 只跳过真正 current-only 的字段/维度，不再全局阻断。
- [x] 对行情、财报、K 线、估值、资金流、龙虎榜、公告、政策、舆情和 UZI 组合维度做
  provider 侧日期限定；历史模式禁止实时网页热度、比赛排名等来源回填。
- [x] 保存查询窗口、逐来源状态、字段覆盖和重建证明；财报按可见披露截止日过滤，不能按
  报告期提前进入 Bundle。
- [x] 引入五态 `collection_outcome`，修正每日 Result、Task、Job 的结算和可重试性。
- [x] `missing_or_stale` 对可重建的旧降级槽重新采集；真正 current-only 的既有披露继续复用。
- [x] Agent recovery 对历史可重建来源执行定向 provider 补采；实时浏览器/网页恢复在历史日
  继续禁用。
- [x] 前端逐维展示“完整恢复 / 部分恢复 / 查证为空 / 数据源失败 / 无历史档案”，只给
  `retryable_failure` 提供重试入口。
- [x] 保持 Snapshot/Bundle 只追加不改写，保持当前日原生 UZI root workflow 兼容。
- [x] 历史财报显示值按 UZI 合同转换为数值序列（金额统一亿元、比例统一百分数值），
  并让 profile 自审尊重 `_excluded_from_scoring` 的安全缺口。
- [x] 区分重试输入：节点级重试保留原 manifest；确认过的完整重跑冻结最新合格 Bundle。
- [x] 把持仓批次外层预算改为 profile-aware：Lite 60 分钟、Medium 120 分钟、Deep
  180 分钟；多日历史补跑仍至少使用独立的 6 小时预算。
- [x] 历史六维定性证据若仅缺目标日 PIT-safe URL，保留预检失败事实并降为 warning；
  当前日、模型错误、无效来源和其他 UZI critical 继续硬阻断。
- [x] 用 UZI root 的 `full/market/code` 纠正 suffix-only 特征身份；Agent 覆盖后重算
  `consensus_valid/hollow_*`，扩充明确中文证据缺口词，并按问题类别/维度/正文终止重复恢复。
- [x] 合法弃权票跨四个解释字段校验，分组展示标签与成员身份解耦；日报新 attempt 清理同日
  旧错误、旧完成时间，但保留历史 attempt 和报告修订审计链。
- [x] 回测把目标 `as_of_date` 传到 Bundle 只读层，并兼容 `ohlcv/bars/history` K 线投影。
- [x] 基金持仓子任务改用 Manifest 子集加载：父 Manifest hash、全部 Bundle 引用和基金父
  依赖仍完整校验，但每个子 Agent 只物化自身 22 个 Snapshot，消除 10×220 重复扫描。
- [x] 当前日回归夹具统一使用 `Asia/Shanghai` 交易日，并让基金 Snapshot 的
  `effective_at` 对齐披露日，消除北京时间午夜后被 UTC 日期误判为历史任务的偶发失败。
- [x] 完成 `020464@2026-09-01` 的真实数据中心 → 10 股 Bundle/Manifest → 报告 → 回测验收。

## 4. 自动化验收

- 历史 provider 合同：未来记录裁剪、财报披露可见日、资金流日期过滤、龙虎榜合法空值、
  估值历史投影、部分可用与来源失败分流。
- 任务语义：`partial` 不可重试且不算阻断；`retryable_failure` 保持失败和定向重试；
  `verified_empty` 按成功结算。
- UZI 兼容：无历史日期的当前采集仍走原生 root router；UZI data-integrity/profile 门禁不变。
- 前端：四类非完整状态有不同文案与按钮行为；任务终态后自动刷新明细。
- 不可变性：旧 Snapshot/Bundle 不更新；补采只产生新 Snapshot 和新候选 revision。
- 编排性能：十股父 Manifest 的单股子请求只返回对应 1 个 Bundle/22 个 Snapshot，仍能在
  父基金 Bundle 撤销后拒绝重试；模拟 Agent 批次须在 5 秒门限内完成。

## 5. 真实验收记录

已完成的数据阶段：

- 基金父采集 Job：`01M1H63G0PFZ45H1QQ965BDAS`；10 股子 Job：
  `01M1H63T365ZMTGWDJ3M54YXAT`。子任务按并发 5 完成 220 个逻辑槽，基金父 Bundle 为
  `01M1H6CB6RKEM2AXMKXYSXZF8T`。
- 修正财报数值合同后，仅对 10 只股票的 `1_financials` 做定向重采，Job
  `01M1H80TJACHXJRD8JMBEND3C8` 为 10/10 成功；随后发布每只股票 revision 3。十只股票
  均为 21/22 可评分，唯一缺口 `19_contests` 是目标日无历史档案、明确排除评分的 warning，
  `critical=0`，可生成报告。
- 完整重跑 attempt 3 已冻结新 report manifest `01M1H8HMB92DKJ1WXSP997560D`
  （hash `75acc414c2e02b03efca0808ea3274b7becee6e6885bb3773c8b4ff73a92a452`），
  持仓批次 `01M1H8HNKY9XW17TH550A84G9K` 已越过原先的财务计算失败点并进入 4 组维度
  LLM 分析，但在固定 30 分钟外层截止时间到达后被旧编排逻辑取消；该运行用于确认数据
  修复有效，也暴露了与 Deep 档模型预算不匹配的第二个缺陷。profile-aware 外层预算已修复，
  后续定向修复 roleplay 合同，并通过 canonical 结果避免重复执行已成功股票。
- 最终日报 Job `01M1H6WY0RDTWJ45JC1X87R4ND` 的 attempt 8
  `01M1HHJ0ZDC7J7XSMFBZQYSH7M` 已完成；报告集 `01M1HHJFB1SY08R08WMVEF3C60`
  为 `final`、coverage `1.0`，持仓批次 `01M1HHJHDV5N03R3FD5985B238` 为 10/10 可交付、
  0 失败。八只持仓复用 canonical 结果，仅 002371、300604 做定向重算；最终 Job 的
  `errors=[]`、`failed_symbols=[]`，旧 timeout 不再污染新 attempt。
- 为验证交易日行情边界，显式采集并确认发布 2026-09-02 的 10 个持仓 Bundle，Job
  `01M1HJPZZ9P4QZCEBG5QJ0JP9F`。该批次的行情/K 线可用且 UZI critical 为 0；非核心历史
  定性来源的 60 个临时失败按 proposal 披露后人工确认，没有伪装成完整采集。
- 回测 Run `01M1HK21VK6FV712FB3NMVHGNS`（父失败尝试
  `01M1HJWH1PE4CMSKF8H76BNA43`）在 2026-09-02 对十大持仓 Bundle 完成回放，状态
  `completed`，结果 hash `1381c5ed4a8423e14abe59648f82c9fa62d2473be8d6ceb6819e192ee25d60bb`。
  该策略信号因 confidence `0.50 < 0.65` 为 `BLOCKED`，所以 0 笔成交、全现金、0% 收益是
  风控语义的正确结果，不是执行失败。最新 final revision 于 2026-09-03 才生效，未被回写
  到 2026-09-02，避免未来函数。

## 6. 最终回归结果

- 数据中心与 Finance API：`266 passed`，另有 `16 subtests passed`。
- UZI、基金报告、日报编排与回测核心组合：`205 passed`，另有 `33 subtests passed`。
- Manifest/基金批次专项：`39 passed`，父依赖撤销后的查看、交付与重试均保持 fail-closed。
- 前端：Vitest `17` 个测试文件、`80 passed`；TypeScript 与 Vite production build 通过。
- Python `compileall`、Finance Artifact 合同生成检查、`git diff --check` 全部通过。
- API 已使用当前工作区代码重启，`http://127.0.0.1:8000/docs` 返回 HTTP 200。

以上测试集合存在覆盖重叠，数字用于记录各验收面的独立结果，不作简单求和。
