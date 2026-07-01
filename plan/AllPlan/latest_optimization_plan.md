# 最新优化方案（2026-06-30）

> 状态说明（2026-07-01）
>
> 这份文档更接近当前主线，但它仍然同时包含“已落地方向”和“继续优化方向”。
> 当前已确认落地的部分包括：thread memory、methodology memory、memory-aware planning/execution、proposal/review 控制面、以及一轮本地全量回归。
> 还未最终收尾的部分，请直接对照 `completion_matrix.md` 的 `partial` 项，而不要把本文件里的所有优化项都视为尚未开始。

## 目标

本轮优化不再把重点放在“继续加 Agent”，而是先把研究系统的底座补强，尤其是数据源层、维度标准化层、检索层和方法论沉淀层。

核心判断：

1. 当前系统已经具备 `intent -> planner -> orchestrator -> snapshot/retrieval -> report` 的闭环。
2. 下一阶段的真正瓶颈不是再多加几个 subagent，而是数据源单薄、维度不稳定、检索轴不够强、长期记忆未落地。
3. `UZI-Skill` 最值得全面学习的不是“66 位评审团”，而是它的数据基础设施设计：
   - provider chain
   - data source registry
   - network preflight
   - market router
   - 按维度而不是按页面组织抓取策略
   - 失败降级和来源标注

因此，新的优先级应调整为：

1. 路由控制面升级
2. 数据源体系升级
3. 新 snapshot 维度补齐
4. 蒸馏维度标准化 + Qdrant 增强
5. 核心方法论模块化
6. subagent 收敛
7. methodology evolution 落地
8. mem0 长期记忆落地

---

## 一、总体策略

### 1.1 总原则

这次优化遵循 6 条原则：

1. Agent 不直接碰 vendor，所有外部数据都必须经 `skill runtime -> provider layer`。
2. 数据源不再按“某个接口能不能拿到数据”组织，而按“某个研究维度需要什么证据”组织。
3. fallback 不只是“源 A 挂了换源 B”，还要支持字段级补洞和来源保真。
4. 检索层不只是按 `symbol/sector/source_type` 查，还要逐步转向按 `dimension/methodology/scenario` 查。
5. 自动进化不能直接吃原始蒸馏结果，必须经过复核、proposal 和 approval。
6. mem0 只记住长期方法论，不记普通对话，不记未经审核的原始材料。

### 1.2 对 UZI 的吸收方式

这里强调“全面学习 UZI”，但不是“照搬 UZI”。

应全面学习的部分：

1. provider registry + priority chain
2. data source registry 元信息
3. network preflight
4. market/security router
5. 按 dim 组织的 source strategy
6. fallback merge 和来源标注
7. 自检 gate 和健康检查

不建议直接搬的部分：

1. 一开始就引入大量 persona 型 investor panel
2. 一开始就把 Playwright 变成默认主路径
3. 过早扩展到港股、美股、基金全市场深度链路
4. 把数据抓取逻辑散落进 agent prompt 或 service 细节里

结论：学习 UZI 的“底座工程”，而不是复制它的“上层表演形式”。

---

### 1.3 路由控制面：Redis 动态路由表

这一层必须单独提出来，因为当前把触发词、关键词、正则写死在 Python 代码里，本质上还是开发态方案，不适合生产态系统。

当前问题：

1. 新增或修改一个 intent/agent 的触发词，需要改代码并重启进程。
2. 多实例部署时，各实例的路由规则无法保证同步生效。
3. 无法做灰度、回滚、A/B 对照和审计。
4. 规则越来越多后，`TaskRouter` 会变成硬编码堆积点。

因此，路由层应改成“控制面 + 执行面”分离：

1. 控制面
   - Redis 保存动态路由表
   - 版本化发布
   - 支持回滚
   - 支持灰度切换
2. 执行面
   - `TaskRouter` 每次路由时读取本地缓存中的最新规则
   - 缓存过期后自动从 Redis 刷新
   - Redis 故障时回退到 last-good snapshot 或本地默认表

建议路由顺序固定为：

1. 显式 `intent`
2. 会话上下文补全
3. Redis 路由表的关键词/正则匹配
4. 语义向量匹配
5. LLM 兜底判断
6. clarify

这样做的原则很明确：

1. 低成本、确定性的规则优先
2. 模糊表达交给向量层
3. 只有冲突或高歧义时才上 LLM

#### Redis 路由表建议形态

