# 历史不可用披露的原生输入适配修复

## 问题与边界

真实 `688012.SH / 2026-09-02` 报告运行 `01M2G17SQEA33K6E7XD94GPR8Z` 已完成模型分析，但原生 `check_empty_dims` 把 `19_contests.data={}` 判成 critical。本地已落库“当前状态来源不能回补历史”的决定，却只放在原生不读取的外层扩展中。

原生采集器在拿不到组合时仍返回来源状态及 `_note`。本次对齐这一缺失表达，不声称原生存在历史采集能力，也不把今天的诊断伪装为当日的市场观测。

## 实现

1. 新增 `uzi-dim-result-v8`，v1—v7 原规则保留；新 Manifest 固定 v8。不得改写已冻结输入。
2. 唯一转换函数识别专用 `cognitrade-historical-disclosure-v1` 生产者、`data_gap` 状态、明确 PIT 安全的历史不可用模式，以及一致的原因、消息、目标日期、是否尝试重建和评分排除标志。
3. 合格的历史可用性决定转换为原生可读 `_note` 与明确标注非研究事实的 `_historical_availability`。不添加持仓、零条统计、成功查询、error 或 timeout。
4. 其他上游空结果、失败、verified-empty 绑定和 PIT 拒绝保持不变。不是通用“填个对象过门禁”。实际采集状态、严格完整度、评分特征和原生覆盖率不改善。
5. 预检、Bundle 物化、报告、量化输入共同识别 v8；旧版本附加输入和证据绑定不改变。

## 验证

- 必要本地回归 292 项及 11 个子测试通过；覆盖真实披露生产者、未证明／矛盾披露、当前数据误回填、异常空输入、旧 Manifest、绑定 hash、评分特征与覆盖率不变。
- 专门验证：把所有维度都换为披露说明仍被原生财务覆盖率门禁拦住。
- 部署后新 Manifest `01M2G38GHS6K3SAYB8E2M5RWDA`，hash `0381182f54bf2471761ae780f2cbd15f56faa9747af78e9de321cb5da8a991ed`。仍绑定旧股票 Bundle `01M2G0R4W3FSPGAQK3ANTC10P7`、同样的 22 个快照和证据关系。
- 真实原始输入比较只有 `19_contests` 发生说明变化；原生空维度检查 1 critical → 0。
- 新 `medium/full / gpt-5.6-luna` 报告运行 `01M2G38X0KT52QR61EJH8HGPZ0`；终态以 `workspace/reports/uzi-historical-disclosure-v8-20260914/observation/summary.json` 为准，不将输入预检通过等同于正式报告通过。
- API 镜像 `sha256:999143a2a4715735c5ce13029f950480fda7917576059cde0d1fb019a5feb9b1`，无需迁移，部署前无活跃任务，未自动重启旧暂停任务。未自动采集、订阅、提交或推送。

本次范围是修复历史缺失的输入表达并验证单股新报告。基金十股及报告条件回测是否完成，必须独立记录。

## 重测终态

运行结束于 gated，1392.224 秒。第一轮原生自审为 0 critical，最终也没有历史持仓空维度错误。定性研究 3/3、评委 5/5 最终完成；第二轮正文 `narrative_core` 在 600 秒处发生 `llm_transport_timeout`，耗尽记录保留至终态，导致 Agent 分析缺失，HTML 409。未启动回测，非人工取消。

本次历史披露适配修复完成；正式报告与基金 E2E 尚未完成。详细记录：`workspace/reports/uzi-historical-disclosure-v8-20260914/修复与重测结果.md`。
