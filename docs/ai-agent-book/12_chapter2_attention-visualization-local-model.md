# chapter2/attention_visualization 本地模型适配记录

- 日期：2026-10-22
- 环境：conda `ai-gpu`（Python 3.12，torch 2.8.0+cu128，transformers 4.57.6，RTX 4050 Laptop 6GB）
- 模型：`/home/dupengair/shared/LLM/Fine-tuning/model/Qwen3-0.6B`（HF safetensors 原版，1.5GB）

## 为什么不能走 API 服务化（Ollama / vLLM）

本实验是**白盒**实验：`agent.py` / `main.py` / `attention_cli.py` / `run_*.py` 全部用
transformers 进程内加载权重 + `attn_implementation="eager"` + `output_attentions=True`
捕获注意力矩阵。两条服务化路线都不可行：

1. **协议层**：OpenAI `chat.completions` 响应体没有 attention 字段（只有 content / tool_calls / 可选 logprobs）。
2. **引擎层（根本原因）**：vLLM 的 PagedAttention/FlashAttention 与 Ollama (llama.cpp) 的 GPU kernel
   只输出加权和后的上下文向量，**从不物化 `[seq, seq]` 注意力矩阵**。不是"没返回"，是"不生产"。

transformers 能拿到注意力是因为 eager 实现显式算出完整注意力矩阵再乘 V。
结论：**只要实验目标是看注意力矩阵，推理必须在 transformers eager 模式下进程内进行。**

本机 Ollama 的 `qwen3.5-4b:q4` / `qwen3.6-35b-a3b:q4` 还额外不可用：GGUF 格式无法被
transformers 直接加载；且 4B FP16 ≈ 8GB > 6GB 显存，q4 量化的注意力模式本身已失真。

## 实际改动（3 处，约 5 行）

排查发现：`config.py` 原本**没有被任何入口 import**，`.env` 是死配置——模型全部由
CLI 参数或硬编码默认值决定。本次适配顺带修复了这一链路：

| 文件 | 改动 |
| --- | --- |
| `agent.py` | 新增 `from config import MODEL_NAME`；构造函数默认值 `"Qwen/Qwen3-0.6B"` → `MODEL_NAME`（`main.py` 的 `ReActAttentionAgent` 继承此默认值，自动生效） |
| `attention_cli.py` | argparse `-m/--model` 默认值同样改为 `MODEL_NAME` |
| `run_attention_experiment.py` | 新增 `--model` 参数（默认 `MODEL_NAME`），模型加载从协议硬编码改为 CLI 覆盖；本地路径无 Hub `_commit_hash`，改用 `model.safetensors` 的 sha256 作为内容级 pin（gate `pinned_real_model_revision` 放宽为「40 位 revision **或** 权重 sha256 存在」），evidence 如实记录实际模型路径 + `weights_sha256`；协议 JSON 本身不动 |
| `run_status_bar_experiment.py` | `--model` 默认值 `"Qwen/Qwen3-0.6B"` → `MODEL_NAME`（原有 `local_files_only=True` 对本地路径无害；gates 无 revision 检查，零风险） |
| `.env`（新建） | 精简为一行 `MODEL_NAME=/home/dupengair/shared/LLM/Fine-tuning/model/Qwen3-0.6B`（DEVICE 不写，auto 检测必然选中 cuda） |

`.gitignore` 已忽略 `.env` 与 `*.png`，本地路径不会被提交。`run_attention_experiment.py` /
`run_status_bar_experiment.py` 是验收 campaign 脚本（有 pinned-revision 检查），保持不动，
需要时用 `--model` 参数覆盖。

## 两种"模型名"的区别（MODEL_NAME vs --served-model-name）

`config.py` 里 `os.getenv("MODEL_NAME", "Qwen/Qwen3-0.6B")` 的默认值与 vLLM 的
`--served-model-name qwen3-0.6b` 长得像，但机制完全不同——一个是**地址**，一个是**别名**。

### transformers 白盒加载：不能随便写，它是"地址"

`from_pretrained(...)` 的字符串是 **HF Hub 仓库 ID 或本地磁盘路径**，transformers 靠它定位权重：