不建议把规则拆成很多零散 key，也不建议直接把 Python 字典序列化后随意写入。

建议采用“活动指针 + 版本化 payload”：

1. `cognitrade:router:intent_rules:active`
   - 当前生效版本号
2. `cognitrade:router:intent_rules:v2026-06-30-001`
   - 完整 JSON 路由表
3. `cognitrade:router:intent_rules:last_good`
   - 最近一次成功加载的版本快照

这样可以支持：

1. 热更新
2. 秒级回滚
3. 多实例一致性
4. 审计和发布记录

#### 路由规则建议字段

建议每条规则至少包含：

1. `rule_id`
2. `enabled`
3. `priority`
4. `route_target`
   - 第一阶段仍以 `intent` 为主
   - 后续可扩展 `planner_hint`
5. `match_type`
   - `keyword`
   - `regex`
   - `semantic`
6. `target_field`
   - `query`
   - `normalized_query`
7. `lexicon`
8. `negative_terms`
9. `market_scope`
10. `requires_fields`
11. `response_mode`
   - `research`
   - `direct_answer`
12. `updated_at`
13. `updated_by`
14. `version`

#### 语义向量层怎么接

这部分正好可以和前面讨论的“关键词正则匹配 + 语义向量”接起来。

建议做成两层：

1. 规则层
   - 命中强触发词和正则
   - 速度快、可解释、适合高频场景
2. 语义层
   - 处理同义、近义、口语化表达
   - 例如“帮我看下最近哪个方向值得蹲”“有没有中线思路”这类模糊表达

语义层不建议重新发明一套复杂系统，优先可选两种实现：

1. 复用现有向量基础设施，在 Qdrant 中单独建一个轻量 `intent_routes` collection
2. 单独维护小规模 route embedding 索引，只服务 intent/route 匹配

LLM 不应再承担“第一层分类器”角色，而应改成最后兜底。

#### 路由控制面的边界

这里要刻意收住边界。

第一阶段 Redis 动态路由表控制的是顶层 `intent`，不是所有 subagent。

也就是说：

1. `stock_check`
2. `stock_screen`
3. `sector_research`
4. `distill_kol_logic`
5. `review_thesis`

这几个顶层入口先动态化。

subagent 的具体编排仍然由 `planner/supervisor` 决定，不建议一开始就把每个 subagent 暴露成路由终点。否则控制面会过快失控。

更合理的下一步是让 Redis 路由表先支持少量 `planner_hint`，例如：

1. `prefer_methodology=comps`
2. `prefer_depth=deep`
3. `prefer_review=true`

#### 生产要求

这一层必须满足生产要求，而不是“能用就行”：

1. 修改触发词后，不重启即可在下一个 cache refresh 周期生效。
2. Redis 短时故障不能让路由整体不可用。
3. 每个进程都应持有 `last-good` 规则快照。
4. `doctor/check` 必须能显示：
   - 当前路由版本
   - 缓存 TTL
   - Redis 健康状态
   - 最近一次刷新时间
5. 路由命中必须可观测：
   - 命中规则
   - 命中层级
   - 是否走向量
   - 是否走 LLM fallback

结论：

“规则 + 向量 + LLM”是对的，但前提是规则不能继续埋在 Python 代码里。  
路由规则必须先外置到 Redis，才谈得上动态维护、多人协作和生产发布。

---

## 二、数据源体系重构：全面学习 UZI

这是本轮最优先、也最需要重写设计的一部分。

### 2.1 当前问题

当前项目的数据源结构适合 MVP，但不适合继续长大：

1. `market`、`fundamental` 对 AkShare 依赖过重。
2. `notice` 基本单源依赖 Cninfo。
3. `sector`、`theme`、`capital_flow`、`lhb`、`sentiment` 等关键 A 股研究维度覆盖不足。
4. 失败时主要是“整段失败”，缺少字段级回填。
5. 没有系统化的来源优先级、健康状态、网络画像和交易市场路由。
6. Qdrant 里保留了证据，但对“这份证据来自哪个 source chain、补了哪些字段、可信度如何”记录不够强。

### 2.2 目标架构

新的数据层应重构为：

`Intent/Planner`
-> `SkillRuntime`
-> `SnapshotSkill`
-> `DimStrategy`
-> `ProviderRegistry`
-> `ProviderChain`
-> `VendorAdapters`
-> `FallbackMerge`
-> `VerifiedSnapshot`
-> `Blackboard/Qdrant`

这里最关键的是两层：

