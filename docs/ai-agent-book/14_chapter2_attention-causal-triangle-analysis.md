# attention_cli.py 因果三角热力图：三个颜色现象的定量分析与源码导读

> 对应实验：`ai-agent-book/chapter2/attention_visualization`（实验 2-2 注意力机制可视化）
> 复现命令：`python attention_cli.py --output results/smol_verification_heatmap.png`
> 模型：本地 `Fine-tuning/model/Qwen3-0.6B`（`.env` 的 `MODEL_NAME` 注入，eager attention，进程内 transformers 前向）
> 图参数：Layer -1（最后一层）、全部 16 个 head 平均、chat template 包装后的 29 个 token
> 本文档所有数值来自同链路独立复现（`seq_len=29, layers=28, heads=16`），sink 均值 0.761 与图中标注 76.1% 一致。

## 0. 矩阵数值体检表（先看数据，再看颜色）

| 区域 | 含义 | 最后一层 (head 平均) | Layer 0 (对照) |
| --- | --- | --- | --- |
| 上三角 (j>i) | 因果掩码禁止关注的位置 | **严格 = 0**（实测 max 0.00e+00） | 同左 |
| 第 0 列 `<|im_start|>` | attention sink 列 | mean **0.761**，前 5 行 [1.0, 0.907, 0.831, 0.875, 0.729]，后 10 行 0.57~0.84 | mean 仅 0.066 |
| 对角线 (i=j) | token 对自己的注意力 | mean **0.179**，波动 0.088~0.234（锯齿来源） | mean 0.510 |
| 其余可见格 (i>2, 0<j<i) | 对其它历史 token | mean **0.007**，max 0.247 | mean 0.0346 |
| 列平均权重 Top2 | — | `<|im_start|>` 22.06 vs `system` 0.153（**差两个数量级**） | `<|im_start|>` 1.90 vs `\n` 0.72 |

逐 head（最后一层）：16 个 head 的 sink 占比全部 >0.5（0.52~0.93），其中 head 9 = 0.932、head 14 = 0.521（self=0.473，是个"局部头"）。
**结论：sink 不是单头怪癖，是该层几乎全体的行为；而"深蓝的其他列"不是没有注意力，是被 sink + 对角线把行预算（每行和恒为 1）挤到了 0.007 的量级。**

## 1. 现象一：为什么第一列是绿色高亮 —— attention sink

**纠正一个表述**：不是"im_start 乘以所有 token 输出热度高"。注意力格子的值不是两个 token（向量）直接相乘的结果，而是
`A[i,j] = softmax(q_i · k_j / √d)_j` —— 第 i 行（query）的查询向量与第 j 列（key）的键向量的**点积打分，再对该行所有可见位置做 softmax 归一化**后的概率。第一列绿，是因为**所有 29 行的 query 都把大比例概率质量分给了第 0 个 key**（`<|im_start|>`），实测平均 76.1%。

**为什么模型要这么做（机制）：**

1. **softmax 没有"弃权"选项**。每一行必须把概率和凑成 1。当某个 head 在深层"觉得没有任何历史 token 值得看"时，它不能输出全零，必须把预算倒在一个"安全"的位置。
2. **`<|im_start|>` 是完美的倾倒场**。它是唯一出现在每一行因果窗口内的 token；语义上它是格式控制符，不携带内容信息，把注意力堆在这里不会干扰语义检索。模型在训练中自发学会了这个约定。
3. **列内颜色梯度（顶部黄 → 底部绿青）的原因**：行 0 只能看自己，softmax 单元素 = 1.0（全图最亮格，也是标题 max 100% 的来源）；行 1~4 序列还短、可选 key 少，sink 占比 0.73~0.91；后面的行可选 key 增多，sink 被稀释到 0.57~0.84 —— 在 viridis 色标（0→深紫，1→黄）上正好呈现"黄→绿→青"的渐变。

