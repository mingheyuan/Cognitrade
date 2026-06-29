# Distiller Prompt Template Spec

## 1. 文档目标

这份文档用于冻结 Distiller 的 Prompt 模板结构、负向约束、输出约束与验收标准。

它不是运行时代码，也不是业务总方案，而是供后续实现 `Distiller` 时直接照着落地的 Prompt 合同文档。

相关文档：

- 逻辑主方案：[plan.md](./plan.md)
- 分阶段方案：[phase_plan.md](./phase_plan.md)
- Distiller 方案：[distiller_agent_plan.md](./distiller_agent_plan.md)
- 实现方案：[agent_system_implementation_plan.md](./agent_system_implementation_plan.md)

---

## 2. 目标能力

Prompt 模板第一版只服务以下目标：

- 从 KOL 文本中拆出主张、证据、风险、催化
- 区分作者观点与引用观点
- 保留原文可追溯锚点
- 输出与 `DistillationSummary / LogicBlock / DistilledDimension / DistillationResult` 对齐的结构化结果
- 在不确定时显式返回风险，而不是强行补全因果链

---

## 3. 模板结构

Distiller Prompt 第一版固定拆成 6 段。

### 3.1 System Role

职责：

- 定义模型身份为“结构化研究蒸馏器”
- 强调目标不是写摘要，而是提取可复核逻辑单元
- 强调不得编造标的、时间范围、方向和因果链

### 3.2 Task Objective

职责：

- 说明当前输入材料类型
- 说明需要完成的蒸馏任务
- 说明本次任务只处理提供的材料，不补外部知识

### 3.3 Extraction Rules

职责：

- 先识别内容类型
- 再区分作者观点、引用观点、背景说明
- 再切分逻辑块
- 再提取维度
- 再输出风险与置信度

### 3.4 Negative Rules

职责：

- 明确不能做什么
- 避免模型把泛化理解包装成结构化结果

### 3.5 Output Schema Reminder

职责：

- 明确输出必须符合结构化对象语义
- 强调字段完整性优先于措辞优美

### 3.6 Confidence And Risk Rubric

职责：

- 给出高、中、低置信度判定准则
- 给出必须触发 `needs_review` 的情况

---

## 4. 固定负向约束

Prompt 第一版必须明确以下禁止项：

- 不把情绪表达当成维度
- 不把引用内容默认算作作者主张
- 不把价格结果倒推成原文因果
- 不在证据不足时编造时间范围、标的或方向
- 不把背景介绍伪装成结论
- 不把模糊措辞强行标准化成高置信主张

---

## 5. 固定处理顺序

Prompt 第一版固定引导模型按以下顺序执行：

1. 识别材料类型
2. 标记引用与作者新增内容
3. 切分逻辑块
4. 分类逻辑块
5. 提取候选维度
6. 归并到标准维度
7. 输出风险与置信度

如果其中任一步无法可靠完成，允许降低置信度并标记 `needs_review`。

---

## 6. 输入变量合同

Prompt 模板应至少支持以下输入变量：

| 变量 | 说明 |
| --- | --- |
| `source_id` | 来源标识 |
| `source_type` | 材料类型 |
| `author` | 作者 |
| `platform` | 平台 |
| `published_at` | 发布时间 |
| `source_url` | 原始链接 |
| `raw_text` | 原始文本 |
| `candidate_symbols` | 可能相关标的 |
| `candidate_sectors` | 可能相关行业或主题 |
| `quality_hints` | 上游质量提示 |

---

## 7. 输出对象合同

Prompt 第一版必须对齐以下对象：

- `DistillationSummary`
- `LogicBlock`
- `DistilledDimension`
- `DistillationResult`

输出要求：

- 先保证字段语义正确
- 再保证字段填充完整
- 不允许为了凑满字段而编造内容

---

## 8. 置信度规则

### 8.1 高置信度

满足以下条件时才可输出高置信度：

- 主张清晰
- 证据对应明确
- 作者与引用归属明确
- 标的或行业映射明确
- 时间范围明确或可从原文稳定推出

### 8.2 中置信度

适用于：

- 主张存在一定抽象化
- 证据相关但不完全充分
- 时间范围或对象存在轻微歧义

### 8.3 低置信度

适用于：

- 引用归属不清
- 因果方向不清
- 证据不足
- 上下文缺失
- 只能识别到模糊情绪或泛化结论

---

## 9. needs_review 触发条件

满足以下任一条件时，Prompt 应显式输出 `needs_review = true`：

- 作者观点与引用观点无法可靠区分
- 因果方向无法可靠判断
- 标的映射存在明显歧义
- 时间范围无法判断
- 低信息密度文本被强行切出主张风险较高
- 长文本预筛后上下文明显不足

---

## 10. 模板骨架

以下骨架用于后续实现，不是最终完整 Prompt 文案。

```text
[System Role]
你是一个结构化研究蒸馏器。你的任务不是总结文章，而是提取可复核的逻辑块、维度和风险。

[Task Objective]
请处理以下材料，并仅基于提供内容完成蒸馏，不补外部知识。

[Extraction Rules]
1. 识别材料类型
2. 区分作者观点、引用观点、背景叙述
3. 切分逻辑块
4. 标记 claim/evidence/risk/catalyst
5. 提取维度并归并
6. 输出风险与置信度

[Negative Rules]
- 不要把引用默认当作作者观点
- 不要编造标的、方向、时间范围
- 不要把情绪表达当维度
- 不要倒推因果

[Output Schema Reminder]
输出必须对齐 DistillationSummary、LogicBlock、DistilledDimension、DistillationResult。

[Confidence And Risk Rubric]
当因果、引用归属、对象映射、时间范围不清时，降低置信度并标记 needs_review。
```

---

## 11. 验收要求

Prompt 模板在进入运行时前，至少通过以下检查：

- 短帖样例
- 长帖样例
- 回复链样例
- 作者观点与引用观点区分样例
- 因果方向易混淆样例
- 低信息密度样例
- structured output 成功率样例

如果这些检查不过，优先修改 Prompt 模板或输出合同，不扩大下游链路。
