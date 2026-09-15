# 原生 UZI 对齐与端到端验收报告（进行中）

## 结论

**最新：当前模型最小请求明确返回403／PermissionDeniedError，已停止后续模型调用并请求用户确认权限。** 这与此前无HTTP响应的ReadError分别记录，不能把二者混为已确认的同一根因。正文提取修复已通过99项归档／68项绑定回归、真实19段公告离线复核及36组／72worker原生对照；候选镜像`34199c85`的632文件与工作区匹配、原生完整性通过，但尚未部署，服务仍`d8f32bc6`／healthy。原生完整Agent、内容质量及历史信号回放仍未完成。[最新封存证据](../workspace/reports/uzi-native-alignment-20260908-rYqLft/archive-body-permission-stop-20260913/summary.json)与进度第60节优先于以下旧检查点；权限恢复前不新增模型请求。

09-13最新补充：宿主完整OS `01M2BM43PCAG0XGCDZ7VCYCMAJ` 已373.888秒completed，2次实际模型请求，原生0 critical／1warning，HTML GET200；内部原生raw全部维度与录制参考相同，内容质量未验收。原生参考`t3-frozen-panel-reference-20260913`终态4/5组，G+H+I在600秒截止；现已实现精确请求绑定的局部续跑，两次各只请求该失败组，分别约2.870／2.654秒发生APIConnectionError，第二次因果链明确ReadError，无HTTP响应，未执行主分析／最终自审。没有重复请求成功评审或研究。归档漏源修复已部署并经过36组对照、历史输入和旧报告复核；部署入口真实归档1篇上海证券报原文，仅证明09-13取得该内容，不代表历史可恢复或研究完成。[最新机器收据](../workspace/reports/uzi-native-alignment-20260908-rYqLft/reference-resume-and-source-verification-20260913/summary.json)与进度第59节为准；下列更早“最新”段落均为保留检查点。

**目标尚未完成。** 当前已经证明指定输入下的原生计算／检查对齐，已有完整 Agent OS 真实完成记录，也实际验证了历史基金分支停止的位置；这些不能合并表述为“历史基金报告与信号回测已通过”。本文用于收拢分散检查点，不能覆盖原始运行结果。同采集档位实采现已结束，剩余条款级审计及未通过项的逐一归因仍需完成。

唯一原生基准是 **UZI 3.9.4 / `b3665e85b8666477038e11157d8e8feb0e006b76`**。当前后端镜像 `sha256:d8f32bc66d41e3d11ddfe647d83550484f6752ae0e4603c7c34fb7eae1eb684a`、Web `8523b57f`，632个生产源码文件与工作区一致，部署核验healthy；数据库版本 `20260906_0006`。新Manifest使用v7投影，旧v1—v6保持原规则；这不是升级原生UZI。保留持仓并发5、模型并发4、原截止和来源权限，不改评级／作战价位，不提交推送、不启用订阅。旧OS `01M2BFWRMKGB9W9QTMX0REHS03` 的完整SKILL结论已被逐项审计否定：缺少Agent辩论与warning说明；后续`01M2BJGKJ3KW6640FCJJDKYFSF` 已实际验证这两项修复，但不能证明其他职责及内容合格。旧结果和源码版本不可混为同一次执行。

### 09-13 完整职责更正

[真实产物复核](../workspace/reports/uzi-native-alignment-20260908-rYqLft/full-skill-closure-recorded-audit-20260913/summary.json)确认原生同输入实录具备3+3轮辩论及资金流warning原因，旧宿主两项均缺。现已修复full工作流被旧档位开关禁辩论的问题，并接通局部Agent警告说明与再次自审；180 tests及11 subtests通过，已部署。原生警告不改severity，旧输入／报告不重写；修改后的真实报告尚待验证。下列03:25及更早检查点中的“SKILL通过”仅指原机器门当时结果，不再作为完整工作流验收证据。

