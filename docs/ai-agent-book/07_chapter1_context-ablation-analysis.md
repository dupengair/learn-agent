# 实验 1-1 上下文消融：5 组生成结果差异分析（ollama / qwen3.5-4b）

- **证据文件**：`ai-agent-book/chapter1/context/validation/real_20261015T084659Z/evidence.json`
- **运行方式**：`python run_experiment_1_1.py --provider ollama`（base_url `http://localhost:8000/v1`，模型 `qwen3.5-4b:q4`）
- **任务**：四季度收入（Q1 USD / Q2 EUR / Q3 GBP / Q4 JPY）换算美元后求年度总额与季度均值（`task_variant: guarded`）
- **对照基准**：`chapter1/context/README.md` 的「预期行为」表

## 一、五组结果总览

| 实验组 | 移除组件 | 迭代 | 工具行动 | 重复行动 | outcome | 最终答案 |
|---|---|---:|---:|---:|---|---|
| **full** | 无（基线） | 4 | 5 | 0 | `correct` | ✅ 9,602,895.73 / 2,400,723.93 |
| **no_history** | 历史消息 | 5（触顶） | 15 | **12** | `no_terminal_response` | ❌ 无答案 |
| **no_reasoning** | 推理内容 | 3 | 4 | 0 | `correct` | ✅ 数值正确（反而快一轮） |
| **no_tool_calls** | 工具定义 | 1 | 0 | 0 | `unsupported_numbers` | ⚠️ 9,600,000.00 / 2,400,000.00（编造） |
| **no_tool_results** | 工具执行结果 | 5（触顶） | 11 | 5 | `no_terminal_response` | ❌ 无答案 |

三种质上不同的失败形态：**失忆死循环**（no_history）、**盲目死循环**（no_tool_results）、**看似合理的编造**（no_tool_calls）。

## 二、各组生成轨迹的具体差异

### full — 基线：推理逐轮建立在前一轮的观测之上

4 轮推理清晰递进：

1. 规划：「I need to: 1. Convert Q2 (2.1 million EUR)…」
2. 汇总时**引用工具返回的具体数字**：「Q2: 2,282,608.70 USD, Q3: 2,278,481.01 USD, Q4: 2,541,806.02 USD」
3. 计算季度均值
4. 输出与 `expected_numbers` 完全一致的答案

5 次工具调用 0 重复——每一步都有新进展。这是 ReAct 闭环（思考→行动→观测）完整时的形态。

### no_history — 失忆死循环：每轮从零开始

5 轮的 reasoning **几乎逐字相同**（每轮都是「I need to: 1. Convert Q2 (2.1 million EUR)…」）。因为 `_prepare_messages_for_api()` 只发送 system + 当前任务，模型每一轮都失忆、都重新做同一份规划、发出同样的换汇调用：15 次工具行动中 12 次完全重复。它永远走不到「汇总」那一步，因为**它永远不知道自己已经换算过了**。5 轮触顶，无终止回答。

### no_reasoning — 本例未退化，反而更快

剥离 `reasoning_content` 后模型仍 3 轮完成（比 full 少一轮），4 次工具调用 0 重复，数值正确。且最终回答格式更详细（逐季度列明细）——它把原本放在推理里的内容直接写进了回答。说明推理内容的缺失伤害是**概率性的**（依赖模型与任务），四类上下文中它冗余度最高。README 亦注明「预期行为表是设计预期，不是实测」。

### no_tool_calls — 幻觉：用参数记忆填补观测空缺

请求中没有 `tools` 参数，模型 1 轮直接作答、0 次工具行动，给出：

> Annual Total: **9,600,000.00** USD, Quarterly Average: **2,400,000.00** USD

真实值是 9,602,895.73——误差仅 0.03%，格式完美、整数平滑。模型没有实时汇率，就**用训练时记忆的平均汇率凑数**，且不声明这一点。`groundedness.verdict = ungrounded`：答案中 2 个数量全部无法追溯到工具观测。这是三种失败里最危险的——不是崩溃，而是「看起来正确的错误答案」。

### no_tool_results — 盲目死循环：知道没结果，不知道为什么没结果

与 no_history 同样触顶无答案，但轨迹**性质不同**。工具调用消息保留、仅结果置空，于是模型知道「我调过了」，却永远看不到「调出了什么」：

- t1：「The currency conversion tool calls didn't return any output. **Let me try again**…」
- t2：「returned empty results. Let me try again with the exact values…」
- t4：「not returning any output. Let me try a **different approach** — use the calculate function to do the conversions myself…」

它在换格式重试、换工具重试，但无论怎么换，观测永远是空的。它能察觉「结果没来」，却无法推断「结果不会来」。11 次行动 5 次重复（重复率低于 no_history，因为一直在换姿势），5 轮触顶。

## 三、差异说明了什么

1. **上下文组件各有不可替代的职责，缺哪个坏哪个方向**（对应 Agent = LLM + 上下文 + 工具）：
   - 历史消息 = **行动记忆**（我做过什么）→ 缺失 → 机械重复，永远无法推进；
   - 工具结果 = **事实观测**（世界发生了什么）→ 缺失 → 行动-观测闭环断裂，盲目重试；
   - 工具定义 = **能力边界**（我能做什么）→ 缺失 → 模型退回参数记忆，用内部知识填补外部世界的空缺，产生幻觉；
   - 推理内容 = **内部规划**（我打算做什么）→ 本例缺失不致命，重要性因模型/任务而异。

2. **幻觉的机制被清晰呈现**：幻觉不是胡言乱语，而是用参数知识**自信地填补本应由工具提供的缺失观测**，输出格式完整、误差微小、不声明不确定性。`success`/`completed`/`task_success` 三个指标都抓不住它，必须靠专门的 `outcome=unsupported_numbers` 与 groundedness 校验。

3. **移除不同组件产生的是质变而非程度变化**：同一任务、同一模型，仅改上下文就得到三种不同的失败模式。消融实验的价值正在于识别每个组件各自支撑哪一种行为能力。

4. **harness 工程启示**：
   - ReAct 轨迹（thought/action/observation）必须完整回传，这是循环的命脉；
   - 要为「观测缺失」设计防御：迭代上限（本实验正是靠上限 5 轮止损）、以及结果缺失时的显式信号——README 中 `--hidden-result marker` 变体会改变模型行为，因为占位符本身是一个「这里有观测被藏了」的信号，拿走信号与拿走观测不是同一个消融；
   - 不能只信任格式正确的答案，答案中的数字要能追溯到工具观测（grounding 校验）。

## 四、与设计预期的对照（本次运行的特殊点）

- `no_reasoning` 本次**未**出现「方法无结构、易出错」的退化（`manuscript_behavior_claims.without_reasoning_degraded: False`），与书中预期不同——单次单模型运行不能把书中预期现象当成必然结论（这正是 EXPERIMENT_LEDGER 存在的意义）。
- 其余各组与 README「预期行为」表一致：full 完整成功、no_history 重复操作触顶、no_tool_calls 用记忆作答、no_tool_results 反复重调触顶。
- token 用量：prompt 20,057 / completion 6,556，cached_prompt_tokens 16,462——静态前缀缓存命中率高，与第 2 章 KV Cache 主题衔接。
