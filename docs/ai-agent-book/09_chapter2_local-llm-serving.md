# Chapter 2 · local_llm_serving 实验环境适配记录

- 日期：2026-10-20
- 实验目录：`ai-agent-book/chapter2/local_llm_serving/`（实验 2-1：本地 LLM 服务部署与工具调用）
- 本机环境：Ollama 0.35.0 服务端跑在 **8000 端口**（`export OLLAMA_HOST=127.0.0.1:8000` 启动），已装模型 `qwen3.5-4b:q4`、`qwen3.6-35b-a3b:q4`；vLLM 模型权重在 `/home/dupengair/shared/LLM/Fine-tuning/model/Qwen3-0.6B`
- Python 环境：conda `ai-gpu`（Python 3.12），位于 `/home/dupengair/shared/conda/anaconda3/envs/ai-gpu`

> 注意：该实验实际在 **chapter2**（不是 chapter1）。上游默认 Ollama 组合为 `qwen3:0.6b` @ `:11434`，与本机环境不符，本次全部适配为 `qwen3.5-4b:q4` @ `:8000`。

## 一、代码适配（Ollama 写死信息 → 本机组合）

`ollama_native.py` 顶部新增两个常量，全项目引用：

```python
DEFAULT_OLLAMA_MODEL = "qwen3.5-4b:q4"
OLLAMA_BASE_URL = "http://localhost:8000"
```

改动文件与要点：

| 文件 | 改动 |
|---|---|
| `ollama_native.py` | `OllamaNativeAgent` / `OllamaOpenAICompatible` 默认模型与地址；所有 `ollama.Client()` 显式 `host=OLLAMA_BASE_URL`；demo/test 函数内写死的模型名与提示 |
| `main.py` | `_init_ollama()` 默认模型逻辑与 `Client(host=...)`；`_init_vllm()` 向 `VLLMToolAgent` 传入 `model=MODEL_NAME` |
| `benchmark.py` | `BACKEND_DEFAULTS["ollama"]` → `{base_url: http://localhost:8000/v1, model: qwen3.5-4b:q4}`；help 文案 |
| `run_experiment.py` | `--base-url` 默认 `http://localhost:8000`、`--model` 默认 `qwen3.5-4b:q4` |
| `demo_streaming.py` | 默认模型逻辑与 `Client(host=...)` |
| `check_compatibility.py` | 3 处 `ollama pull` 提示文案 |
| `test_ollama_thinking.py` / `test_benchmark.py` | 测试内模型名同步 |
| `test_parallel_tools.py` | `api_base` 11434→8000；`REAL_MODEL` 由 `qwen2.5:7b-instruct-q8_0`（本机没有）→ `qwen3.5-4b:q4` |

**未改动**：`experiment_protocol.json`（冻结协议设计文档，`run_experiment.py` 会记录其 sha256，实际模型由 `--model` 参数控制）；`README.md`（上游配套文档，其中 `qwen3:0.6b`/`11434` 描述的是默认环境，含大量实测记录示例，不随本机改动）。

## 二、vLLM 侧检查发现的问题与修复

用户 `.env`：

```bash
MODEL_NAME=qwen3-0.6b
MODEL_PATH=/home/dupengair/shared/LLM/Fine-tuning/model/Qwen3-0.6B
VLLM_HOST=localhost
VLLM_PORT=8000
```

发现两处断点并修复：

1. **`server.py` 从不使用 `MODEL_PATH`**：只拿 `MODEL_NAME` 启动 vLLM，而 `qwen3-0.6b` 不是合法 HF repo id，会去找 hub 下载并失败。修复：`config.py` 把 `MODEL_PATH` 放进 `VLLM_SERVER_CONFIG`；`server.py::_build_command()` 在 `MODEL_PATH` 存在时用 `--model <本地路径> --served-model-name <MODEL_NAME>`。
2. **`agent.py` 写死 `model="Qwen/Qwen3-0.6B"`**：与本地路径启动的 vLLM（served name 为路径）不匹配，请求会 404。修复：`VLLMToolAgent.__init__` 增加 `model` 参数（默认取 `config.MODEL_NAME`），`chat/chat_stream` 用 `self.model`；`main.py` 初始化时传入 `.env` 的 `MODEL_NAME`。

修好后 `.env` 语义 = 「本地权重 + 对外服务名 `qwen3-0.6b`」，agent 与 server 名字必然一致。

⚠️ **端口复用**：vLLM 与 Ollama 共用 8000。跑 vLLM 前必须先停 Ollama（反之亦然）。

## 三、Python 环境依赖（conda ai-gpu）

实验的 Ollama 主路径走 `ollama` Python 原生客户端（HTTP 客户端，与服务端 0.35.0 版本线独立、无冲突），本次增量安装：

- `ollama==0.6.3`（纯增量，未触碰现有依赖）
- `PyPDF2`（`tools.py` 需要）

其余依赖已存在：`openai 1.109.1`、`pydantic 2.12.5`、`httpx 0.28.1`、`python-dotenv`、`requests`。vLLM GPU 栈（`torch`/`vllm` 等）未安装——走 Ollama 路径不需要。

## 四、验证结果（真实运行）

| 测试 | 结果 |
|---|---|
| 默认配置连接（不传参） | ✅ `qwen3.5-4b:q4` @ `http://localhost:8000`，`2+3` → `5` |
| 工具调用完整 ReAct 循环 | ✅ 正确调用 `get_current_time` 并生成最终回复 |
| 全部改动文件 `py_compile` | ✅ 通过 |
| `benchmark.py --dry-run` | ✅ 配置解析正确（跑 Ollama 基准时记得加 `--backend ollama`） |

## 五、遗留说明

- ~~`run_experiment.py` 的 `--tokenizer` 默认 `Qwen/Qwen3-0.6B` 会触发 HF 下载~~ **已修复（10-20）**：本机 HF 缓存 `models--Qwen--Qwen3-0.6B` 残缺（只有 `refs/` 没有 `snapshots/`），tokenizer 加载失败/回退联网。现默认改为本地路径 `/home/dupengair/shared/LLM/Fine-tuning/model/Qwen3-0.6B`（vLLM 权重目录自带完整 tokenizer 文件），加载零网络。实测完整实验 60s 跑完，无任何下载。注意该 tokenizer 仍是 qwen3-0.6b 的词表，对 qwen3.5-4b 的 token 计数是近似值（ChatML 模板一致，不影响功能结论）。
- 首次完整实测（`runs/exp2-1-qwen3.5-4b-check/`，2026-10-20）：tool_case ✅、`local_model_verified: true`、吞吐 48.3 tok/s；KV Cache 命中 TTFT 均值 **0.196s** vs 未命中 **1.691s**，**miss/hit = 8.63×**，5/5 组命中更快——书中「前缀缓存 + 系统提示词开头改动导致全量重算」的直接证据。
- 运行方式（conda 环境）：

```bash
conda activate ai-gpu
cd ai-agent-book/chapter2/local_llm_serving
python main.py --backend ollama
python benchmark.py --scenario kv-cache --backend ollama
```