**这说明了什么：** 这就是第 2 章 KV Cache 部分讲的 **attention sink 现象**（StreamingLLM 论文， Xiao et al. 2023）。工程含义：滑动窗口截断最旧 token 会把 sink 一起丢掉，模型输出立刻崩溃；所以 StreamingLLM 做"保留前 4 个 token + 滑动窗口"，KV Cache 压缩（如 H2O）也不能简单按"分数小就丢"处理最前面的列。

**顺带回答"为什么第二列 system 开始就是深蓝"**：system 列的可见行平均是 0.153，已是全图第二亮的列，但 viridis 把它映射到深蓝——因为色标最大值是 1.0，0.15 在色带上位于 15% 位置（深蓝区）。**第一列不是"亮一点"，是亮了两个数量级。**

## 2. 现象二：为什么是三角形 —— 因果掩码，且这张图既不是 prefill 也不是 decode 的"过程记录"

**三角形的来源是因果掩码（causal mask），不是生成方式。** 自注意力打分后、softmax 前，模型把所有 j>i 的分数设为 -inf，softmax 后这些位置严格为 0（实测上三角 max = 0.00e+00）。`visualization.py:510` 又把上三角显式 mask 成 `#f0f0f0` 灰白色块，所以你看到的空白三角是"渲染层的美化"，底下本来就是 0。

**prefill/decode 的概念澄清（这是个常见误区）：**

- 你的命令没传 `--max-new-tokens`，默认 0 → **没有生成任何新 token**。`full_ids` 就是 chat template 后的 29 个 prompt token，代码只做了一次 `model(input_ids=full_ids, output_attentions=True)` 的并行前向（`attention_cli.py:227-233`）。
- prefill/decode 是**推理引擎的调度概念**（prefill = 一次并行算出全部 KV；decode = 每步用 cache 增量算 1 个新 token 的 KV 和注意力行）。而注意力矩阵是**静态数学对象**：不管一次并行算还是逐 token 增量算，第 i 行的数值完全相同（KV Cache 就是保证这种等价性的机制）。
- 所以正确的读法是：**这张图 = 一次性并行前向的注意力快照（计算方式上更像 prefill），它的第 i 行恰好就是 decode 走到位置 i 时会得到的那一行**。29 行一次性全画出来，而不是逐步生成的记录。想看"模型自己生成的 token 对 prompt 的注意力"，加 `--max-new-tokens N`，代码会先生成、再对 prompt+生成做一次独立前向捕获（`attention_cli.py:204-218`），此时图上会多一条红色 Prompt/Generated 分界线（`visualization.py:531-536`）。

**行/列语义**：行 = Query（谁在看），列 = Key（被谁看）。每个 token 只能看自己和之前的 token → 每行非零格数 = 行号+1 → 下三角。

## 3. 现象三：为什么对角线（右侧斜坡）比左边亮 —— 自注意力的"自我+局部"偏好

**方向判断正确，机制表述需修正。** 对角线格子确实是 `q_i · k_i`（query_i 与自己对应的 key_i 的点积），它通常是该行得分较高的项之一（自身向量经 W_q/W_k 投影后仍最"对齐"）；再经 softmax 后落到 0.09~0.23 区间。实测：

- 最后一层：对角线 mean **0.179** vs 其余可见格 mean **0.007** —— **约 25 倍**。所以斜坡在色带上是浅蓝，左侧大片是深紫。
- 但"自己乘自己一定最高"**不绝对**：某些行里上一个 token 或语义相关 token 的得分会超过自身（max 的其它格 = 0.247 > 部分对角线值）；且这是 head 平均后的结果，单个 head 差异很大（head 14 self=0.473 是局部头，head 9 self=0.047 几乎全给 sink）。
- **锯齿的来源**：不同 token 的自注意力强度天然波动（"样"0.234 vs "气"0.088 量级），标点、格式符、实词各不相同，逐格颜色抖动就成了锯齿带。
- **深层 vs 浅层反转**（对照实验实测）：Layer 0 对角线 mean 0.510、sink 仅 0.066 —— 浅层忙着"读自己与邻居"（局部定位），最后一层把行预算大量倒给 sink（0.761）。用 `--compare-layers 0 -1` 可以一眼看到这个反转。