真实复验 `01M2BJGKJ3KW6640FCJJDKYFSF` 已completed，观察490.902秒，3次模型尝试，实际具备3+3轮辩论及逐条warning原因；原生0 critical／1warning保持，GET200。Manifest `01M2BJEAEN52ZSJHB7S8SN7SE9` 保留原09-12同输入，研究及66人结果复用，不是冷启动计时或新日采集。[真实收据](../workspace/reports/uzi-native-alignment-20260908-rYqLft/full-skill-closure-live-verification-20260913/summary.json)只证明两项修复，不能代表全部SKILL内容合格。

随后又发现Task3.3/4综合输入丢失规则明细、逐人推理、职责、改变判断条件及覆盖前规则分，已修复并通过165项／11子测试，详见进度第57节。静态导出遮罩、裁切、页脚重叠及模板类名错配也已修复，27项回归与真实两图复验完成；二维码离线不可用、原生人数口径及内容问题仍保留。新源码36组独立对照通过，已备份并安全部署，历史220快照与引用只读不变；新OS `01M2BM43PCAG0XGCDZ7VCYCMAJ` 正在综合研判，使用原09-12输入的新Manifest `01M2BM2KYA1VZ6XEBXXPHFSF67`。同时发现并修正原生实验驱动漏传规则分基线的问题，66项离线回归通过，新的真实参考工作流仍需执行；该问题不能归因于原生公式。

## 验收分层

03:25 CST 最新修订：完整OS `01M2BFWRMKGB9W9QTMX0REHS03` 已659.886秒completed，3组研究、5组66位评委和主分析完成。三组研究和四组评委复用严格验证的合格缓存，实际新请求A+B四次、主综合一次；不是全冷启动性能。自审通过不代表内容正确，见[交付收据](../workspace/reports/uzi-native-alignment-20260908-rYqLft/t3-path-assignment-delivery-20260913/summary.json)和[内容审计](../workspace/reports/uzi-native-alignment-20260908-rYqLft/t3-path-assignment-content-visual-v4-20260913/content-findings.json)。随后诊断补丁167项回归通过，架构边界124 tests／17 subtests通过；部署后新旧报告GET200、历史220份快照和引用不变。条款级证据仍继续核查，不能把机器账本条目数当作总体完成率。

最新条款级续验：当前生产源码的36组／72进程对照全部通过，PIT／条件回放／研究引用164项回归通过。[逐项账本](../workspace/reports/uzi-native-alignment-20260908-rYqLft/requirements-architecture-acceptance-v2-20260913/summary.json)新增8项证据核验，现41/68条；余下27条包含尚未审完的上层验收要求，不等于27个已知代码bug。历史分支按原计划的条件执行规则核验：先跑一股，缺当时固定输入则停止；这一分支的准确受限结论可验收，但不能称历史报告／回测已成功，也不能据此提前结项。

| 层 | 证据所支持的结果 | 不能据此宣称 |
| --- | --- | --- |
| 原生计算／检查 | 诊断版本部署后的最新36组配对、72独立进程，业务字段零差异；6组计算／综合，30组历史context自审 | 所有数据源成功、十股重新研究成功 |
| 当前输入完整 Agent | 原生参考实录11次模型请求；最新宿主OS completed，研究3/3、评委5/5共66人、主分析完成，实际新请求5次 | 两侧每个阶段都是本轮新调用；09-13新日全量采集完成 |
| Python原生自审 | 旧终态0 critical／1资金流warning；原机器交付门通过，但后续查出两项SKILL职责遗漏 | 完整SKILL已验收、引用质量合格或数据严格齐全 |
| 报告内容／视觉 | 宿主HTML接口200，320／390／1440px无整页横向溢出，最终50及/100正常；期货论据错绑公司PDF、原生评委总数残留，桌面浮动目录遮住部分标题 | 可当作可信投资建议；整份长报告和所有交互已人工验收 |
| 数据中心独立实采 | 原生deep来源阶段421.640秒；宿主含补证／重建824.907秒，partial终态，实际修订与重算完成 | 两侧缓存、硬时限、阶段范围完全相同；平均耗时更优 |
| 历史基金 | 新Bundle／Manifest已冻结，真实单股执行在历史辅助输入缺口处gated | 十股正式报告已生成 |
| 历史信号回放 | 新服务再次正式预检：0合格报告／0信号、can_run=false；业务库存未变，引擎未启动，`trade_count=null` | 引擎跑完但零成交、或真实成交链路通过 |