1. `DimStrategy`
   - 定义某个研究维度该优先找哪些 source
   - 决定是否允许 browser fallback、是否允许官方口径覆盖、是否允许字段补洞
2. `ProviderChain`
   - 把具体的 vendor 调用顺序、可用性判断、错误隔离、最终来源返回做统一管理

### 2.3 必须新增的核心模块

建议新增以下模块：

1. `research_system/adapters/vendors/provider_registry.py`
   - 统一注册 provider
   - 每个 provider 声明 `provider_id / markets / dims / available / access_tier / requires_key`
   - 提供 `get_provider_chain(dim, market)` 能力

2. `research_system/adapters/vendors/data_source_registry.py`
   - 参考 UZI 的 `DataSource` 元信息设计
   - 管理 source 的显示名、站点、市场、维度覆盖、tier、官方性、HTTP/Browser 类型、备注

3. `research_system/adapters/vendors/network_profile.py`
   - 参考 UZI 的 `NetworkProfile`
   - 检测：
     - 国内站点可达性
     - 海外站点可达性
     - 搜索能力可达性
     - 代理存在与否
     - 平均延迟
     - 建议降级策略

4. `research_system/adapters/vendors/market_router.py`
   - 参考 UZI 的 `exchange + security_type` 分类思路
   - 至少识别：
     - A 股 SH/SZ/BJ
     - stock / etf / lof / mutual_fund / convertible_bond / index
   - 决定后续 snapshot 路线和 early-exit 策略

5. `research_system/adapters/vendors/fallback_merge.py`
   - 实现多源结果 merge
   - 支持：
     - 优先保留主源值
     - 次源补空字段
     - 记录字段来源
     - 记录 `_fallback_applied`
     - 输出缺失字段摘要

6. `research_system/runtime/provider_health.py`
   - 给 `doctor` / `check` / `reindex` / CLI 提供健康检查
   - 输出：
     - provider 可用性
     - 最近错误
     - 建议 fallback 顺序

### 2.4 source registry 要建成什么样

这个 registry 不只是“列个站点名单”，而是为运行时决策服务。

建议 `DataSource` 元信息至少包含：

1. `id`
2. `name_cn`
3. `base_url`
4. `markets`
5. `dims`
6. `tier`
7. `official`
8. `access`
   - `http`
   - `browser`
   - `sdk`
   - `official_disclosure`
9. `requires_key`
10. `stability`
11. `latency_level`
12. `notes`

推荐 tier 参考 UZI 思路：

1. `tier1`
   - HTTP 主路径
   - 快、便宜、适合默认 fallback chain
2. `tier2`
   - Browser / Playwright 路径
   - JS 页面或反爬更强时兜底
3. `tier3`
   - 官方披露口径
   - 慢但权威，适合公告、财报、监管类数据兜底

### 2.5 provider chain 要怎么做

应参考 UZI 的能力，但改成当前项目可维护的版本。

建议核心接口：

1. `get_provider_chain(dim: str, market: str) -> list[Provider]`
2. `try_chain(method: str, dim: str, market: str, **kwargs) -> ProviderResult`
3. `merge_chain_results(primary, fallbacks) -> MergedPayload`

同时支持环境变量覆盖，例如：

1. `COGNITRADE_PROVIDERS_MARKET=akshare,baostock,efinance`
2. `COGNITRADE_PROVIDERS_NOTICE=cninfo,sse,szse,eastmoney`
3. `COGNITRADE_PROVIDERS_SENTIMENT=xueqiu,guba,news`

这会带来两个直接收益：

1. 不改代码就能切换优先级。
2. 不同机器、不同网络环境可以有不同 provider 顺序。

### 2.6 field-level fallback merge

这是当前项目比“补几个源”更值得做的点。

不是所有 fallback 都要整份替换。很多情况下更合理的是：

1. 主源给出 `price`、`volume`、`turnover`
2. 次源补 `market_cap`、`pe_ttm`、`pb_mrq`
3. 官方源补 `notice_url`、`publish_time`、`disclosure_title`

因此 `VerifiedSnapshot` 建议新增：

1. `primary_source`
2. `fallback_sources`
3. `field_sources`
4. `fallback_applied`
5. `missing_fields`
6. `source_confidence`
7. `source_trace`

这样后续 report、review、Qdrant 检索和 doctor 都能知道：

1. 数据是不是多源拼出来的
2. 哪些字段可信度更高
3. 哪些字段其实还没拿到

### 2.7 network preflight

