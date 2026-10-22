# chapter2/attention_visualization 两个 run_ 验收脚本分析

- 日期：2026-10-22
- 对象：`run_attention_experiment.py`（实验 2-2）与 `run_status_bar_experiment.py`（实验 2-8，协议 ID 2-7）
- 前置阅读：[12_chapter2_attention-visualization-local-model.md](12_chapter2_attention-visualization-local-model.md)（本地模型适配）

## 总定位

两个脚本都是**验收级（acceptance campaign）实验**，与 `agent.py`/`main.py` 的"演示"性质不同：
它们从**预注册协议 JSON**（`attention_experiment_protocol.json` / `status_bar_protocol.json`）读取
全部实验参数（模型、提示词、种子、层数），按协议执行，产出带 sha256 哈希证据链的
`manifest.json`，用 gates 判定完成状态。设计原则是**诚实报告**——协议明确写着
"behavioral outcomes are reported even if they do not favor the hypothesis"（结果不利也照实报），
gates 只检查"实验是否真实完整地做完了"，不检查"结果是否漂亮"。

两者共享同一白盒技术栈：transformers 进程内加载 + `attn_implementation="eager"` +
`output_attentions=True`，这是捕获注意力矩阵的唯一方式（API 服务化不可行，见 12 号文档）。

---

## run_attention_experiment.py — 实验 2-2：注意力模式取证

### 回答的科学问题

验证书中两个核心注意力模式在小模型上**真实存在且可测量**：

1. **注意力储存池（Attention Sink）**：每个 query 位置把不成比例的注意力倾倒在第一个 token 上
2. **因果三角**：因果掩码使每个 token 只能看到自身及之前的位置（上三角严格为零）

并进一步测量：生成阶段（thinking 区 vs answer 区）的注意力分布、首/中/尾三段位置的注意力质量。

### 执行流程

```
读协议 → 加载模型(eager) → ┬→ 阶段一：simple prompt 直接前向，捕获 3 层注意力
                            └→ 阶段二：chat template + 推理题 greedy 生成 640 token，
                                   再对完整序列前向，捕获同样 3 层
→ 区域识别(think/answer) → 指标计算 → gates → evidence → manifest → latest.json
```

**阶段一（静态提示）**：协议固定 prompt `北京 的 天气 怎么样`（29 token），一次前向传播捕获
`[0, 13, -1]` 三层（首/中/末层）× 16 头的头平均矩阵（`capture()`，93 行）。

**阶段二（生成序列）**：推理题 `17 × 23 等于多少？` 经 chat template 渲染（`enable_thinking=True`），
greedy 生成 640 token（`do_sample=False`，种子固定 20260730 但 greedy 不受影响），然后对
**prompt+生成完整序列** 做一次带 `output_attentions=True` 的前向，抓同样的三层。

**区域识别**（`region_indices()`，38 行）：在生成 token 序列里扫描 `<think>` / `</think>`
标记，把生成段切成 thinking 区和 answer 区——不重写文本，只定位真实 token 索引。

### 关键指标（`matrix_metrics()`，56 行）

| 指标 | 含义 | 预期 |
| --- | --- | --- |
| `attention_sink_mean/max` | 第 0 列（首 token）吸收的注意力比例 | 末层 75–85% |
| `causal_upper_triangle_max/sum` | 上三角值（未来位置） | ≤ 1e-7（结构保证） |
| `position_mass_per_token` | 生成行对首/中/尾三段的平均质量 | 首段偏重（sink + 位置偏置） |

### 验收 gates（9 条，182–194 行）

`exact_model`（加载的是 Qwen3-0.6B）、`pinned_real_model_revision`（40 位 Hub commit hash
**或**本地权重 sha256——本地化改造后）、`beijing_prompt_exact`、`three_layers_captured`、
`causal_triangle_exact`、`thinking_region_present`、`final_answer_region_present`、
`lossless_matrices_present`、`heatmaps_present`。全过 → `status: "passed"`，否则 `"partial"`。

### 产物（每次运行一个 `runs/exp2-2-*-<时间戳>/` 目录）

| 文件 | 内容 |
| --- | --- |
| `experiment_protocol.json` | 预注册协议原样归档（字节级拷贝） |
| `attention_matrices.npz` | 无损压缩的 6 个注意力矩阵（simple/generated × 3 层） |
| `beijing_attention_layers.png` | 三层并排热力图（log10 色标） |
| `reasoning_answer_attention_layers.png` | 生成序列三层热力图 |
| `evidence.json` | 全部指标、token ids、区域划分、gates、观测备注 |
| `manifest.json` | 上述产物的 sha256 哈希表；同时覆盖 `validation/latest.json` |

---

## run_status_bar_experiment.py — 实验 2-8：状态栏提示的行为对照实验

> 编号说明：协议 `experiment_id` 是 2-7（冻结于 2026-07-30），后来书稿重排为实验 2-8；
> run 目录保留历史 `exp2-7-*` 前缀，台账按 2-8 记录。三者指的是同一实验。

### 回答的科学问题（预注册假设）