## 已修复的本地差异

1. 原生权威计算入口直接调用固定函数，移除生产链上的额外panel评分与合成重算；原生问题向量、宿主研究完成度、执行失败和PIT安全分开。
2. 原始DimResult保留错误、超时、来源和缺失语义；原文库存不再强制“两份不同URL”。不同事实可引用同一原文，未知信息不冒充证据。
3. 数据中心接通原文归档、显式事实修订、新快照、依赖重建及原生重算；基金和单股共用补采判断。旧快照、Bundle、Manifest不随新增资料变化。
4. 原生综合实际需要的量化辅助响应在数据中心固定，报告只回放。缺失时列明operation和参数，不用默认零值冒充已取数；独立执行依赖诊断不新增原生critical。
5. 来源进程与后续补搜共享每股真实搜索消费，避免每个进程重新领取额度。完整和定向入口各自有明确契约，仍调用原生搜索／缓存函数。
6. 真实故障回放覆盖公平排队、429与权限／额度区分、共同截止和取消清理。报告条件回放只执行原评级与`dashboard.battle_plan`，没有旧MA策略或自动补仓。
7. 修复同一公司报告里的期间引用不完整：对有明确报告期定义的定性披露，补充原样期间引用，并在保存边界重验；不据报告标题猜数值期间。真实保存的5条提案经已部署代码只读回放通过，1条全年／半年混淆仍被拒绝。没有直接改写原任务或旧快照。
8. 拒绝提案增加有界类型、失败字段、引用身份与hash诊断，持久化到任务检查点；不记录任意原回答／引文，不声称恢复旧4条wire内容。
9. 修复verified_empty状态被填进原生data的问题：新采集保留实际零计数、空列表和source/error/_timeout，新v7不再用状态说明凑非空。55份原内容已丢失的旧空快照明确标未留存；旧投影与旧冻结输入不变。
10. 四派研究买区无法定价时保留null及原因，去掉宿主“强制结构失败”要求；原生仍仅给warning，有数值仍验证依据。未知研究价不会回填零／现价，也不替代回测读取的原生作战计划。买区提示变更使对应缓存失效，未影响阶段仍复用。
11. 拆分主分析接入固定SKILL的原文研判职责，并独立传入其必需的已有模型假设／估值／催化剂／IC风险；不靠上一阶段是否引用才决定可见。不增加模型阶段，不改原生公式或已冻结输入。新提示的真实模型复验已启动，旧完整OS记录不能代替新版验证。
12. 修复研究与评委并行导致研究未传入、研究修订复用旧评委、raw关键摘要及原生人物资料遗漏。研究→评委依赖明确，组内仍并行；仅变更的上下文使下游缓存失效。完整YAML／原生台词／历史知识都进入请求组装；历史资料不能证明当前或目标历史日持仓，不改冻结事实与交易计划。168项测试及11项子测试通过，36组72worker仍零业务差异。最新真实任务在上游研究失败，未调用评委，不能声称该轮已验证新评委输出质量。
13. 真实请求出现672／681秒才退出，超过配置600秒；离线反例确认宿主只传SDK传输超时、没有单次总耗时截止。已补入单调时钟期限，保留取消／阶段期限优先和请求清理后释放槽位。没有父级控制的UZI调用也使用可取消接口，旧provider签名仍兼容。超时不新增自动重试，不改配置期限；76项定向与89项恢复回归通过，两套可能重叠不相加。实际模型服务是否恢复需另测。

以上每项的具体前后证据与局限保留在[逐次实施记录](uzi-native-alignment-progress-20260908.md)。最新v7必要回归为165项输入／门禁集成、84项事实边界、63项回放归因、9项空数据专项，均通过；原36组对照按新源码复跑，仍零业务差异。上一诊断修复191项及更早回归保留，不将重叠套件累加成去重总数。测试启动错误、fixture错误与真正的业务红测分开记录。完整GitHub Actions尚未触发。

09-13 00:52 CST新增两项分析接线修复：139项测试及11项子测试、32项渲染关联测试通过；当前源码的36组／72worker对照仍零差异。新服务再次确认旧历史220份快照、引用和预检不变，旧HTML200；没有新模型、采集或历史回测。这些最新证据见进度第47节及下列机器入口，不能用旧测试源码hash覆盖此次改动。