- 写 `Qwen/Qwen3-0.6B` → 去 HF Hub 解析仓库（`组织/仓库名` 格式），命中本地缓存则用缓存
- 写 `/home/dupengair/shared/LLM/Fine-tuning/model/Qwen3-0.6B` → 直接读本地目录
- 随便写会直接报错：`OSError: xxx is not a local folder and is not a valid model identifier`

注意 `config.py` 那行只是**默认值**（`.env` 缺失时才生效）；实际生效的是 `.env` 里的本地路径，
所以运行日志显示的是本地路径而非 HF 仓库名。

### vLLM `--served-model-name`：随便写，它是"别名"

```bash
vllm serve ./Qwen3-0.6B ... --served-model-name qwen3-0.6b
#         ↑ 真实权重地址        ↑ 对外暴露的别名
```

服务启动时权重已加载进显存、绑定到进程，`qwen3-0.6b` 只是已加载实例的**对外标识**，唯一作用是
让客户端请求体 `"model": "qwen3-0.6b"` 与之字符串匹配（不一致返回 model not found）。
改成 `--served-model-name foo` 配 `"model": "foo"` 照样能跑；也可起多个别名
`--served-model-name a b c` 调用同一个模型。Ollama 的模型 tag（`qwen3.5-4b:q4`）同理，
底层绑定 manifest digest。

### 对照表

| | transformers 白盒 | vLLM / Ollama API |
| --- | --- | --- |
| 名字的作用 | 定位权重（Hub ID 或本地路径） | 服务端对外别名 |
| 能否随便写 | ❌ 必须真实存在 | ✅ 只要客户端 `"model"` 字段对上 |
| 写错了会怎样 | 加载时直接报错 | 请求时 404 model not found |
| 与权重的关系 | 强绑定（就是路径/仓库） | 解耦（权重在启动命令里已指定） |

## 验证结果（2026-10-22）

```bash
cd ai-agent-book/chapter2/attention_visualization
conda activate ai-gpu
python attention_cli.py --output results/smol_verification_heatmap.png
```

- 模型从本地路径加载到 cuda，28 层 × 16 头注意力全部捕获
- 默认 prompt `北京 的 天气 怎么样`（29 token）最后一层头平均 **attention sink = 76.1%**
  （README 预期区间 75–85%，吻合）
- 热力图中因果三角、`<|im_start|>` sink 列、chat template 三段结构均清晰可见
- 验证产物：`results/smol_verification_heatmap.png`（被 gitignore，不入库）

依赖补充：`pip install seaborn`（画图需要，此前 ai-gpu 缺失）。

## 三条运行路线

```bash
# A. CLI 出热力图 PNG（无需前端）
python attention_cli.py                                # 默认即本地模型
python attention_cli.py --compare-layers 0 13 -1       # 跨层对比 attention sink

# B. 基础轨迹（写 frontend/public/trajectories/，前端可视化）
python agent.py

# C. ReAct + 工具轨迹
python main.py

# D. 验收 campaign（2026-10-22 改造后：默认读 .env 的 MODEL_NAME 本地路径，完全离线）
python run_attention_experiment.py --output runs/exp2-2-local-$(date +%Y%m%d-%H%M%S)
python run_status_bar_experiment.py --output runs/exp2-8-local-$(date +%Y%m%d-%H%M%S)
# 两脚本均可 --model 覆盖：传 Qwen/Qwen3-0.6B 则走 HF 缓存（需网络或 HF_HUB_OFFLINE=1，
# 获得 40 位 commit revision）；默认本地路径则用权重 sha256 作内容级 pin。
# 注意：run_attention_experiment.py 会把 manifest 拷贝覆盖 validation/latest.json。
```

## 注意事项

- 跑本实验前**停掉 vLLM / Ollama 服务**：vLLM `--gpu-memory-utilization 0.7` 预占 ~4.3GB，
  6GB 卡剩余空间不够 eager 注意力的激活开销。
- `ruff` 检查：改动文件需 `ruff check` 通过（line-length 100）。
