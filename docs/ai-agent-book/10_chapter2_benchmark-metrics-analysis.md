# Chapter 2 · benchmark.py 指标统计原理分析

- 日期：2026-10-20
- 分析对象：`ai-agent-book/chapter2/local_llm_serving/benchmark.py`（实验 2-1 配套服务基准）
- 运行环境：Ollama `qwen3.5-4b:q4` @ `:8000/v1`（OpenAI 兼容端点）

## 一句话结论

**benchmark.py 是"服务端给 token 数、客户端自己掐表"的混合测量**：token 数量来自服务端在流末尾返回的 `usage.completion_tokens`（精确值），所有时间量（TTFT、总时长、墙钟）由客户端 `time.perf_counter()` 自测。**脚本从不调用任何"缓存命中率"或"批处理内部状态"类 API**——KV Cache 的效果、批处理的收益，都是通过精心设计的对照组请求 + TTFT/墙钟差异**间接推断**出来的。

## 一、测量基元：`stream_once()`（L86-147）

三个场景全部复用这一个函数，它一次流式请求返回 4 个数。逐个拆解：

### 1. TTFT —— 纯客户端墙钟（服务端不给）

```python
start = time.perf_counter()                      # L101 发请求前起表
...
for chunk in stream:
    ...
    if text:                                     # L130 第一个带文本的分片
        if ttft is None:
            ttft = time.perf_counter() - start   # L131-132 捏表
```

- 从发起 HTTP 请求到**第一个携带文本的流式分片**到达的墙钟时间，涵盖排队 + prefill + 网络。
- "文本"的判定（L125-129）取 `delta.content` **或** `reasoning_content` **或** `reasoning`——对 qwen3.5 这类思考模型，**思考分片也算首 token**，所以测到的 TTFT 实际是"首个思考分片"的延迟。
- 若整个响应没有任何文本分片，兜底 `ttft = total`（L136-137）。

### 2. output_tokens —— 服务端 usage 优先，分片计数兜底

```python
stream_options={"include_usage": True},          # L112 要求流末尾附带 usage
...
if getattr(chunk, "usage", None) is not None:    # L117 末尾的 usage 专用分片
    usage_tokens = chunk.usage.completion_tokens # L119 服务端 tokenizer 数的精确值
...
output_tokens = usage_tokens if usage_tokens is not None else chunk_count  # L138
```

- **精确路径**：`include_usage` 让 OpenAI 兼容端点在流的最后一个分片附带 `usage`（该分片没有 `choices`，所以 L122-123 直接跳过）。`completion_tokens` 是服务端 tokenizer 统计的**真实输出 token 数**——这是全脚本唯一的"模型内部数据"，但注意它是 token **计数**，不是性能数据。
- **兜底路径**：`chunk_count` 只是"有文本的分片数"（L133 每片 +1）。Ollama 流式恰好 1 分片 ≈ 1 token，但 vLLM 不保证分片粒度，所以兜底值只是近似。

### 3. decode_tps —— 客户端计算

```python
decode_time = max(total - ttft, 1e-6)            # L139 扣掉首 token 前的时间
decode_tps = output_tokens / decode_time         # L140
```

逻辑：请求总时长中，TTFT 之前是 prefill（+排队+网络），之后视为纯解码阶段，因此 **解码吞吐 = 输出 token ÷（总时长 − TTFT）**。这是客户端侧近似——TTFT 之后的时间并未剔除网络尾延迟，但本机回环下误差很小。

## 二、三个场景的实验设计

### throughput（L153-177）：重复 + 求均值

固定一条要求长输出的 prompt（讲 KV Cache 原理），连续跑 `--repeats`（默认 5）次 `stream_once`，对 `ttft` / `decode_tps` / `output_tokens` 各求算术平均（`statistics.fmean`，L173-175）。无预热——第一次请求含冷启动成分。

### kv-cache（L180-224）：对照组实验，**间接推断**缓存行为

这是设计最巧妙的场景。关键在制造"唯一差异"：

```python
base_prompt = build_padded_system_prompt(args.prefix_tokens)  # L189 约1024 tok 的系统提示词
stream_once(client, model, warm_msgs, ...)        # L192-194 预热：写入缓存，不计入统计
...
hit = stream_once(client, model, warm_msgs, ...)  # L199 命中组：前缀逐字节相同
...
mutated = f"[req-{i}-{time.time_ns()}] " + base_prompt  # L203 只在开头插唯一串
miss = stream_once(client, model, miss_msgs, ...)  # L205 未命中组
...
speedup = miss_mean / hit_mean                    # L221
```

原理链条：

1. `build_padded_system_prompt()`（L60-73）用重复句子把前缀撑到约 1024 token（按"4 字符 ≈ 1 token"粗估）——前缀越长 prefill 越贵，缓存效果越可测。
2. 服务端前缀缓存（vLLM 的 automatic prefix caching / Ollama 的 prompt cache）按 KV 块做**前缀匹配**：命中组前缀逐字节不变 → prefill 几乎全部跳过；未命中组在**开头**插入 `req-{i}-{纳秒时间戳}`，第一个 KV 块就匹配失败 → **整个前缀缓存失效**，必须全量重算。这正是书中"系统提示词一旦定下来就不要改开头"的实测版。
3. 两组的输出指令相同（输出长度基本一致）、解码成本相同，因此 TTFT 差异**只能**来自 prefill 阶段 → `speedup` 就是缓存收益。