## 真实任务与输入身份

最新完成：`01M2BFWRMKGB9W9QTMX0REHS03`，Manifest `01M2BFSDGX32Z1VA9W7B3949CH`／hash `cb291d71dce3ee796881e8e1c0d83bf3abe8aba40cc27a2a3033d71c365b21ee`。完整OS观察659.886秒，实际A+B组77.202／69.514／85.452秒三次结构失败，104.217秒第四次通过；主综合186.919秒通过。前三次仅保存`value_error`及字段路径，不能反推具体理由或说模型漏了全部15人；新增诊断只影响未来失败。最新原生工件hash `18d31ca69e9da4b82bf2bf830afe0ea4b113770281f7bb3568eb5523bcf62ee2`，运行期间源码／数据库输入不变。后续API诊断补丁不修改该任务结果。

以下为保留的先前失败检查点，不是最新状态：`01M2B9GVHBCBQHRVXK1NJDF3M2`，Manifest `01M2B9G2EN2J3XHMNC585FFP58`／hash `6734a2c52bc23a63d4bc248e9afead654f4e204c0c854cd00f86a64716cca883`，在六维研究后以gated终态结束，观察器780.571秒。成本组3次服务错误累计219.937秒；行业／宏观分别672.203／680.840秒超时。共5次真实模型尝试，0/3研究组完成，评委和主综合未执行；最终原生Python自审1 critical／1 warning，报告接口409，回测未运行、交易数null。该失败记录没有被新成功任务改写。

该次使用v7、prompt hash `d99bf708eac61d92f3983fd43effc2835f8f7ba2fac3d06664cd29c50823a375`，raw／辅助响应与旧原生参考逐项相同，证据仅Manifest ID/hash变化；来源库存与运行期代码hash不变。原生参考是已录制真实执行，非同一时刻的新调用，因此不能仅凭本轮服务故障归因成两侧业务逻辑差异。随后修复总时限并部署新镜像；旧失败任务不改写。

总时限修复后的另一旧复验：`01M2BB1ZX3KY52CBBJTC8R773K`，Manifest `01M2BB186AZKNQMGNJ6F3A42ZA`／hash `bc4ab3a107238242d02d6d3519f3a49b4b5a2f2b7945e4eb4e5839e32b32f838`。751.436秒、18次尝试后终态gated，3组研究完成、评委1/5；完整记录见实施进度第51节。A/B首轮明确返回HTTP502，重试后通过，不代表其余组都成功。此任务已停止，不是仍在执行。

| 项目 | 身份／结果 |
| --- | --- |
| 当前日完整OS | `01M2AQE0P2A77GFBKHM0FV3PFY`；completed；观察器447.171秒，含采样／导出开销 |
| 上述固定输入 | Manifest `01M2AQBTGEEMTJQ9Q0W5HVPYN9`；hash `33f31cef2760e04291992ab193e8f18b47b77ec1dbb64d6c47af16b28ec74fc8` |
| 原生完整Agent参考 | 814.575秒、11次真实模型请求；后续对照显式复用，不重复付费 |
| 原生deep实采 | 2026-09-12 15:13:42—15:20:43 UTC；421.640秒；157个导出工件hash校验通过；固定原生源码未改 |
| 原生deep实采Manifest | hash `32c82160d78fe2778358c1ebd4ddb0c2a892109529c57616790105ba0b587b78` |
| 宿主deep实采 | `01M2B3AM928D9A2GABXVTZZ2MZ`；688041.SH／2026-09-12／all／force_all；显式归档研究、不自动冻结；824.907秒partial终态 |
| 历史基金补缺父任务 | `01M2AXD73MW0M8RW3YT8S6T512`；020464／2026-09-02 |
| 历史新冻结输入 | Manifest `01M2B1CRXQ5MM3NMM6NDF0ZK09`；hash `b000a57acf04c02c949eca8e37d34bb44a72e6f50b477d1986d6e875ac9cb589` |
| 历史真实单股OS | `01M2B1QE62JN706Y1FAXNTKTF7`；28.693秒，gated，核心Agent未启动 |

