# chapter2/local_llm_serving — Tool Call 实现框架与执行流程分析

> 分析对象：`ai-agent-book/chapter2/local_llm_serving/`
> 命令：`python main.py --backend ollama --mode single --task "What's the weather in Tokyo?"`
> 说明：该实验实际位于 **chapter2**（chapter1 下无此实验）；本机 Ollama 兼容端点 `http://localhost:8000`，默认模型 `qwen3.5-4b:q4`。

---

## 一、总体框架：三层结构

Tool call 的实现分布在三个文件中，各司其职：

| 层 | 文件 | 核心类/函数 | 职责 |
| :-- | --- | --- | --- |
| CLI 外壳层 | `main.py` | `ToolCallingAgent`、`run_single_task`、`main` | 后端选择与初始化、流式 chunk 的终端渲染 |
| Agent 循环层 | `ollama_native.py` | `OllamaNativeAgent`（`chat_stream` / `_execute_tool_calls` / `_chat_with_think_fallback`） | ReAct 循环：调模型 → 解析 tool_calls → 执行 → 回填结果 → 再调模型 |
| 工具层 | `tools.py` | `ToolRegistry` | 工具注册、OpenAI 格式 schema 导出、工具实际执行 |

另有一个未走主路径的对照实现 `OllamaOpenAICompatible`（`ollama_native.py:403`，走 OpenAI 兼容端点），`--backend ollama` 主链路用的是 **Ollama 原生 API**（`ollama.Client.chat`）。

### 工具层：`ToolRegistry`（tools.py:16）

注册了 4 个工具（`_register_default_tools`，tools.py:23）：

| 工具 | 实现 | 数据来源 |
| --- | --- | --- |
| `get_current_temperature` | 先 geocoding 拿坐标，再查当前天气 | Open-Meteo 免费 API（无需 Key）；失败时降级为随机模拟数据（tools.py:228-249） |
| `get_current_time` | `zoneinfo` 时区转换，含 PST/JST 等缩写→IANA 别名表 | 本地系统时间 |
| `convert_currency` | 双边汇率经 USD 中转换算 | 内置**模拟汇率表**（tools.py:340） |
| `code_interpreter` | `exec()` 在受限命名空间执行 Python，捕获 stdout/stderr，并从 `result`/`total` 等常见变量名提取返回值 | 进程内执行 |

三个关键方法构成工具协议：

- `register_tool(name, function, description, parameters)`（tools.py:103）— 把可调用函数与 JSON Schema 参数描述绑定存入 `self.tools` 字典；
- `get_tool_schemas()`（tools.py:111）— 导出 **OpenAI function-calling 格式**的 schema 列表（`{"type": "function", "function": {name, description, parameters}}`）。Ollama 原生 API 与该格式兼容，`_convert_tools_to_ollama_format`（ollama_native.py:42）直接透传；
- `execute_tool(name, arguments)`（tools.py:125）— 查表 → `function(**arguments)` → dict/list 结果 `json.dumps` 序列化；**工具不存在或抛异常都不向上抛，而是返回 `{"error": ...}` JSON**（tools.py:128-134）。错误被当作普通观察结果回传给模型，模型有机会自我纠正，这是 tool-call Agent 的标准容错设计。

---

## 二、命令执行链路

```
main()                                          main.py:517
 └─ argparse: --backend ollama → backend="ollama"（非 auto，跳过平台探测）
 └─ ToolCallingAgent(backend="ollama")          main.py:598
     └─ _initialize_backend → _init_ollama      main.py:113
         ├─ ollama.Client(host="http://localhost:8000").list()  存活检查
         ├─ model = DEFAULT_OLLAMA_MODEL = "qwen3.5-4b:q4"      ollama_native.py:17
         └─ self.agent = OllamaNativeAgent(model=model)
             ├─ self.client = ollama.Client(...)                原生 API 客户端
             ├─ self.tool_registry = ToolRegistry()             注册 4 个工具
             └─ self.conversation_history = []                  消息历史（本实验的状态中心）
 └─ run_single_task(agent, task, stream=True)   main.py:260
     └─ agent.chat(task, stream=True)           → OllamaNativeAgent.chat → chat_stream
```

`--mode single` + 带 `--task` 时走 `main.py:648-650` 分支；`--stream` 默认开启（`--no-stream` 才关闭），所以天气查询走的是**流式 ReAct 循环** `chat_stream`（ollama_native.py:217）。