**这说明了什么**：因果 LM 预测下一个 token 时，**当前 token 和邻近 token 是最强信号**（bigram/局部性），远处 token 只在语义必要时才拿到显著权重——而"多余"的预算统一倾倒进 sink。同时它揭示了"最后一层 ≠ 语义检索层"：语义检索（如"天气"去查"北京"）更多发生在中层，最后一层的平均模式反而是 sink+对角线这种"结构性"模式。

## 4. 源码导读：从命令行到 PNG 的数据流水线

阅读顺序（也是数据流向）：

```
build_parser (attention_cli.py:50)
  └─ run (attention_cli.py:180)
       ├─ ① AttentionVisualizationAgent(...)          agent.py:160-208   ← eager 加载
       ├─ ② build_input_ids                           attention_cli.py:137-150  ← 决定横轴 token
       ├─ ③ [可选 generate]                           attention_cli.py:204-218
       ├─ ④ 单次 forward output_attentions=True       attention_cli.py:227-233  ← 矩阵诞生地
       ├─ ⑤ extract_layer_matrix                      attention_cli.py:153-177  ← 选层/选头
       └─ ⑥ create_layer_attention_heatmap            visualization.py:472-556 ← 三角形与配色
             └─ attention_sink_stats                  visualization.py:439-469  ← 标题 76.1%
```

### ① eager 加载 —— 一切的前提（agent.py:194-199）

```python
self.model = AutoModelForCausalLM.from_pretrained(
    model_name, ...,
    attn_implementation="eager"  # Enable attention output
)
```
HF transformers 只有 **eager** 实现会物化完整注意力矩阵；SDPA/FlashAttention 为省显存不返回 attn weights（`output_attentions=True` 也拿不到）。这也是 `.env` 注释强调"必须 transformers 进程内加载、vLLM/Ollama 无法服务化本实验"的原因。**没有这一行，④ 会返回空元组直接报错退出（attention_cli.py:234-237）。**

### ② chat template —— 决定"第一格为什么是 <|im_start|>"（attention_cli.py:137-150）

`apply_chat_template(..., add_generation_prompt=True)` 生成：
`<|im_start|>system\nYou are a helpful AI assistant.<|im_end|>\n<|im_start|>user\n北京 的 天气 怎么样<|im_end|>\n<|im_start|>assistant\n`
共 29 token。所以第一列/第一行是 `<|im_start|>`——**sink 的位置由 template 的第一个 token 决定，不是模型偏好"绿色"**。加 `--no-chat-template` 后第一个 token 变成 `北京`；实测（见 5.2）sink 反而**更强**（83.1% vs 76.1%）——sink 绑定的是"位置 0"，不是 `<|im_start|>` 这个特殊 token 本身。

### ④ 单次前向 —— Attention 计算结果的体现（attention_cli.py:227-233）

```python
outputs = agent.model(input_ids=full_ids, output_attentions=True, return_dict=True)
attentions = outputs.attentions   # tuple(28)，每个 [batch=1, heads=16, 29, 29]
```
`attentions[l][0, h, i, j]` = 第 l 层、head h、**query i 分给 key j 的 softmax 后权重**。这就是"Attention 计算结果"的原始形态：每层每头一张 29×29、每行和为 1 的下三角概率矩阵。真正的 `softmax(QK^T/√d + causal_mask)` 发生在 Qwen3 模型代码内部（`modeling_qwen3.py` 的 eager_attention_forward），本仓库只消费其输出。

### ⑤ 选层选头 —— 决定你看到"谁的模式"（attention_cli.py:153-177）

默认 `layer=-1`（最后一层）、`head=-1` → `layer_attn.mean(dim=0)` 对 16 头取平均。**head 平均会放大"公共模式"（sink）**：如果某层 10 个头有 sink、6 个头局部，平均后 sink 和对角线同时浮现。想看真实单头行为用 `--head 9`（强 sink）或 `--head 14`（强局部）。

### ⑥ 绘图 —— 直接塑造图形形状的三行（visualization.py:472-556）