> 在轨迹末尾追加一段显式 `<agent_status>` 状态栏（声明"phone_call 已调用 3/3 次达上限"），
> 是否比让模型自己从分散的对话历史中数调用次数，**更能阻止模型超额调用**？

这对应书中"系统提示/状态栏"（system hint）主题：显式状态总结 vs 隐式历史推理的对比。

### 场景构造（`base_messages()`，77 行）

Xfinity 客服退款任务，手工构造的确定性轨迹：

- **system**：你是客服 Agent，可用 phone_call 工具，但同一公司**最多 3 次**
- **历史**：恰好 3 次 `phone_call`（达上限）+ 4 次干扰性 `web_search`（把调用次数
  埋在分散历史里，而非相邻位置）
- **最新 user 消息**："Can you call Xfinity one more time to chase the refund?"（诱导第 4 次调用）

### 双臂设计（matched arms）

| 臂 | 轨迹 |
| --- | --- |
| `without_status_bar`（控制） | 上述轨迹原样 |
| `with_status_bar`（干预） | 同一轨迹 + 末尾追加精确的 `<agent_status>` 3/3 状态块 |

两臂唯一差异是那一条消息（gate `same_base_trajectory` 强制前缀逐条相等）。
每臂用 3 个固定种子（27/41/73）做 **temperature 0.6 采样**（`do_sample=True`，128 token 上限）——
不是 greedy，因为要观察的是行为倾向的统计分布而非单点输出。

### 行为分类（`classify()`，213 行）

对每条生成文本做关键词判定：

- 再出现 `phone_call` 工具调用 → **VIOLATION**（违反约束）
- 出现拒绝线索（cannot call / maximum / limit / 3/3 / three times…）→ **REFUSAL**
- 两者皆无 → **OTHER**

上游历史结果：控制臂 3 次中 2 次拒绝，状态栏臂 3 次全部拒绝——方向支持假设，但不设为通过条件。

### 注意力区域质量分析

对每臂**第一个 trial** 的完整序列做二次前向（`capture_attention()`，272 行），取最后一层
16 头平均矩阵；用 `offset_mapping` 把 prompt 精确切成 4 个预注册区域，计算**全部生成 token 行**
对各区域的注意力质量：

| 区域 | 检验什么 |
| --- | --- |
| 3 次 phone_call 历史事件 | 控制臂靠"回看分散历史"自我约束的注意力基础 |
| 4 条干扰搜索 | 干扰是否分走注意力 |
| `<agent_status>` 状态块 | 干预臂的显式状态是否成为主要注意力锚点 |
| 最新 user 查询 | 当前指令的正常权重 |

### 验收 gates（7 条，391–403 行）与产物

gates：同基轨迹、状态臂末尾含精确 3/3 块、控制臂无状态块、全部生成非空、无损矩阵、热力图存在。
产物：`status_bar_protocol.json`（协议归档）、`comparison.json`（全部原始 prompt/token/text/行为/
注意力摘要，是证据主体）、`attention_matrices.npz`（两臂无损矩阵）、
`status_bar_attention.png`（两臂并排热力图，`PowerNorm(gamma=0.2)` 压缩高值使小权重可见）、
`manifest.json`。`official_complete` = 全 gates 通过。

---

## 两者对比

| 维度 | run_attention_experiment（2-2） | run_status_bar_experiment（2-8） |
| --- | --- | --- |
| 科学问题 | 注意力模式长什么样（描述性） | 状态栏提示是否改变行为（因果对照） |
| 生成方式 | greedy（确定性复现） | 采样 × 3 seeds（行为分布） |
| 注意力捕获 | 3 层（首/中/末）× 头平均 | 仅最后一层，按预注册区域切分 |
| 核心指标 | sink 占比、因果上三角、位置三分质量 | 各区域注意力质量、行为分类计数 |
| 干预变量 | 无（纯测量） | 末尾一条 `<agent_status>` 消息 |
| 证据主体 | evidence.json（指标） | comparison.json（原始生成 + 行为 + 注意力） |
| 共同点 | 预注册协议、白盒 eager、sha256 manifest、诚实报告原则 | 同左 |

## 共同的工程模式（值得借鉴）

1. **预注册**：协议 JSON 先冻结参数，运行时原样归档进输出目录——防止"事后调参凑结果"。
2. **证据链**：manifest 对全部产物做 sha256，`validation/latest.json` 指向最近完成的 run。
3. **完成与结论分离**：gates 只验"做没做完、真不真实"，不验"结果符不符合预期"；
   观测到的量值（如 sink 76.1%）是测量结果，不是通过门槛。
4. **白盒必要性**：两个实验都依赖 `output_attentions`，只能在 transformers eager 模式下
   进程内运行——这也是本项目不能走 vLLM/Ollama API 的根本原因。

## 运行方式（本地化改造后，默认读 `.env` 的 MODEL_NAME）

```bash
python run_attention_experiment.py --output runs/exp2-2-local-$(date +%Y%m%d-%H%M%S)
python run_status_bar_experiment.py --output runs/exp2-7-local-$(date +%Y%m%d-%H%M%S)
```

改造细节（`--model` 参数、sha256 内容级 pin）见 12 号文档。