报告分析仍为medium；deep只用于数据中心现有全维采集及本次匹配的原生来源阶段。一次顺序对照不是平均延迟或成功率统计。周末采集保留来源返回的实际交易日期，不虚构周末行情。

## 历史分支为什么停止

基金冻结时严格可用166/220槽，54个缺口；其中当时4个可定向重试、50个不可原样重试。这里只统计相应候选，不代表新增166份采集。11个Bundle逐一确认，十只股票的固定输入全部绑定。

真实海光历史执行先缺：

```text
all_holding_funds(ticker_code="688041", max_funds=80)
reason: native_quant_inputs_missing
```

当时可用的响应未被归档，现有较晚响应不能回填09-02。这是当前历史输入能力缺口，不是LLM过期、请求超时，亦不是这次已完成最终自审后才拒绝。原生`19_contests`历史非空档案不足仍是另一项独立数据检查问题。

只读线上Gateway已经与离线诊断逐项核对；旧输入库存hash不变，未在报告阶段隐藏采集。正式回测预检0合格报告／0信号，未调用引擎。30条历史真实开盘价的独立库存核验，不替代报告信号输入，也不能把预检的0个已检查股票误读为十股行情全覆盖。

## 仍存在的问题及归因

- **已知原生问题**：固定版资金流调用中股票代码被传到股东户数的季度参数，曾实测进入879页扫描，主力资金接口尚未执行就耗尽120秒；本次原生整波还被mini-racer保护跳过。不能归因为“资金流数据不存在”。本轮不改上游算法或删除保护。
- **来源／内容质量**：本次原生Stage1完整性15/18，仍缺公司所属行业，部分搜索结果相关性不足；非空JSON不代表事实可靠。宿主已根据原文修订行业和主营业务，实际原生重算使用修订后的行业“计算机、通信和其他电子设备制造业”。最终重算输入22份快照中20份available，资金流和相似股票为data_gap；这是快照状态，不代表所有研究内容通过。
- **补证拒绝已部分定位并修复**：原任务仍保留10项拒绝的历史记录。其中5条不是原文缺失，而是事实短引文未带同报告期间定义；修复后只读重放通过，但未把这5条直接写入旧任务。外汇提案将6月末事实标成全年，超截止拒绝仍正确。余下2项摘录未找到、1项不支持字段、1项解析后不合约缺少保存的原始wire提案，具体内容尚不能核验，不能一律归为来源不存在。政策维原文库存evidence_gap也未被本次修复消除。
- **模型引用归属**：既有宿主报告至少3条研究事实错挂公司PDF引用，原生参考也有同类问题；保留原回答，不用改结论或引用制造通过。
- **原生视觉问题**：社交图与战报图实际已生成，经文件hash与人工查看确认，大块加载遮罩挡住正文。旧主结果“deferred”已由后续生成收据补充；正确状态是“生成完成、视觉不合格”，不是缺文件或LLM失败。
- **验收尚未收拢**：独立实采终态及20核心维逐项来源对照已保存，全部计划条款仍需逐项关联代码、测试和真实工件；不能用局部绿测替代整个目标。
- **旧拒绝提案无法精确恢复**：原路径只保存拒绝索引／原因，模型请求日志仅记阶段／耗时，当前配置禁用远端response存储。新有界诊断已部署，能定位失败类型与绑定身份，但不保存原文wire，不能倒推出旧4条回答。此结论限于已检查的正常任务路径，不声称搜遍一切外部日志和用户备份。
- **旧零查询来源内容丢失**：55份verified_empty快照保留查询状态、没有原始数据容器。v7不再用状态绕过原生空维检查；新采集会保存真实内容。已冻结v6 Manifest仍使用原规则，新服务读取其220份物化快照和全部引用逐项不变。这不等于55个接口实际失败，也不是这些内容已恢复。

## 本轮补证实际完成了什么

宿主任务创建于15:21:11 UTC、15:34:56 UTC结束；观察器832.892秒包含API轮询／导出，服务执行824.907秒。原生补搜约79.295秒，原文归档34.639秒，三组研究138.786秒，身份依赖重建262.884秒，量化辅助固定2.585秒；来源阶段日志约296.7秒。各阶段起止与总计间还有任务协调开销，不能把原生日志中错误复用总计的“wave1”字段当作基础行情耗时。

