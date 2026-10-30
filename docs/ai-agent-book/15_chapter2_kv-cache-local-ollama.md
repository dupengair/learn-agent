# chapter2/kv-cache 本地 Ollama 改造记录

> 日期：2026-10-30
> 实验：`ai-agent-book/chapter2/kv-cache/`（实验 2-3：六种上下文构造策略对 KV Cache 命中 / TTFT / 成本的影响）
> 运行环境：Ollama 0.35.0 @ `localhost:8000`，模型 `qwen3.5-4b:q4`（Q4_K_M，4.2B），GPU 6GB

## 一、改造目标

沿用 `chapter1/context` 的本地化模式，把本实验从「Moonshot/Kimi 官方 API」改为「本地 Ollama 为默认 provider」，`cp env.example .env` 后零参数即可运行；Kimi / OpenRouter 路径保持向后兼容。

改造前可行性实测（决定方案的三条关键结论）：

| 验证点 | 结果 | 结论 |
| --- | --- | --- |
| Ollama `/v1` 是否报 `cached_tokens` | ✅ 相同前缀第二次请求 `prompt_tokens_details.cached_tokens` 0→460 | `agent.py` 原有的 `prompt_tokens_details` 回退分支可直接工作，**缓存统计无需改** |
| ReAct 多轮回传 `reasoning` 字段是否破坏前缀缓存 | ✅ 回传带 `reasoning` 的 assistant 消息 + tool 结果后，下轮命中 294/298 | 无需剥离 reasoning 字段 |
| qwen3.5 thinking 行为 | 默认开启，耗时 0.72s→2.27s（同一请求）；`reasoning_effort="none"` 可按请求关闭 | 默认关闭 thinking，加 `--think` 开关保留对比能力 |

## 二、代码改动（3 个文件）

### `agent.py`

1. `KVCacheAgent.__init__` / `compare_implementations` 新增 `provider=None, think=False` 形参：
   - **显式传入 provider 时直接使用**；未传时保持旧行为（按 key 前缀推断：`sk-or-` 开头 → openrouter，否则 → kimi）——旧 Kimi 用法零变化；
   - provider 解析统一走 `agentbook.providers.resolve_backend`（ollama 在注册表中 `requires_key=False`，`OLLAMA_BASE_URL` 可覆盖默认的 `localhost:11434`）；
   - agentbook 导入加了 try/except 兜底（直接 `python main.py` 运行时把仓库根插回 `sys.path`，与 chapter1/context 同一模式）。
2. 思考控制：`provider == "ollama" and not think` 时请求附加 `reasoning_effort="none"`（Ollama 0.35 的 `/v1` 端点支持，实测有效）。联网 provider 不发送该参数。
3. 新增 `_is_local_thinking_model()`：本地 qwen3* 的思考 token 计入 completion 预算，开 `--think` 时通过 `_reasoning_safe_max_tokens` 把 max_tokens 上限抬到 4096（温度不动——本地端点不限 temperature=1）。
4. **工具结果序列化改 `json.dumps(result, ensure_ascii=False)`**（关键修复，见下文「踩坑」）。

### `main.py`

1. `--provider` 参数，`default="ollama"`，choices 来自 `agentbook.providers.SUPPORTED_PROVIDERS`；
2. `--model` 默认值运行时解析：`provider=ollama` → 读 `OLLAMA_MODEL` env（缺省 `qwen3.5-4b:q4`）；其余 provider 保持 `kimi-k2.6`；
3. api_key 检查豁免 ollama（无 key 走占位值）；kimi/openrouter 缺 key 报错逻辑不变；
4. `--think` 开关（默认关）；
5. `run_single_mode` / `run_comparison` 透传 provider/think，输出文件名自动带 provider+model 标记（如 `result_correct_ollama_qwen3_5_4b_q4_<ts>.json`，与归档的 `*_kimi_k2_6.json` 区分）；
6. 顺带把 main() 尾部 5 处重复的调用分支收敛为 `common` 参数字典（消重，逻辑不变）。

### `env.example`

顶部加 Ollama 段（`OLLAMA_BASE_URL=http://localhost:8000/v1` / `OLLAMA_API_KEY=ollama` / `OLLAMA_MODEL=qwen3.5-4b:q4`），Moonshot/OpenRouter 段保留为联网备选。

## 三、踩坑记录：Ollama 16k 上下文截断 → qwen3.5 模板 500

### 现象

首跑（correct 模式，小任务）前两轮正常且缓存命中（`cached=783`），第三轮起 Ollama 返回 500：

```
Jinja Exception: No user query found in messages.
```

### 排查过程（逐层排除）