应把 UZI 的 network preflight 思想引入当前 CLI。

建议新增：

1. `research_system/adapters/vendors/network_profile.py`
2. `workspace/cache/network_profile.json`

启动时机：

1. `doctor`
2. `check`
3. 首次 `run/chat`
4. provider 全部失败时自动重试一次预检

建议检测分组：

1. `domestic`
   - 东财、CNInfo、交易所、AkShare 依赖站点
2. `overseas`
   - GitHub、OpenAI、国际站点
3. `search`
   - 搜索引擎或新闻检索能力

运行时用途：

1. 国内站点通、海外不通
   - 优先本地 HTTP source
   - 降低 browser/search 依赖
2. 搜索不可达
   - 禁用搜索型 sentiment fallback
3. 代理存在但异常
   - 提前在 `doctor` 告警，不等任务跑崩

### 2.8 market router

当前系统如果继续做 A 股研究，必须把“标的类型分流”补齐。

建议至少支持：

1. 普通股票
2. ETF
3. LOF
4. 开放式基金
5. 可转债
6. 指数
7. 北交所股票

原因：

1. ETF/LOF 的研究路径和个股不同。
2. 可转债不应直接走普通 stock snapshot。
3. 指数/板块和个股的 `fundamental`、`notice`、`capital_flow` 取法不同。

因此 `TaskRouter` 和 `collector` 前面都应增加 market router 结果。

### 2.9 doctor/check 集成

新的 `doctor/check` 应该不只检查 env 和目录，而要检查：

1. provider registry 是否加载成功
2. 每个核心 provider 是否可用
3. network profile 是否健康
4. Qdrant 是否可写
5. snapshot skill 是否有最小 fallback chain
6. 是否存在“单点 provider 依赖”

---

## 三、新 snapshot 维度补齐

按 UZI 的思路，数据源是按维度扩展，而不是只扩某个站点。

### 3.1 现有 skill 重构

建议保留并重构：

1. `market_snapshot_skill`
2. `fundamental_snapshot_skill`
3. `notice_snapshot_skill`
4. `sector_snapshot_skill`

每个 skill 内部不再直接绑某一个 adapter，而是读 `DimStrategy + ProviderChain`。

### 3.2 必须新增的 skill

这是最有价值的缺口：

1. `lhb_snapshot_skill`
   - 龙虎榜、席位风格、净买入、连板特征
2. `capital_flow_snapshot_skill`
   - 主力净流入、北向资金、板块资金、异动流向
3. `sentiment_snapshot_skill`
   - 雪球热度、新闻热度、关键词情绪、社区关注度
4. `events_snapshot_skill`
   - 业绩预告、重大事项、会议催化、产业事件
5. `policy_snapshot_skill`
   - 行业政策、监管发布、部委信号、地方扶持

### 3.3 A 股维度对应的推荐 provider chain

这一部分应“全面学习 UZI”，但按当前项目的复杂度裁剪落地。

#### market / basic quote / kline

建议顺序：

1. `AkShare`
2. `BaoStock`
3. `eFinance`
4. `direct_http`（东财 / 腾讯 / 新浪中的稳定口径）

#### fundamental

建议顺序：

1. `AkShare`
2. `Tushare`（如用户具备 token）
3. `Cninfo`
4. `direct_http`（东财财务页等）

#### notice / disclosure

建议顺序：

1. `Cninfo`
2. `SSE`
3. `SZSE`
4. `Eastmoney`

#### sector / theme / concept

建议顺序：

1. `AkShare`
2. `Eastmoney`
3. `browser/search fallback`

#### lhb

建议顺序：

1. `AkShare`
2. `Eastmoney`
3. `direct_http`

#### capital flow

建议顺序：

1. `AkShare`
2. `Eastmoney`
3. `Sina`

#### sentiment

建议顺序：

1. `Xueqiu`
2. `Guba`
3. `news aggregation`
4. `browser fallback`

#### policy / events

建议顺序：

1. `Cninfo`
2. `gov / ministry / exchange official sites`
3. `industry media`
4. `browser/search fallback`

### 3.4 对当前项目的约束

为了控制工程复杂度，建议：

1. 第一阶段仍以 A 股为主。
2. source registry 预留 `A/H/U` 字段，但先不展开 H/U 全链路。
3. Browser fallback 先只给 `sentiment`、`sector/theme`、个别政策事件用，不默认全局开启。

---

## 四、蒸馏维度升级：从标签到方法论资产