```python
masked = np.ma.array(matrix, mask=np.triu(np.ones_like(matrix, dtype=bool), k=1))  # L510
cmap_obj.set_bad(color="#f0f0f0")                                                   # L514
im = ax.imshow(masked, cmap=cmap_obj, aspect="auto")                                # L515
```
- L510：`np.triu(k=1)` 生成上三角布尔掩码 → 被 mask 的格子**不参与配色**，画成灰白 → **右侧大空白三角是代码画出来的，不是数值小**（虽然数值也确实是 0）。
- L515：`imshow` 约定**行=纵轴(Query)、列=横轴(Key)**，原点在左上 → 下三角数据落在左下。
- viridis 色标 + `fraction=0.046` 的 colorbar（L538）把 [0,1] 映射为 深紫→蓝→绿→黄。
- L531-536：`context_boundary` 画 Prompt/Generated 红线（本命令未触发）。
- `attention_sink_stats`（L439-469）：对每个可见行 i 取 `m[i,0] / m[i,:i+1].sum()`，得标题的 mean 76.1% / max 100%（max=行 0 的 1.0）。注意分母只累加该行可见前缀——因为每行和本来就是 1，这一步是在"行内归一化"口径下量化 sink。

### 与三角形/颜色相关的"模型侧"代码（本仓库之外，但值得知道）

- **因果掩码**：`transformers` 的 `modeling_qwen3.py` 中 `create_causal_mask` 生成 4D 掩码，eager 路径把禁止位置加 -inf → softmax 后为 0 → 三角形的物理来源。
- **softmax**：`torch.softmax(dim=-1)` 行归一化 → 每行和为 1 → sink 能"挤占"其他列 → 深蓝大片。
- **head 数量**：Qwen3-0.6B 共 28 层 × 16 头（Qwen3-0.6B 是 16 heads/8 kv heads 的 GQA 结构；eager 路径返回的 attn weights 已是 16 个 query 头各自对 kv 的平均）。

## 5. 后续动手实验：命令、实测结果与分析（2026-10-28 执行）

```bash
conda activate ai-gpu
cd ai-agent-book/chapter2/attention_visualization

python attention_cli.py --compare-layers 0 -1 -o results/cmp_layer0_last.png
python attention_cli.py --no-chat-template -o results/raw_prompt.png
python attention_cli.py --max-new-tokens 30 -o results/with_gen.png
python attention_cli.py --head 9  -o results/head9.png
python attention_cli.py --head 14 -o results/head14.png
```

### 5.1 浅层 vs 深层（cmp_layer0_last.png）：对角线反转不只是"零和挤压"，更是功能分工

实测观察：layer 0 无第一列高热、对角线亮、sink mean 6.5%；layer -1 第一列高热、对角线偏冷、sink mean 76.1%。
（注：子图标题 "Layer -1" 是负索引 = 第 27 层（最后一层），不是 layer 1。）

"没有 sink 高热，所以对角线占比变大"——方向正确，softmax 行和恒为 1 的零和挤压确实是**直接机制**。但定量数据表明**不只有转移，两层本来分工就不同**：

| 指标（head 平均） | Layer 0 | Layer -1 | 变化 |
| --- | --- | --- | --- |
| sink 列 | 0.066 | 0.761 | +0.695 |
| 对角线 | 0.510 | 0.179 | −0.331 |
| 其余可见格 mean | 0.0346 | 0.0070 | **也降了 ~5 倍** |

如果只是"sink 抢走预算"，其余格占比应该相对上升；实测其余格也同步萎缩——说明**浅层的真实形态是"近处分散"**（自己 0.51 + 邻居若干，做局部/逐位的特征聚合，深层的 sink 行为此时还未形成），而**最后一层是"远处全灭、预算集中倾倒 sink"**（语义检索已在中层完成，末层注意力近乎"收尾复位"：把无用预算倒进 sink，让残差流保住已聚合的特征）。"自我+局部偏好"确实被 layer 0 干净地呈现出来了。

### 5.2 裸 prompt（raw_prompt.png）：thinking 从来不在输入里；sink 是"位置 0"现象，与 <|im_start|> 无关

实测观察：图里只剩输入 token，9×9 矩阵。两个结论：