`run_single_task`（main.py:260）本身不含任何 Agent 逻辑，只是消费 chunk 流并渲染：`thinking` → 灰色🧠、`tool_call` → 🔧 打印工具名+参数、`tool_result` → ✓ 打印结果、`content` → 🤖 正文。

---

## 三、`chat_stream` 的 ReAct 循环执行流程（核心）

`chat_stream`（ollama_native.py:217-395）是本实验 tool call 的心脏。设 `max_iterations = 10`（ollama_native.py:236）防止无限循环。

### 迭代 1：模型决定调用天气工具

**① 组装请求。** 用户消息入历史：

```python
conversation_history = [{"role": "user", "content": "What's the weather in Tokyo?"}]
```

随后调用 `_chat_with_think_fallback(model, messages, tools, options={"temperature": 0.3}, stream=True)`（ollama_native.py:244）。注意 **`tools` schema 每一轮都随请求发送**，让模型在任何轮次都能继续发起工具调用（链式多步工具使用的前提）。

**② `think=True` 回退机制**（`_chat_with_think_fallback`，ollama_native.py:50）：先带 `think=True` 调用（qwen3.5 支持思考模式）；若模型返回 400（不支持思考的模型如 qwen2.5/llama3.2）则去掉 `think` 重试一次，并把该模型记入 `_think_disabled` 缓存集合，后续调用零开销直通。

**③ 流式 chunk 解析**（ollama_native.py:259-346）。逐 chunk 检查 `message` 里的三种字段：

- `thinking` 字段 → 收集并 yield `{"type": "thinking"}`（qwen3.5 原生思考流）；
- `content` 字段 → 常规正文直接 yield；若内联出现 `<think>...</think>` 标签则做状态机解析（`in_thinking` 标志，兼容非原生思考流的模型）；
- `tool_calls` 字段 → **只收集不执行**。参数若为字符串先 `json.loads`；带去重保护（ollama_native.py:340-345，某些服务端会在每个 chunk 重发累积的 tool_calls 列表），加入 `pending_tool_calls` 并 yield `{"type": "tool_call"}` 供前端展示。

对本次任务，模型生成（经 Ollama 服务端解析出结构化 tool_calls）：

```json
{"function": {"name": "get_current_temperature",
              "arguments": {"location": "Tokyo", "unit": "celsius"}}}
```

**④ 流结束后回填 assistant 轮次**（ollama_native.py:352-362）。把**完整的** assistant 消息（含 `content`、`tool_calls`、`thinking`）写入历史 —— 保留完整轮次是 Ollama 多轮消息契约的要求（测试 `test_streaming_history_preserves_assistant_turn_before_tool_results` 专门守护这一点）。

**⑤ 并行执行工具**（`_execute_tool_calls`，ollama_native.py:79）。单工具调用直接顺序执行；多个 tool_calls 时用 `ThreadPoolExecutor(max_workers=n)` 并发执行 —— 依据是这些调用由模型在同一次生成中一次性产出、彼此看不到对方结果，**构造上相互独立**；`executor.map` 保证结果顺序与调用顺序对齐。

本次实际执行的 HTTP 链路（`get_current_temperature`，tools.py:138）：

```
GET https://geocoding-api.open-meteo.com/v1/search?name=Tokyo      → 取经纬度
GET https://api.open-meteo.com/v1/forecast?latitude=..&longitude=..
       &current=temperature_2m,relative_humidity_2m,weather_code,wind_speed_10m
→ 返回 {location, temperature, unit, conditions, humidity, wind_speed, ...}
```

其中天气码 `weather_code`（WMO 编码）经映射表翻译为 "clear sky"/"light rain" 等人类可读条件（tools.py:196-207）。

**⑥ 工具结果回填**（ollama_native.py:366-372）。每个结果 yield `{"type": "tool_result"}` 渲染，并追加 `{"role": "tool", "tool_name": ..., "content": <JSON字符串>}` 到历史。

### 迭代 2：模型消化结果，产出最终回答

带完整历史（user → assistant(tool_calls) → tool(result)）再次流式调用。模型看到天气数据后生成纯文本回答，本轮无 `tool_calls` → 走 ollama_native.py:376-386 分支：assistant 消息入历史，`break` 退出 ReAct 循环。

若 10 轮耗尽仍有工具调用，循环外补发 `{"type": "error", "content": "Maximum iterations reached in ReAct loop"}`（ollama_native.py:394-395）。

