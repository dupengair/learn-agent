# chapter2/kv-cache 六模式实现解析与《实验 2-3》正文验证

> 日期：2026-10-30
> 代码：`ai-agent-book/chapter2/kv-cache/agent.py`（核心 ~380 行）+ `main.py`
> 对照材料：正文 `book/chapter2.md` L540-554《实验 2-3 ★★：常见的错误上下文管理模式》；
> Kimi K2.6 归档实测（`result_*_kimi_k2_6.json`，默认重任务）；本地 Ollama qwen3.5-4b 实测（`comparison_ollama_*_20261030_152137.json`，小任务，见 [15] 号文档）

## 一、实验骨架：所有模式共享的执行循环

`KVCacheAgent.execute_task()` 是一个标准 ReAct 循环，六种模式共用同一骨架，**唯一变量是每轮请求消息列表的构造方式**：

```python
if self.mode == KVCacheMode.CORRECT:
    if iteration == 1:
        messages = self._format_messages(original_task)   # 只建一次
else:
    messages = self._format_messages(original_task)       # 每轮重建
```

关键设计：

1. **`conversation_history` 始终以结构化格式完整追加**（assistant tool_calls 消息 + tool 结果消息），各模式只是把它"投影"进请求时采用不同策略——因此结果差异可归因于上下文管理策略本身。
2. **CORRECT 模式 append-only**：`messages` 列表第一轮建好后只往后追加，前缀字节级稳定 → Prompt Cache 可以最大化复用。
3. **错误模式每轮重建**：重建本身是内容等价的（历史相同），真正的破坏来自各模式注入的"变动点"。变动点位置决定代价（正文 L538：*变动点越靠前，需要重新计算和计费的 token 越多*）。
4. **同一任务、同一工具集跑全部六模式**（`compare_implementations` 对 `KVCacheMode` 枚举逐个实例化）——同任务对照是结论有效性的前提。
5. 指标采集双路兼容：`usage.cached_tokens`（Kimi 顶层字段）→ 回退 `usage.prompt_tokens_details.cached_tokens`（OpenAI/Ollama 风格）。这条回退分支让本地化移植**零改动**拿到了缓存指标。
6. `_execute_tool` 用 `inspect.signature` 过滤模型多给的参数；sliding_window 的窗口起点会向前回退以保持 assistant↔tool 配对（否则 API 直接拒绝孤儿 tool 消息）。

## 二、六模式实现逐个解析

### 1. correct —— 稳定上下文基线

- `_get_system_prompt()`：固定 base_prompt，无任何动态内容；
- `_get_tools()`：`self.tool_definitions.copy()`，顺序固定（read_file / find / grep）；
- `_format_messages()`：`[system] + 完整 history + [user(task)]`，结构化 role-content 消息；
- execute_task 路径：只建一次列表，append-only。

缓存语义：第 2 轮起，上一轮的全部消息都是本轮前缀 → 只有新增的尾部需要 prefill。

### 2. dynamic_system —— 系统提示加时间戳

```python
if self.mode == KVCacheMode.DYNAMIC_SYSTEM:
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S.%f")
    return f"{base_prompt}\n\nCURRENT TIME: {timestamp}"
```

- 变动点：**消息 #1（system）的末尾**，每轮不同（含微秒）；
- 每轮重建消息列表 → 时间戳必然刷新；
- 缓存语义：前缀只能复用到 "CURRENT TIME:" 之前（≈ system prompt 本体），时间戳 token 起全部重算。这就是正文开篇的"时间戳故事"：时间信息应作为**末尾追加的用户消息**或工具获取。

### 3. shuffled_tools —— 打乱工具顺序

```python
if self.mode == KVCacheMode.SHUFFLED_TOOLS:
    random.shuffle(tools)   # 对 copy() 浅拷贝重排，工具集合不变
```

- 变动点：**system 块内的 tools 渲染段**（qwen3.5 模板把 tools 放在 system 最前，Kimi 同理）——变动点几乎是上下文最前面；
- 每轮 random → 顺序必然不同；
- 缓存语义：从第一个顺序不同的工具起全部失效，接近全损。正文 L548：*工具定义通常占据很大篇幅……改变顺序会让缓存从首个变动的工具起全部失效*。同时正文声称"保持固定顺序对模型选择工具的能力几乎没有影响"——两次实测中该模式任务均完成（工具功能等价），验证了后半句。