**强调**：脚本没有读任何缓存命中计数（Ollama/vLLM 的 OpenAI 兼容端点不暴露 prompt-cache hit 指标；vLLM 虽有 Prometheus `/metrics` 可查 `cached_token` 统计，但脚本未使用），缓存是否命中是从 TTFT 差异反推的——这也是为什么文档说"所有数字来自真实实测"但"缓存命中率"从来不是直接读数。

### batching（L227-269）：客户端并发压测 + 聚合吞吐

```python
with ThreadPoolExecutor(max_workers=level) as pool:      # L243 level 个线程同时发请求
    futures = [pool.submit(stream_once, ...) for _ in range(level)]
    results = [f.result() for f in futures]
wall = time.perf_counter() - start                       # L251 整批墙钟
total_tokens = sum(r["output_tokens"] for r in results)  # L252 各请求 token 求和（仍是服务端 usage）
agg_tps = total_tokens / wall                            # L253 聚合吞吐
per_req_tps = agg_tps / level                            # L254 单请求摊均
```

对每个并发度（默认 1,2,4,8）记录：`wall_s`（整批墙钟）、`aggregate_tps`（这批全部 token ÷ 整批墙钟）、`per_request_tps`、平均 TTFT。

**continuous batching 原理**（这是该场景要验证的服务端机制）：

- 无批处理的服务器一次只解一个请求的 token，其他请求排队——8 个并发 ≈ 8 倍墙钟，聚合吞吐 ≈ 单流吞吐（常数）。
- 有 continuous batching 的 serving（vLLM 的核心特性）把同时在场的请求拼进同一个解码批：GPU **一次前向传播为批内每个请求各解一个 token**。解码是显存带宽受限的（每步权重读一遍，算力大量闲置），批内多请求几乎不增加每步耗时 → 聚合吞吐随并发显著上升。
- 权衡被 L253-254 + L262 直接量化：并发↑ → `aggregate_tps`↑（GPU 吃满），但排队使 `ttft_mean`↑；若聚合吞吐不能随并发线性增长，`per_request_tps = agg/level` 就下降。
- 对 Ollama 的一个解读要点：Ollama 的并行度由 `OLLAMA_NUM_PARALLEL`（默认自动，通常 4 或 1）决定。若并发请求超过服务端 slot 数，超出部分在服务端排队——实测 `aggregate_tps` 曲线在哪一级并发"走平"，就是对服务端真实并行容量的测量。

## 三、指标来源速查表

| 指标 | 来源 | 代码位置 | 性质 |
|---|---|---|---|
| TTFT | 客户端 `perf_counter` 掐表 | L101, L131-132 | 自测，含排队+prefill+网络 |
| output_tokens | 服务端 `usage.completion_tokens` | L112, L117-119 | **唯一来自服务端的数据**（计数） |
| output_tokens 兜底 | 客户端分片计数 | L133, L138 | 近似（vLLM 分片粒度不定） |
| decode_tps | 客户端计算 token/(总时长−TTFT) | L139-140 | 自测近似 |
| kv-cache speedup | 对照组 TTFT 均值相除 | L203, L213-221 | **间接推断**，未读缓存计数器 |
| aggregate_tps | 批 token 求和 ÷ 批墙钟 | L251-253 | 自测；token 仍来自服务端 usage |

## 四、解读时的注意点（测量局限）

1. **TTFT 含思考分片**：qwen3.5-4b 是思考模型，`reasoning_content` 分片被当作首 token（L127），测到的是首个思考分片延迟，通常比"首个可见答案"更早。
2. **无预热 vs 有预热**：只有 kv-cache 场景做了显式预热（L192-194）；throughput 的第一次请求可能被冷启动（模型加载/首次编译）污染。
3. **采样随机性**：默认 `temperature=0.7`，每次输出长度有波动，`decode_tps` 均值含长度方差；kv-cache 场景两组同指令对冲了这一点。
4. **Ollama 的缓存受 slot 管理**：Ollama 按 slot 缓存 KV，`OLLAMA_NUM_PARALLEL`、`OLLAMA_KEEP_ALIVE` 都会影响命中组是否真的命中；若 Ollama 在两次请求间驱逐了模型，hit 组也会退化成 miss。
5. **batching 是客户端并发视角**：`ThreadPoolExecutor` 模拟负载，`aggregate_tps` 把排队时间也算进分母（墙钟），它测的是"这套服务端在 N 路并发下的端到端聚合能力"，不等价于服务端内部批大小的直接读数。
6. **兜底 token 数不可比**：若 `include_usage` 被某服务端忽略，输出 token 退化为分片数，此时 decode_tps 与 vLLM 环境下不可直接对比（Ollama 1 分片≈1 token，vLLM 不一定）。