当前系统已经有蒸馏链路，但还没完成“维度资产化”。

### 4.1 当前问题

`distilled_dimension` 目前更像一次运行中的标签产物：

1. 命名可能漂移
2. 别名匹配偏弱
3. 和 Qdrant filter 没形成稳定闭环
4. 不足以驱动 methodology evolution

### 4.2 新的两层结构

建议把维度拆成两层：

1. `ObservedDimension`
   - 一次蒸馏或一次 review 中观察到的维度
   - 允许描述更细、更口语化

2. `MethodDimensionCandidate`
   - 从多个 `ObservedDimension` 聚合出来的方法论候选
   - 具备统计和适用性字段

### 4.3 标准化流程

维度标准化不应完全依赖 LLM，而应按三级策略：

1. 关键词/正则
   - 归并高频固定模式
2. 向量相似度
   - 聚合同义和近义表达
3. LLM 兜底
   - 负责命名、边界解释和冲突说明

建议新增：

1. `research_system/services/dimension_normalizer.py`
2. `workspace/taxonomy/dimension_aliases.yml`
3. `workspace/taxonomy/dimension_patterns.yml`

### 4.4 Qdrant 需要补的 metadata

建议在现有 payload 之上增强：

1. `dimension_id`
2. `dimension_family`
3. `dimension_stage`
   - observed / candidate / approved
4. `scenario_tags`
5. `trigger_tags`
6. `market_scope`
7. `evidence_strength`
8. `falsification_hint`
9. `source_confidence`
10. `origin_run_id`

这样检索时才可以真正支持：

1. 找“政策催化类维度”的历史证据
2. 找“高辨识度龙头接力”在过去 review 中的正反样本
3. 找“只来自官方口径”的 notice 类证据

---

在进入 subagent 收敛之前，需要先把“方法论本体”补出来。否则系统即使有更多 evidence，也只会生成更长的描述，不会形成更强的分析框架。

---

## 五、核心方法论模块化：参考 financial-services-plugins 的 DCF / Comps / LBO

这里可以明确参考 `financial-services-plugins` 一类项目中的三种经典分析框架：

1. `DCF`
2. `Comps`
3. `LBO`

但仍需按当前项目做适配，而不是机械照搬。

### 5.1 为什么值得引入

当前项目已经有：

1. `stock_check`
2. `stock_screen`
3. `sector_research`
4. `review_thesis`
5. `distill_kol_logic`

但这些链路主要还是“收集证据 -> 组织解释 -> 输出结论”。

缺的不是再多一个 summary agent，而是可复用、可审计、可对比的方法论模块。

`DCF / Comps / LBO` 的价值在于：

1. 提供结构化分析模板，而不是自由发挥式报告。
2. 让 thesis review 从“对不对”进化成“假设哪里变了”。
3. 让 methodology evolution 有更明确的学习对象。
4. 让 Qdrant 和记忆层可以沉淀“分析框架经验”，而不是只沉淀事实碎片。

### 5.2 不同模块在当前项目中的定位

#### DCF

最适合融入：

1. `stock_check`
2. `review_thesis`
3. 中长期基本面研究

DCF 在当前项目里不应只是“算个估值”，而应拆成：

1. 假设集
   - 收入增长
   - 利润率
   - 资本开支
   - 营运资本
   - WACC
   - 终值增长率
2. 敏感性矩阵
   - `WACC x terminal_growth`
   - `margin x growth`
3. 估值区间输出
4. 关键假设脆弱点
5. 反证条件

对 A 股场景尤其重要的是：

1. 允许不完整数据下输出“轻量 DCF”
2. 明确区分“有财报支撑的假设”和“研究员主观假设”

#### Comps

最适合融入：

1. `stock_screen`
2. `stock_check`
3. `sector_research`

Comps 不应只做一个 peer list，而应做成：

1. 可解释 peer selection
   - 行业
   - 业务模式
   - 增长阶段
   - 市值区间
2. 指标分层
   - `PE`
   - `PB`
   - `PS`
   - `EV/EBITDA`
   - `EV/Sales`
3. 同组分位数
4. 偏离解释
5. 相对估值结论

这对当前项目很关键，因为 `stock_screen` 现在仍偏单一打分逻辑，Comps 模块可以把“筛选”从黑箱打分改成“相对定价 + 特征偏离”的框架。

#### LBO

LBO 对 A 股不是主路径，但仍有参考价值。

不建议把 LBO 作为默认主分析模块，而应作为：