**① 是的，序列里只剩输入 token。** `--no-chat-template` 让 `build_input_ids`（attention_cli.py:147-148）把 `text = prompt` 原样喂 tokenizer：没有 system/user 包装、没有 `<|im_start|>`/`<|im_end|>`、没有 `assistant` 起始头。要澄清的是 **thinking 从来不属于"输入序列"**——无论哪个模式。chat template 的 `add_generation_prompt=True` 只补到 `<|im_start|>assistant\n` 为止；`<think>` 是模型**生成**出来的 token（5.3 里红线下方从 think 开始正是证据）。裸 prompt 模式下 `max_new_tokens` 默认 0，既无格式 token 也无生成 token，热力图自然只有 9 个输入 token。
（细注：裸文本分词出 9 个 token = `北京/的/天?/气/怎?/么/样` 等，其中"天""怎"落在 byte-level fallback（单 token decode 显示 �，图上 □ 是字体缺字）——带空格的孤立汉字对 BPE 是低频组合。）

**② 关键发现：sink 不但没消失，反而更强。** 位置 0 现在是普通实词「北京」，最后一层 head 平均下 sink mean = **83.1%**（各行 0.75~0.87，对角线 0.229，其余 0.016），高于 chat template 时的 76.1%。这修正了本文档早先"sink 现象通常变弱"的预期，也印证了 StreamingLLM 的核心论点：**attention sink 绑定的是"位置 0"**（唯一出现在每一行因果窗口内的位置），而不是 `<|im_start|>` 这个特殊 token 的"魔力"。甚至序列越短、无意义格式 token 越少，倾倒越集中。Layer 0 对照不变（sink 0.147 / 对角线 0.585），浅深分工与 5.1 一致。

### 5.3 带生成（with_gen.png）：红线下方 = decode 各步的注意力行

实测观察：红线下方第一个 token 是 `<think>`。这正是 Qwen3 thinking 模型的行为：assistant 起始头之后**模型自产**的第一个 token 就是 think 块开头（template 未显式注入）。红线上方 29 个 prompt token，下方每行 = 一个生成步对该步全部历史的注意力分布——即第 2 节"第 i 行 ≡ decode 走到位置 i 的那一行"的直接展示。可顺带观察：红线下方的行第一列通常依然亮（sink 在生成步同样存在）。

### 5.4 单头视角（head9.png / head14.png）：16 个头是 16 组独立投影，训练中涌现分工

实测观察：head 9 第一列热度明显高；head 14 对角线热度高。定量（最后一层，行内归一化）：

| head | sink 占比 | self 占比 | 角色 |
| --- | --- | --- | --- |
| head 9 | **0.932** | 0.047 | sink 头（近乎全倾倒） |
| head 14 | 0.521 | **0.473** | 局部头（自己+邻居） |
| 16 头范围 | 0.52~0.93 | 0.05~0.47 | 全部 sink>0.5 |

**机制**：多头注意力里每个 head 有自己独立的 `W_q^(h)`/`W_k^(h)` 投影（Qwen3-0.6B 为 16 query 头 / 8 kv 头的 GQA，head_dim=64），等于在同一层里并行跑 16 个"视角"，训练中自发特化：

- **sink 头（head 9 型）**：其投影使得任意位置的 query 与位置 0 的 key 点积在行内 softmax 中碾压一切。功能解释是 **"no-op 头"**：当这个头对当前预测没有可贡献的信息时，把输出锁定为 sink token 的 value（近似常数向量），相当于"让自己静默"而不向残差流注入噪声——这是 StreamingLLM 所说"注意力倾倒"的单元级形态。
- **局部头（head 14 型）**：对自己与邻近位置打分高，承担局部语法/逐词传递类特征（文献中的 previous-token / local head 一族）。
- **平均视图（默认 `--head -1`）的作用**：16 头相加后，公共成分（sink，16/16 头都有）被保留和放大，单头差异被稀释——平均图的对角线只有 0.179，正是因为像 head 14 这样的局部头是少数。**先看平均图识"公共模式"，再回到单头图看"分工"，是读这类可视化的正确顺序。**