实际追加58份快照，所有旧快照及Bundle hash不变。公司基础新版本 `01M2B3VNYPNXSKR7KDEA8P4Z2M`／hash `25b1f4eacca099fb623e99ae1d8b23ea61d41cdfb0f7681fdb9c8d06049e0f4c` 含两项有来源事实修订，九维依赖均完成绑定。原生重算0.090秒，输入hash `caf4ce267a3a850c8661099e20d95f8178f711e4a0f08a43474e1a0975165d09`、输出hash `8d21625590f71ff122736ae4da7355d7eefb40fee01b4df8787414ad9509bc5e`；该次重算无新模型调用。量化辅助状态available、证据 `01M2B43STSTS77X3V5W3AD8QG2`。未自动冻结或新建报告、未重跑历史任务。

比较使用重算收据列出的实际22份输入ID/hash，而非初始采集任务仍指向的旧基础行情版本，避免误判“修订没有生效”。原生20核心维逐项列出source/error/timeout与数据非空状态，额外明确两側采集时间、缓存热度、硬时限与研究阶段范围不同。这一组验证采集／补证能力，不用于推导计算差异。

## 机器证据与下一步

工作区审计目录：`workspace/reports/uzi-native-alignment-20260908-rYqLft`。关键入口：

- [最新36组对照](../workspace/reports/uzi-native-alignment-20260908-rYqLft/t1-diagnostic-deployment-20260913/summary.json)
- [逐条验收证据账本](../workspace/reports/uzi-native-alignment-20260908-rYqLft/requirements-architecture-acceptance-v2-20260913/summary.json)：已对原计划68条建立索引，当前41条完成条款级证据核验；其余待核验不等于存在27个代码故障，更不是完成率统计。诊断变更涉及的4项旧源码绑定以AST范围证明和最新原生对照重新验证。
- [新服务历史回测预检](../workspace/reports/uzi-native-alignment-20260908-rYqLft/requirements-architecture-acceptance-v2-20260913/historical-preflight.json)：复用已创建V2版本，09-02—09-04，无新增采集／报告／信号／引擎运行。
- [当前部署的真实提案回放](../workspace/reports/uzi-native-alignment-20260908-rYqLft/v7-deployed-period-replay-20260913/summary.json)：6条保存提案、零新增模型／来源请求、数据库只读。
- [55份旧空快照的版本对照](../workspace/reports/uzi-native-alignment-20260908-rYqLft/native-empty-replay-20260913/summary.json)：旧6个投影版本无差异，新版本不造非空事实，原始内容未恢复。
- [旧220份物化快照与引用的线上核验](../workspace/reports/uzi-native-alignment-20260908-rYqLft/t4-v7-legacy-manifest-verification-20260913/summary.json)：旧Manifest仍固定v6，输入与预检均不变。
- [拒绝提案留存缺口审计](../workspace/reports/uzi-native-alignment-20260908-rYqLft/wire-rejection-retention-20260913/summary.json)
- [原生独立实采导出校验](../workspace/reports/uzi-native-alignment-20260908-rYqLft/source-profile-parity-deep-20260912/export-verification.json)
- [同档位实采终态对照](../workspace/reports/uzi-native-alignment-20260908-rYqLft/source-profile-terminal-comparison-20260912/summary.json)
- [历史真实单股停止记录](../workspace/reports/uzi-native-alignment-20260908-rYqLft/t4-frozen-single-full-os-20260912/summary.json)

最新入口：[分析职责／空价修复](../workspace/reports/uzi-native-alignment-20260908-rYqLft/native-agent-protocol-final-20260913/summary.json)、[最新36组对照](../workspace/reports/uzi-native-alignment-20260908-rYqLft/t1-native-agent-protocol-20260913/summary.json)、[当前服务旧输入核验](../workspace/reports/uzi-native-alignment-20260908-rYqLft/t4-native-agent-protocol-input-verification-20260913/summary.json)。上面的条款账本是上一检查点；本轮未提前把整个工作流或真实模型质量勾选通过。