1. 高现金流稳定资产的资本结构压力测试模块
2. 现金回收能力和杠杆承受力分析模板
3. “极限保守估值/回收模型”的可选模块

更适合当前项目的落地方式是把它改写成：

1. `cashflow_resilience_model`
2. `debt_capacity_stress_model`

这样既保留了 LBO 的框架价值，又避免和 A 股实际并购语境脱节。

### 5.3 在当前项目里的工程落点

建议把这些方法论做成独立模块，而不是埋进 prompt 文本里。

建议新增：

1. `research_system/services/methodologies/dcf_service.py`
2. `research_system/services/methodologies/comps_service.py`
3. `research_system/services/methodologies/cashflow_resilience_service.py`

对应 schema 建议新增：

1. `DCFModelResult`
2. `CompsModelResult`
3. `CashflowResilienceResult`

每个结果对象至少应包含：

1. `inputs`
2. `assumptions`
3. `outputs`
4. `sensitivity`
5. `warnings`
6. `evidence_refs`
7. `confidence`

### 5.4 如何接入现有 intent

建议接法如下：

1. `stock_check`
   - 默认尝试 `Comps`
   - 若财务数据完整且是中长期逻辑，尝试 `DCF`
2. `stock_screen`
   - 默认用 `Comps` 做同行偏离和估值分位支持
3. `sector_research`
   - 用 `Comps` 做板块横向对比
4. `review_thesis`
   - 用 `DCF` 或 `cashflow_resilience` 回看核心假设是否被破坏

### 5.5 这些模块如何进入 methodology evolution

这部分很关键。

如果只是把 `DCF / Comps / LBO` 当成一次性的分析插件，价值有限。真正应该做的是让它们成为方法论演化的对象。

建议新增两类 proposal：

1. `MethodologyTemplatePatch`
   - 修改某个方法模板的默认假设、字段要求、敏感性矩阵
2. `ValuationHeuristicPatch`
   - 修改估值类启发规则，例如在何种场景优先 DCF、何种场景只用 Comps

对应当前 `schemas/proposals.py`，后续应考虑在现有：

1. `DimensionPoolPatch`
2. `RetrievalWeightPatch`
3. `MemoryLearningUpdate`
4. `PromptPatchProposal`

之外新增：

1. `MethodologyTemplatePatch`
2. `ValuationHeuristicPatch`

### 5.6 与 Qdrant / Mem0 的关系

这些方法论模块不应只输出最终数值，还应沉淀中间结构。

建议 Qdrant 额外存：

1. `methodology_type`
   - `dcf`
   - `comps`
   - `cashflow_resilience`
2. `assumption_tags`
3. `peer_group_tags`
4. `sensitivity_tags`
5. `valuation_band`

建议 mem0 只沉淀：

1. 哪类标的适合哪种模板
2. 哪些假设组合经常失真
3. 哪些板块更适合 Comps 而非 DCF
4. 过去 review 里哪些估值框架误导率更高

### 5.7 当前阶段的现实取舍

建议实施顺序不要三套一起上满。

推荐顺序：

1. 先做 `Comps`
   - 对当前 `stock_screen` 和 `sector_research` 提升最大
2. 再做 `DCF`
   - 服务 `stock_check` 和 `review_thesis`
3. 最后做 `cashflow_resilience`
   - 吸收 LBO 的核心思想，但做成更适合 A 股的压力测试模型

结论：

`financial-services-plugins` 值得借鉴的不是“把投行模板搬过来”，而是把分析框架做成结构化、可执行、可复盘、可进化的方法论模块。

---

## 六、subagent 收敛：不是一味减少，而是职责重分
当前问题不是“subagent 太多”本身，而是其中一部分过于薄包装。

### 6.1 应保留的 subagent

保留原则：凡是承担流程边界、上下文切分、异步聚合、复杂判断的，保留。

例如：

1. chunk 构建与合并
2. review claim 抽取
3. retrieval decider
4. benchmark resolver

### 6.2 应收敛的部分

应被合并为 operator / utility / service helper 的：

1. 只做字段拼装的 subagent
2. 只做轻量规则判定的 subagent
3. 只是在 service 外再包一层调用的 subagent

### 6.3 替代策略

对路由、分类、标准化类任务，优先改成：

1. 关键词 / 正则
2. 语义向量
3. LLM 兜底

适用场景：

1. intent 初筛
2. symbol / sector / theme 识别
3. distilled dimension alias 归并
4. risk pattern 归类

