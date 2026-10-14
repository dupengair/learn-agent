# 实验 1-2 web-search-agent：ReAct 轨迹（想 → 做 → 看）的代码实现流程

- **实验**：`ai-agent-book/chapter1/web-search-agent/`
- **日期**：2026-10-14
- **前置**：[01_chapter1_web-search-agent.md](01_chapter1_web-search-agent.md)（依赖检查与 Ollama/mock 改造）
- **行号基准**：含 mock 分支改造后的 `agent.py`（2026-10-14）

## 核心认识

ReAct 轨迹**不是模型一次性生成的文本**，而是**一个 while 循环逐轮"跑"出来的**：每一轮 = 一次 LLM 调用，循环由 `finish_reason` 驱动，每一步经 `_emit()` 记入 `trace` 列表（verbose 时同步打印）。

## 全景流程图

```text
main.py main()                                    # 解析参数，构造 Agent
  └─ run_single_question() / run_interactive_mode()
       └─ agent.search_and_answer(question)       # ★ ReAct 主循环  agent.py:426
            ├─ ① 重置状态: history=[system, user], trace=[], api_turns=[]
            └─ while (finish_reason is None or =="tool_calls") and iteration < 5:   # :457
                 ├─ choice = self._chat(history)           # :379  一次 LLM 调用（带 tools）
                 ├─ ② 想: reasoning → _emit("thought")     # 💭
                 ├─ 按 finish_reason 分支：
                 │    ├─ "tool_calls" → ③ 做: _emit("action")      # 🔧
                 │    │                  执行工具 → ④ 看: _emit("observation")  # 👀
                 │    │                  tool 结果追加进 history → 进入下一轮
                 │    ├─ "length"     → 输出被截断，带提示返回半截答案
                 │    └─ 其他(stop)   → ⑤ 答: _emit("answer") → return   # ✅
                 └─ 循环耗尽 → 返回"超过最大迭代次数"兜底文案
```

## 真实轨迹 ↔ 代码对照

（冒烟测试：`"月球 diameter 是多少公里？请搜索后回答"`，Ollama qwen3.5-4b:q4 + mock 搜索）

| 打印的轨迹行 | trace 类型 | 数据来源（代码位置） |
| --- | --- | --- |
| 💭 [1] 思考： 用户问的是月球的直径… | `thought` | ollama 的 `reasoning` 字段（Moonshot 为 `reasoning_content`），`search_and_answer` 循环内读取 |
| 🔧 [1] 行动： 调用 web_search 参数={"query":…} | `action` | `choice.message.tool_calls[0].function` 的 name/arguments |
| 👀 [1] 观察： （mock 示例结果…） | `observation` | mock：`run_mock_search()`（:97）；Moonshot：`_execute_formula()` Fiber 输出（:290） |
| 💭 [2] 思考： mock 不可靠，用自身知识 | `thought` | 第二轮 `_chat` 的 reasoning——模型在 history 中看到工具结果后重新推理 |
| ✅ [2] 最终答案： 3,474 公里 | `answer` | `finish_reason="stop"` 时的 `choice.message.content` |

## 四个关键机制

### 1. "想"在代码里有两层

- **显式思考**：推理模型的 `reasoning_content` / `reasoning` 字段，读出来**只做展示**——它不会追加回 `conversation_history`，连模型自己下一轮都看不到上一轮的思考原文，思考每轮重新发生。
- **隐式思考**：模型决定「调哪个工具、传什么参数」——`tool_calls` 结构本身就是思考的产物（决策）。🔧 行动行显示的参数即模型「想」的结果。

### 2. "做" = 把模型的行动意图转发给执行者

`finish_reason == "tool_calls"` 分支：
- 先把 assistant 消息（含 tool_calls）**重建为纯 dict** 存入 history。不能直接塞 SDK 的 pydantic 对象——它带 `reasoning_content` 等额外字段，回传 Moonshot 会报 400 "tokenization failed"（代码注释明确记录的坑）；
- 逐个工具：解析 arguments JSON（非法时容错为空参数，保持循环存活）→ `_execute_formula()`（Moonshot Fiber）或 `run_mock_search()`（本地 mock）。

**Agent 本体从不自己搜索**，它只做调度——这正是「模型出意图、Harness 出执行」的分工。

### 3. "看" = 工具结果作为 `role=tool` 消息回流

结果被包成 `{"role": "tool", "tool_call_id": ..., "content": 结果}` 追加进 history，`tool_call_id` 与调用一一配对。下一轮 `_chat(history)` 时模型请求里就带着这条结果——**「观察影响下一步思考」的全部机制就是普通的对话历史变长了**，没有特殊通道。

### 4. 循环终止 = `finish_reason` + 迭代上限双保险

| finish_reason | 含义 | 走向 |
| --- | --- | --- |
| `None`（首轮） | 还没调用过 | 进循环 |
| `"tool_calls"` | 模型还要用工具 | 执行工具后继续下一轮 |
| `"stop"` | 模型认为证据够了 | 返回最终答案 |
| `"length"` | 输出预算耗尽 | 返回带截断提示的半截答案（不误报成功） |
| 跑满 `max_iterations`（默认 5） | 防无限「再搜一次」 | 返回兜底文案 |

## 设计点：展示与控制分离

`_emit()`（:214）只是**记录器**：`trace.append(step)` + 按 `STEP_LABELS`（:29，💭🔧👀✅）渲染打印。`trace` 是纯展示层，**完全不参与循环控制**——控制流只看 `finish_reason`。另一个记录器 `api_turns` 存原始请求/响应（验收证据用）。读 Agent 代码时值得学：**轨迹展示和循环控制是两套独立的数据**。

## 一轮对话的 history 演化示例

```text
第 1 轮请求:  [system, user]
第 1 轮响应:  tool_calls(web_search)          → finish_reason="tool_calls"
追加到 history: [system, user, assistant(tool_calls), tool(结果)]
第 2 轮请求:  [system, user, assistant(tool_calls), tool(结果)]   ← 模型"看到"了结果
第 2 轮响应:  content=最终答案                 → finish_reason="stop" → 循环结束
```