最新评委接线证据：[离线实际输入证明](../workspace/reports/uzi-native-alignment-20260908-rYqLft/roleplay-input-proof-20260913/summary.json)、[当前36组对照](../workspace/reports/uzi-native-alignment-20260908-rYqLft/t1-roleplay-input-native-20260913/summary.json)、[旧历史输入不变](../workspace/reports/uzi-native-alignment-20260908-rYqLft/t4-roleplay-input-native-verification-20260913/summary.json)、[原生录制与当前输入控制变量](../workspace/reports/uzi-native-alignment-20260908-rYqLft/t3-native-protocol-reference-binding-20260913/summary.json)、[最新条款账本](../workspace/reports/uzi-native-alignment-20260908-rYqLft/requirements-roleplay-input-20260913/summary.json)。账本32/68条证据核验，5项受影响绑定已重新测试；不是完成率，也没有自动把运行中的真实Agent验收勾选通过。

最新运行层证据：[总时限条款复验](../workspace/reports/uzi-native-alignment-20260908-rYqLft/requirements-request-wall-budget-20260913/summary.json)、[36组原生对照](../workspace/reports/uzi-native-alignment-20260908-rYqLft/t1-request-wall-budget-20260913/summary.json)、[旧历史输入不变](../workspace/reports/uzi-native-alignment-20260908-rYqLft/t4-request-wall-budget-verification-20260913/summary.json)、[两侧最小请求序列化](../workspace/reports/uzi-native-alignment-20260908-rYqLft/gateway-wire-offline-20260913/summary.json)。另修复参考桥的null／verdict／confidence合并差异，pristine原样前缀3例通过，旧66席录制不受影响：[合并边界审计](../workspace/reports/uzi-native-alignment-20260908-rYqLft/reference-panel-merge-20260913/summary.json)。这些不是新完整报告质量证明。

09-13 02:06最新核验：[完整OS终态与正式交付结果](../workspace/reports/uzi-native-alignment-20260908-rYqLft/t3-request-wall-budget-delivery-20260913/summary.json)为751.436秒、18次模型尝试、正式报告409。四组评委服务错误各重试耗尽，一组460.165秒成功；没有新报告信号或回测。后续执行器排队／阶段进度修复已部署，本轮150 tests／11 subtests、39项恢复边界、4项原生边界、17项UI及[36组对照](../workspace/reports/uzi-native-alignment-20260908-rYqLft/t1-executor-progress-20260913/summary.json)通过，套件不累加。[旧历史输入线上核验](../workspace/reports/uzi-native-alignment-20260908-rYqLft/t4-executor-progress-verification-20260913/summary.json)仍逐项不变；旧HTML GET200但不是新报告生成。

09-13 02:31更新：[无进展误判原红测](../workspace/reports/uzi-native-alignment-20260908-rYqLft/no-progress-signature-counterexample-20260913/summary.json)对应修复现已部署。239 tests／28 subtests和[36组原生对照](../workspace/reports/uzi-native-alignment-20260908-rYqLft/t1-recovery-transport-20260913/summary.json)通过；[条款账本](../workspace/reports/uzi-native-alignment-20260908-rYqLft/requirements-recovery-transport-20260913/summary.json)为33/68项证据核验，P3.3已覆盖，不是总体完成率。[原F组单次真实诊断](../workspace/reports/uzi-native-alignment-20260908-rYqLft/roleplay-F-transport-diagnostic-v4-20260913/summary.json)HTTP200、24位评委通过，不能反推原四组失败原因。新Manifest01M2BE4MV496CDN1S1Y3Z47Q3G的单股完整OS正在复验；[旧历史输入核验](../workspace/reports/uzi-native-alignment-20260908-rYqLft/t4-recovery-transport-verification-20260913/summary.json)仍逐项不变。详见[中文进度第52节](uzi-native-alignment-progress-20260908.md#52-内容状态恢复有界连接诊断与单组真实复验--09-13)。旧引用质量、历史辅助／19能力限制与真实基金回放继续分别核验，不能通过整批模型重跑、改评级／价位或当前数据回填伪造通过。