这比“先上 LLM 再说”更稳，也更便宜。

同时需要补一条工程纪律：

1. 路由类关键词/正则规则不得继续硬编码在 Python 常量里
2. 顶层 intent 路由规则统一由 Redis 动态路由表管理
3. 本地代码只保留默认降级表和 last-good fallback 能力

---

## 七、methodology evolution：从骨架变成闭环

这部分在原规划里是 Phase 4，但真正落地要依赖前面的数据和维度层。

### 7.1 当前状态

当前项目已有：

1. `ProposalDraft` / `ProposalStore` 骨架
2. `methodology_manifest.yml`
3. `review_thesis` intent

但缺少：

1. evolver service
2. candidate 聚合逻辑
3. proposal 生成器
4. approval 后写回逻辑

### 7.2 目标闭环

建议做成：

1. `distill_kol_logic`
   - 生成 `ObservedDimension`
2. `review_thesis`
   - 产生后验有效性证据
3. `methodology_evolver`
   - 聚合多次 run/review，生成 `MethodDimensionCandidate`
4. `proposal_generator`
   - 生成：
     - `DimensionPoolPatch`
     - `RetrievalWeightPatch`
     - `PromptPatchProposal`
     - `MemoryLearningUpdate`
5. `approval/apply`
   - 审核通过后更新：
     - `workspace/config/methodology_manifest.yml`
     - `workspace/config/retrieval_policy.yml`
     - `mem0`

### 7.3 强制 gate

这一块也应学习 UZI 的自检思路。

proposal 生成前至少检查：

1. 是否有足够 run 数量支持
2. 是否有正反样本
3. 是否存在冲突案例
4. 是否仍停留在单次蒸馏臆测
5. 是否能给出明确适用边界和失效边界

---

## 八、mem0：补齐但不要滥用

当前系统里 `mem0` 只有配置，没有真正接入。

### 8.1 目标定位

mem0 不应做“聊天记忆”，而应做“长期方法论记忆”。

### 8.2 建议新增模块

1. `research_system/adapters/storage/mem0_adapter.py`
2. `research_system/services/memory_service.py`
3. `research_system/services/methodology_memory_writer.py`

### 8.3 允许写入的 memory 类型

只写 4 类：

1. `methodology_summary`
2. `dimension_experience`
3. `error_pattern`
4. `user_preference`

### 8.4 不允许直接写入的内容

1. 原始聊天记录
2. 原始 KOL 文本
3. 单次 `distilled_dimension`
4. 未通过审核的 proposal
5. 未复核的推断性结论

### 8.5 写入时机

只允许在这些节点写入：

1. approved proposal 生效后
2. methodology manifest 更新后
3. 经过 review 确认的错误模式沉淀后

---

## 九、执行顺序：重排后的阶段计划

这次需要明确调整顺序。之前把 methodology evolution 放得太靠前，现在应先补数据底座。

### Phase 0：路由控制面

目标：

1. Redis 动态路由表
2. 版本化路由发布与回滚
3. 本地 cache TTL + last-good fallback
4. 关键词/正则路由器
5. 语义向量路由器
6. `doctor/check` 路由诊断

验收标准：

1. 新增或修改触发词，不重启即可生效
2. 多实例在 cache TTL 内完成规则一致
3. Redis 不可用时，系统仍可基于 last-good/default route 正常工作
4. 每次路由都能记录命中规则、命中层级和 fallback 情况

### Phase 1：数据源底座

目标：

1. provider registry
2. data source registry
3. network preflight
4. market router
5. provider health check
6. field-level fallback merge

验收标准：

1. `doctor/check` 能报告 provider 和网络状态
2. 每个核心 snapshot 至少有 2 条 fallback 路径
3. `VerifiedSnapshot` 带来源追踪信息

### Phase 2：snapshot 维度扩展

目标：

1. 新增 `lhb`
2. 新增 `capital_flow`
3. 新增 `sentiment`
4. 新增 `events/policy`

验收标准：

1. `stock_check` 和 `stock_screen` 能消费这些新维度
2. 最终报告能区分“市场热度”和“基本面逻辑”

### Phase 3：维度标准化 + Qdrant 增强

目标：

1. `ObservedDimension`
2. `MethodDimensionCandidate`
3. `dimension_normalizer`
4. `qdrant payload` 增强

验收标准：

1. 同义维度能稳定归并
2. 检索支持按 `dimension_family/scenario_tags` 过滤

