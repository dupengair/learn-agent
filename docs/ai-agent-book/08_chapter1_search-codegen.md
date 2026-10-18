# 实验 1-3 search-codegen 本地化改造（Ollama /v1/responses + 本地工具降级）

> 日期：2026-10-16 · 模型：`qwen3.5-4b:q4` @ Ollama 0.35.0（localhost:8000）
> 改造对象：`ai-agent-book/chapter1/search-codegen/`（实验 1-3：GPT-5.6 原生 Deep Research）

## 1. 这个实验和 context 的本地化有什么本质不同

实验 1-1（context）走 **Chat Completions + 客户端工具**：工具由我们自己的代码定义和执行，换 provider 只是换 base URL。实验 1-3 走 **Responses API + 服务端托管工具**：请求体只写 `{"type": "web_search"}`，搜索和 Python 沙箱都在**提供商服务器**执行，验收对象是服务端回执（`web_search_call` / `code_interpreter_call` output items + URL 引用）。

所以本地化不是"换个 provider"能解决的——Ollama 背后没有搜索服务和代码沙箱，托管工具必然要**降级为本地 function 工具 + 客户端执行循环**。幸运的是协议外壳保得住：Ollama 0.35 原生说 `/v1/responses`。

## 2. Ollama 0.35.0 Responses API 能力实测（改造前探测）

| 能力 | 实测 | 备注 |
| :-- | :-- | :-- |
| `POST /v1/responses` 基本请求 | ✅ 200 | 返回标准 `output` 数组（`reasoning` + `message` items），qwen3.5 的 thinking 走 `reasoning` item |
| function 工具定义 + 调用 | ✅ | `{"type": "function", "name": ..., "parameters": ...}`（Responses 扁平格式，非 Chat Completions 嵌套格式），模型返回 `function_call` item 带 `call_id` |
| `function_call_output` 结果回传 | ✅ | input 数组带完整历史（user message + function_call + function_call_output），模型能读工具结果继续作答 |
| `previous_response_id` 纯文本多轮 | ✅ | "记住我叫小明→我叫什么" 正常 |
| **`previous_response_id` + `function_call_output` 数组** | ❌ **HTTP 500** | 决定性发现：工具循环**不能用**服务端状态做增量续跑，必须自己维护全量 input-items 数组 |
| `max_output_tokens` / `tool_choice:"auto"` | ✅ | 正常接受 |
| `usage` 字段 | ✅ | 标准 `input_tokens` / `output_tokens` / `total_tokens` 结构 |
| 托管工具形状 `{"type": "web_search"}` | ❌（必然） | Ollama 无服务端搜索/沙箱，这是整个改造要解决的问题 |

## 3. 改造方案（4 个决策点）

| 决策 | 选择 | 理由 |
| :-- | :-- | :-- |
| web_search 本地实现 | Mock 固定样本 | 零网络依赖可复现；与 web-search-agent 的 mock 先例一致 |
| 代码执行沙箱 | subprocess + 30s 超时 | context 的 AST 白名单只能算单表达式，跑不了 haversine/MACD 多行代码 |
| 验收语义 | 放宽 + `local_degraded` 戳记 | 本地永远产生不了托管回执；能验证工具循环与计算正确性，不验证搜索真实性 |
| 改动落点 | 现有文件加 ollama 分支 | 单一入口，与 context 改造方式一致，openai/dashscope 路径完整保留 |

文件级改动：

- **`config.py`**：`OLLAMA_BASE_URL/OLLAMA_API_KEY/OLLAMA_MODEL` + `resolve("ollama")`
- **`local_tools.py`（新建）**：mock 语料（东盟坐标 + 60 日合成 BTC 收盘价，写死保证可复现）、`run_python_sandbox`（临时目录 + timeout + stdout 截断防撑爆 4B 上下文）、`execute_local_tool` 分发（坏参数返回错误 JSON 让模型自纠）
- **`agent.py`**：显式 `provider` 参数（localhost 推断兜底）；ollama 分支剥离 `reasoning.effort`/`text.verbosity`/`store`/`background`/`tool_choice`；`_process_request_with_local_tools` 本地工具循环（最多 7 轮，每轮 function_call → 本地执行 → function_call_output 回填）；**无状态续跑**：跨调用历史用 `self.local_items` 数组（因为 previous_response_id 500）
- **`main.py`**：`--backend` 默认 `ollama`；ollama 下 `--reasoning/--verbosity` 提示后忽略
- **`run_experiment_1_3.py`**：`--backends` 默认 `["ollama"]`；本地任务文本变体（"hosted" 措辞 → 本地工具名）；validate 函数 ollama 分支（托管回执检查 → 本地往返检查；`closest_pair_matches_independent_reference` 与澄清先于工具两项语义不变）；evidence 加 `local_degraded` 戳
- **一个坑**：仓库级测试 `tests/test_ch1_search_codegen_null_response.py` 用 `importlib` 单文件加载 `agent.py`，不走包路径——顶层 `from local_tools import ...` 会炸，要按 `config.py` 处理 agentbook 的同款 try/except + sys.path 回退