### 4. dynamic_profile —— 每轮注入变化的用户额度

```python
def _get_user_profile_message(self):
    if self.mode == KVCacheMode.DYNAMIC_PROFILE:
        self.user_credits -= 1
        return {"role": "user",
                "content": f"[User Profile: Premium user with {self.user_credits} credits remaining]"}
```

- 变动点：**消息 #2（紧跟 system 的 user 消息）**，额度每轮 -1；
- 缓存语义：只有 system 本身（~690 tok）可复用，其后全部重算；
- 这是典型生产反模式：把**每请求都变的易失状态**放在上下文最前部。正文 L546 的解法：需要时通过专门的状态管理机制处理（对应本章后文 Agent Status Bar——动态元信息**追加在末尾**）。

### 5. sliding_window —— 只保留最近 6 条历史

```python
if self.mode == KVCacheMode.SLIDING_WINDOW:
    start = max(0, len(self.conversation_history) - 6)
    while start > 0 and self.conversation_history[start].get("role") == "tool":
        start -= 1          # 回退到配对的 assistant 消息，避免孤儿 tool 消息
    messages.extend(self.conversation_history[start:])
```

- 双重破坏：
  1. **缓存**：历史超过 6 条后窗口开始滑动 → 每轮前缀**起点**都不同 → 从消息 #1 起就不匹配，缓存几乎无法跨轮复用；
  2. **行为**：早期工具结果滑出窗口 → 模型"忘记"已读过的文件 → 重新调用工具，陷入重复劳动。正文 L550：*经常陷入循环，反复执行相同的工具调用，因为它"忘记"了之前已经获得的结果*。
- 注意其缓存损失是渐进的：前几轮窗口未滑动时前缀仍稳定（本地实测 sliding_window 总 cached=16,333 不为零的原因）。

### 6. text_format —— 历史扁平化为纯文本

```python
elif self.mode == KVCacheMode.TEXT_FORMAT:
    history_text = "Previous conversation:\n"
    for msg in self.conversation_history:
        ...  # 拼成 "USER: ... / ASSISTANT: [Making tool calls] / - Calling read_file ... / TOOL RESPONSE: {...}"
    messages.append({"role": "user", "content": history_text})
```

- 结构化 role-content 消息（含 tool_calls / tool_call_id 配对、tool role）全部降级为**一条大 user 消息里的文本**；
- 每轮重新渲染，但文本是 append-only → **前缀其实稳定，缓存命中率反而可以很高**（本地实测 82.2%，六模式最高！）；
- 真正的破坏是**格式语义**：模型训练时学的是结构化对话格式，扁平文本迫使它额外消耗注意力去推断角色边界；工具结果混在 user 消息文本里，模型无法确信"结果已拿到" → 重复调用工具 / 不收敛。正文 L552 的原文精确预言了这个现象：*问题的关键不在缓存，而在于偏离了模型训练时使用的标准消息格式……重复执行已完成的操作、忽略工具调用结果*。
- 代码注释也点明：TEXT_FORMAT 仍发送 tools 定义，只改历史格式——确保差异仅来自消息格式。

## 三、结果如何验证《实验 2-3》正文

### 三方对照表

| 模式 | 正文论断 | Kimi K2.6（重任务） | 本地 qwen3.5-4b（小任务） | 验证结论 |
| --- | --- | --- | --- | --- |
| correct | 基线：稳定前缀 → 缓存最优 | cached=768, ratio 10.1%, 3 轮成功 | cached=2,872, **ratio 36.2%**, 3 轮/2 工具 | ✅ 基线成立 |
| dynamic_system | 时间戳 → 整表重建、缓存失效 | cached=768（仅前缀前段），ttftN 19.8s vs 13.9s | ratio 36.2%→**9.4%**（仅剩时间戳前的 ~755 tok） | ✅ 缓存塌方可见 |
| shuffled_tools | 从首个变动工具起全部失效；不影响工具选择能力 | cached **256** vs 768，ttft1 7.82s（冷 prefill） | ratio **4.8%**、Hit% 25%，4 轮仍完成任务 | ✅ 缓存近全损；功能等价成立 |
| dynamic_profile | 无关动态 → 失效，生产反模式 | cached=768，ratio 10.0% | cached=690（= system 本体），Hit% 50% | ✅ 只剩 system 可复用 |
| sliding_window | 破坏前缀一致性 + 丢失工具结果 → 反复重做 | **success=False**（5 轮异常退出，ledger 记为 failure） | 8 轮/7 工具（vs correct 3/2），prompt 总量 59,531 vs 7,933（**7.5 倍**） | ✅✅ 行为退化是最强信号，两次复现 |
| text_format | 最具破坏性；问题在格式偏离而非缓存 | 成功但 ttftN **28.9s** vs 13.9s（≈2 倍慢），cached=674 | **任务失败**：50 轮触顶、53 次工具调用不收敛，cache ratio 却高达 **82.2%** | ✅✅ "不在缓存而在格式"被本地数据精确验证 |