### Phase 4：核心方法论模块化

目标：

1. `Comps` 方法模块
2. `DCF` 方法模块
3. `cashflow_resilience` 方法模块
4. valuation metadata 入库

验收标准：

1. `stock_screen` 能输出同行估值偏离解释
2. `stock_check` 能在适合场景下输出轻量 DCF
3. `review_thesis` 能基于估值假设做回看

### Phase 5：subagent 收敛

目标：

1. 清理薄包装 subagent
2. 引入规则 + 向量 + LLM 兜底的标准化链

验收标准：

1. 核心执行链更短
2. 子模块职责更清晰
3. token 消耗下降

### Phase 6：methodology evolution

目标：

1. `methodology_evolver`
2. `proposal_generator`
3. approval 后写回 manifest/policy

验收标准：

1. 能从多次运行中沉淀方法论候选
2. 审核通过后能稳定更新配置

### Phase 7：mem0 落地

目标：

1. adapter
2. memory service
3. approved methodology memory write

验收标准：

1. 只沉淀长期方法论
2. 不污染普通对话

---

## 十、建议的具体文件改动

### 9.1 新增文件

建议新增：

1. `research_system/services/route_table.py`
2. `research_system/services/route_matcher.py`
3. `research_system/adapters/storage/redis_route_store.py`
4. `workspace/config/router_defaults.yml`
5. `research_system/adapters/vendors/provider_registry.py`
6. `research_system/adapters/vendors/data_source_registry.py`
7. `research_system/adapters/vendors/network_profile.py`
8. `research_system/adapters/vendors/market_router.py`
9. `research_system/adapters/vendors/fallback_merge.py`
10. `research_system/runtime/provider_health.py`
11. `research_system/services/dimension_normalizer.py`
12. `research_system/services/methodologies/dcf_service.py`
13. `research_system/services/methodologies/comps_service.py`
14. `research_system/services/methodologies/cashflow_resilience_service.py`
15. `research_system/services/methodology_evolver.py`
16. `research_system/services/proposal_generator.py`
17. `research_system/adapters/storage/mem0_adapter.py`
18. `research_system/services/memory_service.py`
19. `workspace/taxonomy/dimension_aliases.yml`
20. `workspace/taxonomy/dimension_patterns.yml`
21. `workspace/config/provider_policy.yml`
22. `workspace/config/data_sources.yml`

### 9.2 优先修改文件

建议优先修改：

1. `research_system/services/task_router.py`
2. `research_system/runtime/settings.py`
3. `research_system/runtime/orchestrator.py`
4. `research_system/cli/chat_shell.py`
5. `research_system/cli/chat_fallback.py`
6. `research_system/runtime/skill_runtime.py`
7. `research_system/runtime/gates.py`
8. `research_system/agents/collector/service.py`
9. `research_system/agents/retriever/service.py`
10. `research_system/agents/reporter/service.py`
11. `research_system/adapters/storage/qdrant_adapter.py`
12. `research_system/schemas/distillation.py`
13. `research_system/schemas/proposals.py`
14. `research_system/agents/screener/service.py`
15. `research_system/agents/reviewer/service.py`

### 9.3 新增测试

至少补这些测试：

1. redis route table hot reload
2. route cache ttl and last-good fallback
3. regex + semantic route precedence
4. provider chain fallback
5. field-level merge
6. network preflight degrade path
7. market router classification
8. dimension normalization
9. qdrant metadata filter
10. comps/dcf methodology output contract
11. approved proposal -> methodology manifest apply
12. approved proposal -> mem0 write

---

## 十一、最终结论

这版优化方案的核心变化只有一句话：

先把系统从“能跑的多 Agent 原型”升级成“有稳定数据底座和可演化维度体系的研究引擎”，再谈更复杂的自进化和长期记忆。

如果要用一句更直接的话描述这次重排：

1. 不是继续堆 Agent。
2. 不是先把规则继续写死在 Python 里。
3. 不是先做花哨 panel。
4. 不是先把 mem0 接上就算完成。
5. 而是先把路由控制面和数据底座补齐，把 router、provider、source、network、market、fallback 这几层做成真正可运维的系统。

只有这层补齐，后面的：

1. Redis 动态路由
2. 蒸馏维度资产化
3. Qdrant 高质量检索
4. `DCF / Comps / cashflow_resilience` 方法论模块
5. methodology evolution
6. mem0 方法论记忆

才不会把噪音沉淀成“知识”。