### 时序图

```
用户任务 "What's the weather in Tokyo?"
   │
   ├─[迭代1] history += user 消息
   │    client.chat(stream=True, tools=4个schema, think=True)
   │    ├─ chunk(thinking) ──→ yield 🧠
   │    ├─ chunk(tool_calls) ─→ yield 🔧（仅展示，暂存 pending_tool_calls）
   │    └─ 流结束 → history += assistant{content, tool_calls, thinking}
   │         _execute_tool_calls()  ← ThreadPoolExecutor（多工具时并行）
   │              ├─ geocoding-api.open-meteo.com  → Tokyo 坐标
   │              └─ api.open-meteo.com/forecast   → 温度/湿度/风速/天气码
   │         history += tool{result}
   │
   ├─[迭代2] client.chat(stream=True, tools=仍随请求发送)
   │    └─ chunk(content) ──→ yield 🤖 正文（无 tool_calls）→ break
   │
   └─ history 最终形态: [user, assistant(tool_calls), tool(result), assistant(回答)]
```

---

## 四、关键设计点

1. **ReAct 循环而非单次往返**：`chat_stream` 用 `while` 循环包裹，模型每轮自由决定"再调工具"还是"给出答案"，天然支持多步任务（如示例任务里的五城市对比 + 华氏换算 + 求平均，需要 temperature×5 → code_interpreter×2 的链式调用）。对照：非流式 `chat`（ollama_native.py:110-215）只做**固定两段式**（调一次→执行一轮工具→再调一次），不循环，只能处理单轮工具调用。

2. **流式下工具调用延迟执行**：tool_calls 在流中只收集（yield 给前端做展示），流结束后才统一执行 —— 这是并行执行的前提，也保证了 assistant 轮次先于 tool 结果完整入历史。

3. **错误即观察**：工具层任何异常都转成 `{"error": ...}` 字符串回到模型（tools.py:133-134），循环不中断；连 ReAct 循环顶层的意外异常也只是 yield error 后 break（ollama_native.py:388-391）。

4. **思考模式兼容层**：`_chat_with_think_fallback` 用"每模型一次性缓存"的方式抹平支持/不支持 thinking 的模型差异，保证同一套 Agent 代码可换任意 Ollama 模型。

5. **`<think>` 双通道解析**：qwen3.5 走原生 `thinking` 字段；`content` 内联 `<think>` 标签的状态机解析是给不支持原生思考字段的模型/服务端准备的兜底（ollama_native.py:272-320）。

6. **工具选择交由模型**：schema 中 `convert_currency` 的描述写了 "You MUST use this tool..."（tools.py:66）、`code_interpreter` 同样带强制语气 —— 用提示词工程引导模型优先走工具而非凭参数记忆回答（4b 小模型尤其容易幻觉汇率/复利结果）。

---

## 五、边界与注意点

- **`convert_currency` 是模拟汇率**（硬编码表，tools.py:340），实验结论中不应把汇率换算结果当成真实市场数据。
- `get_current_temperature` 依赖外网访问 Open-Meteo；断网时静默降级为**随机模拟数据**（返回体带 `note: "Simulated data (API unavailable)"`，tools.py:228-249），复现实验时注意辨别。
- 相关测试：`test_parallel_tools.py`（并行执行与顺序保持）、`test_ollama_thinking.py`（think 回退与历史保留）。

### 附：如何判别一次天气查询是真实 API 还是打桩数据（2026-10-22 实测验证）

对照 tools.py 两条返回路径的字段差异，看 tool_result 的 JSON 即可：

| 特征 | 真实 API 路径（tools.py:214-225） | 模拟降级路径（tools.py:242-249） |
| --- | --- | --- |
| `note` 字段 | **无** | **必有** `"Simulated data (API unavailable)"` |
| `coordinates`/`humidity`/`wind_unit`/`source` | 有 | 无（仅 6 个字段） |
| `timestamp` 格式 | Open-Meteo 当地时间**整点**，如 `2026-10-22T17:00` | `datetime.now().isoformat()`，带微秒 |

最快判别法：返回体带 `source: "Open-Meteo"` 且无 `note` → 真实；带 `note` → 打桩。辅助旁证是延迟——真实查询有 geocoding+forecast 两次外网请求的秒级延迟，模拟数据瞬时返回。