## 4. 实测结果（如实记录）

### 4.1 ASEAN 首都距离任务：✅ 通过

single 模式一轮跑通：`web_search` 查坐标（mock）→ `run_python` 枚举 45 对 haversine → 找出最近对。

**数值核对（重要发现）**：本地 qwen3.5-4b 算出 **吉隆坡—新加坡 309.25 km**；仓库独立参考 `independent_asean_reference()` = **309.3 km**（半径 6371.0088 vs 模型用 6371，差 0.05 km）。**数值严格一致**。而 README/LEDGER 里 DashScope 2026-07-31 历史记录是 316.35 km——LEDGER 原文其实写清楚了：316.35 是"the same pair"（最近对相同），括号里标注独立参考是 309.3 km。也就是说 DashScope 那次的**距离数值**与标准坐标算的不符（可能坐标来源不同），本地这次反而和参考代码完全对上。读历史记录时"最近对相同"和"距离数值相同"是两件事。

模型生成的 haversine 代码完全正确（R=6371、atan2 形式、45 对枚举），还主动输出了最远对（雅加达—内比都 3119 km）和前五名排序。

### 4.2 澄清场景（比特币模糊任务）：❌ 未通过——真实的模型行为

验收要求第一轮**先澄清**（数据源、指标）再调工具。实测 **2/2 次 qwen3.5-4b 都跳过澄清直接干活**：调用 3 次工具（含伪造 mock 语料里不存在的日期"2024-01-01 至 2024-02-29"）直接输出完整技术分析。第一轮不算澄清 → 续跑从未发生 → `clarification_passed: false` → 总体 `passed: false`。

两次对比有个有意思的差异：
- 第 1 次（验收脚本内）：伪造日期，未声明数据来源；
- 第 2 次（single 复跑）：同样不澄清，但**主动声明"以上分析基于本地合成样本数据集（非真实市场数据）"**——system prompt 里"如实标注来源"的要求被遵守了，clarify-first 规则没有。

为什么不澄清？两个因素叠加：(a) 4B 模型对 system prompt 行为约束的遵循能力弱（对照：LEDGER 记录 qwen3.7-plus 也要显式 clarify-first 提示才先澄清，GPT-5.6 级别才能稳定做到）；(b) mock 数据**太容易拿到**——一查就有现成 60 天数据，模型没有"缺信息"的动机去澄清。这正是本书反复强调的观点：agent 行为 = 模型能力 × 上下文设计，两个变量在这里同时变了。

### 4.3 最终验收状态

```
run_experiment_1_3.py --backends ollama  →  passed: false
  asean_passed:         true   (local_degraded)
  clarification_passed: false  (模型未先澄清，2/2 稳定)
  证据: validation/runs/real_20261016T125641Z/
```

**没有为凑绿色结果改代码**（比如第一轮强制禁用工具能"造出"澄清场景通过，但那是伪造模型行为）。验收不通过本身就是这次本地化最有信息量的结果：它精确暴露了 4B 本地模型与书中 qwen3.7-plus/GPT-5.6 的能力差距。

## 5. local_degraded 验收语义（诚实边界）

| | 能验证 ✅ | 不能验证 ❌ |
| :-- | :-- | :-- |
| 内容 | function 工具往返完整、澄清先于工具（协议行为）、45 对计算与独立参考一致 | 搜索数据真实性——mock 语料与独立参考坐标**故意同源**（`local_tools.py` 模块注释有声明） |

证据 JSON 带 `local_degraded` 戳 + README 明示，不冒充托管工具验收。

## 6. 学习心得

1. **同一协议、不同工具范式**：Responses API 的价值在 typed output items 和服务端托管工具；Ollama 证明了协议层可以完整复用，托管层不能。对比实验 1-2（web-search-agent 用 Chat Completions + mock）和本实验（Responses + function 降级），两条本地化路线其实是同一个思想的两种表达：**把"能力"从服务端搬回客户端，同时如实标注搬了什么、丢了什么**。
2. **探测先行**：动手前 4 组 curl 探测（基本请求 / function 调用 / 工具回传 / previous_response_id 组合）直接决定了架构——如果没探测出 500 那个坑，工具循环会写成 previous_response_id 增量模式然后线上炸。
3. **验收是实验的良心**：`run_experiment_1_3.py` 的验收哲学（"没有工具回执就不算通过"）在降级模式下依然守住了底线——最近对必须对独立参考、澄清必须真实发生。本地跑不过就是跑不过，记录下来比绿了更有价值。
4. **小模型的 system prompt 遵循是概率不是开关**：与 1-1 消融实验里"guard 是概率移动不是切换"的结论互相印证。