1. 手工构造两轮工具往返的各种消息变体（大结果 / 无 reasoning / 剥离 model_dump 额外字段 / 不同 tool_call id）→ 全部成功，排除消息结构问题；
2. 用 httpx event hook 抓下 agent 真实请求体，逐字节重放 → 稳定复现；
3. 二分定位：**缩小工具结果内容立即通过**，剥离 assistant 额外字段无效 → 内容大小是触发条件；
4. 判别实验：同一请求末尾追加一条 user 消息 → **通过**（prompt=15928 tokens），`/v1` 端点传 `options.num_ctx` → 无效（被忽略）。

### 根因

`/api/ps` 显示 `context_length: 16384`。Ollama 在应用聊天模板**之前**按上下文预算从旧到新丢弃消息：23.7KB 的工具结果（`json.dumps` 默认 `ensure_ascii=True` 把中文变 `\uXXXX`，6 倍膨胀）把开头的 system + user 任务消息挤出预算；qwen3.5 模板随后反向遍历找「最后一条不像 `<tool_response>` 的 user 消息」，找不到 → `raise_exception('No user query found in messages.')`。

### 修复

改 `agent.py` 工具消息序列化为 `ensure_ascii=False`（中文不再膨胀，同场景 prompt 从 ~16k tokens 降到 ~7k）。没有选择在 6GB 显存上把服务端 ctx 抬到 32k（KV cache 需再占 ~0.5-1GB，余量不足）。若以后要跑更重的任务（如默认的「总结 chapter1+chapter2 全部项目」），两个选项：

- 重启 Ollama 时设 `OLLAMA_CONTEXT_LENGTH=32768`（全局）；
- 或 `OLLAMA_HOST=localhost:8000 ollama create` 一个带 `PARAMETER num_ctx 32768` 的模型变体（按模型生效，需注意显存）。

## 四、本地实测结果（2026-10-30）

### 单模式验证（correct，小任务：分析 chapter2/kv-cache 目录）

- 3 轮迭代 / 2 次工具调用（find + read_file），成功产出总结；
- 缓存命中 3/3（100%），cache ratio 79.7%（2857/3586 tokens）；
- 最终答案质量对 4B 模型而言合格（能概括实验目的，略泛化）。

### 六模式对比（--compare，同一小任务）

结果文件：`comparison_ollama_qwen3_5_4b_q4_20261030_152137.json`（约 2.5 分钟跑完）

| Mode | Iters | Tools | Cache% | Hit% | 结论 |
| --- | --- | --- | --- | --- | --- |
| correct | 3 | 2 | **36.2%** | 100% | 基线：3 轮完成，缓存收益最大 |
| dynamic_system | 3 | 2 | 9.4% | 100% | 时间戳打碎前缀，cache ratio 掉到 1/4 ✅ 符合预期 |
| shuffled_tools | 4 | 3 | **4.8%** | 25% | 工具顺序扰动后仅剩 system 段前缀命中（~690 tok）✅ 符合预期 |
| dynamic_profile | 2 | 2 | 27.4% | 50% | 前缀早段变化，命中次数减半 |
| sliding_window | **8** | **7** | 27.4% | 100% | 行为退化：窗口丢掉早期工具结果，模型反复重读文件（vs correct 3 轮 2 次）✅ 符合「滑窗伤行为」论断 |
| text_format | 50(触顶) | 53 | 82.2% | 100% | **任务失败**：扁平化历史让 4B 模型陷入无限工具调用循环，50 轮未给出最终答案 |

**方向性结论（与教材 2-3 一致）**：cache ratio 排序 correct > dynamic_profile > dynamic_system > shuffled_tools，前缀稳定性决定缓存收益；sliding_window 与 text_format 的代价主要表现为**行为退化**（迭代数暴涨 / 任务失败），而非单纯的缓存损失。

**测量口径注意（本地 Ollama 特有）**：agent 的 `ttft_per_iteration` 在非流式调用下测的是**完整请求延迟**（prefill+decode+思考），不像 Kimi API 那样近似 prefill 时间，且本地 GPU 上 decode 占比高、噪声大——六模式 1st TTFT（1.2~2.9s）差异不明显，**本地复现的主要有效信号是 cache ratio 与迭代数，TTFT 列仅作参考**。这与 API 上的现象形态不同，属于服务机制的差异而非矛盾。

**text_format 的高缓存（82.2%）说明**：扁平化后历史成为一条持续追加的 user 消息，前缀其实稳定（缓存反而高），真正的破坏是「结构化 tool 消息语义丢失」导致小模型无法收敛——即该模式的问题不止缓存，更是格式本身。

## 五、回归

- `chapter2/kv-cache/tests/`：7 passed；
- `tests/test_ch2_prompt_injection_robustness.py`（仓库级相关）：18 passed；
- ruff：改动未引入新问题（两文件改造前后均为上游遗留的 75 条，类型未变）。