### 三个值得强调的验证细节

1. **text_format 是正文论断最漂亮的证据**：本地实测它的缓存命中率六模式最高（82.2%——因为扁平文本 append-only，前缀稳定），任务却彻底失败。如果"文本格式的问题是缓存"，这一格应该更好而非更差。数据直接支持正文 L552 的因果判断：破坏发生在**格式语义层**，不在缓存层。
2. **变动点位置决定缓存残量**，与正文 L538 的原理严格对应：
   - 变动点在最前（shuffled_tools：tools 在 system 块头部）→ ratio 4.8%，近全损；
   - 变动点在中前部（dynamic_profile：消息 #2）→ 只剩 system 的 ~690 tok；
   - 变动点在 system 末尾（dynamic_system：时间戳在 base_prompt 之后）→ 保留 ~755 tok 的前段；
   - 无变动（correct）→ 36.2% 且随轮次增长。
3. **sliding_window / text_format 的代价主要不在缓存数字上，而在行为上**——这正是正文把这两个模式单列为"更严重问题"的原因。Kimi 上 sliding_window 直接失败、text_format 慢一倍；本地小模型上 text_format 完全不收敛。小模型对格式破坏**更敏感**，两套数据的差异方向一致。

### 测量口径的诚实声明（与 EXPERIMENT_LEDGER 的 "reported rather than forced" 政策一致）

- **Kimi 侧**：`cached_tokens` 按 768-token 块粒度上报（correct / dynamic_system / dynamic_profile 三者同为 768，只能区分"有/无/少"）；且 k2.6 是推理模型、非流式调用，`ttft_per_iteration` 实为**完整请求延迟**，decode+思考占主导，缓存效应在 TTFT 列被稀释（correct 末轮 13.9s vs dynamic_system 19.8s 的差只能看方向）。
- **本地侧**：`cached_tokens` 是逐 token 精确值（探针实测 460/287 等），cache ratio 排序清晰；但 Ollama 非流式 → TTFT 同样是完整延迟，且本地 GPU decode 占比高，六模式 1st TTFT（1.2~2.9s）无区分度。**本地复现的有效信号是 cache ratio 与迭代/工具调用数，不是 TTFT。**
- 结论：两次实验（API / 本地）各自验证了正文论断的不同侧面——Kimi 验证了 ttft 与成本方向，本地验证了 cache ratio 精细排序与行为退化。两者都支持"错误模式的代价 = 缓存损失 + 行为退化，后者往往更致命"。

## 四、正文没明说、代码里值得学的工程细节

1. **孤儿 tool 消息防护**（sliding_window 的 `while start > 0 and role == "tool"` 回退）：窗口裁剪必须保持 assistant↔tool 配对，否则 OpenAI 兼容端点直接 400。做上下文截断时的通用纪律。
2. **参数消毒**：`_execute_tool` 用 `inspect.signature` 只传工具声明过的参数——小模型经常多给参数，过滤比报错更适合学习实验。
3. **错误即观察结果**：工具执行失败以 `{"error": ..., "success": False}` 结构回传给模型而不是抛异常中断循环，Agent 可以自我纠正。
4. **指标双路兼容**：`cached_tokens` 顶层字段与 `prompt_tokens_details.cached_tokens` 都尝试——这是对接不同 provider 时的通用模式。
5. **成本示意与实测分离**：`--cache-price-ratio`（默认 0.1）只作用于实测 token 数做"应付多少"的换算演示，不虚构任何厂商报价——数据与假设严格分离。
