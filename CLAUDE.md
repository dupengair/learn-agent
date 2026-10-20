# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## 工作区用途

这是一个 **AI Agent 开发学习工作区**，不是生产代码库：

- `ai-agent-book/` — 《深入理解 AI Agent：设计原理与工程实践》教科书（`bojieli/ai-agent-book` 的 git 克隆）。含 10 章中文正文 + 109 个配套实验。核心公式：**Agent = LLM + 上下文 + 工具**。
- `docs/` — 用户自己的学习笔记和经验输出，**所有笔记、总结、心得都写到这个目录**（Markdown 格式，中文）。

**回答语言：所有问题一律用中文回答。**

## 工作约定

- `ai-agent-book/` 是上游仓库的克隆，默认**不要改动**其中的书稿正文（`book/`）；为学习目的运行、调试、修改实验代码（`chapterN/`）是可以的。
- **【硬性规则】Python 环境规范**：一律使用 conda 环境 `ai-gpu`（`/home/dupengair/shared/conda/anaconda3/envs/ai-gpu`，Python 3.12）。**禁止使用 uv**（`uv run`/`uv sync` 会在仓库内创建 `.venv`，被 VSCode Python 扩展自动选中并注入终端，导致解释器错乱），也不要手动创建任何 `.venv`。缺依赖直接 `pip install <包名>` 装进 `ai-gpu`。
- 帮用户做实验、读章节时，优先参考各章 `README.md`（有推荐的 Starter 实验和阅读顺序）和 `EXPERIMENT_LEDGER.md`（记录真实运行条件与结果，避免把书中预期现象当成必然结论）。
- **【硬性规则】文档沉淀规范**：凡经 Claude 在本项目产生的文档（笔记、实验总结、原理解释、分析报告等），必须保存到 `docs/<目录名>/` 下，并按 `<数字序号>_<章节序号>_<内容>.md` 命名：
  - `<目录名>` — 来源项目目录名（如 `ai-agent-book`）；
  - `<数字序号>` — 两位数字序号（`01`、`02`…），同一 `<目录名>` 下按产生顺序递增；
  - `<章节序号>` — 来源章节（如 `chapter1`）；
  - `<内容>` — 文档主题，短横线连接。
  - 例：运行 `ai-agent-book/chapter1/web-search-agent/` 实验过程中由 Claude 产生的文档 → `docs/ai-agent-book/01_chapter1_web-search-agent.md`。
  - 新建文档前先查看目标目录中已有的序号，避免重号；不适用章节概念的内容（非章节相关），`<章节序号>` 段可省略，命名退化为 `<数字序号>_<内容>.md`。

## 常用命令（conda 环境 `ai-gpu`）

一律先 `conda activate ai-gpu`（Python 3.12）；上游 `pyproject.toml` 按 extras（`ch1`~`ch10`）组织依赖，但 conda 下没有 extras 概念，**遇到 `ModuleNotFoundError` 再 `pip install` 缺的包即可**，不预装整套依赖。

```bash
# 激活环境（所有命令都在此环境执行；VSCode 终端请确认 which python 指向 envs/ai-gpu）
conda activate ai-gpu

# 运行实验（进入实验目录直接跑，如第 2 章本地服务实验，Ollama @ :8000）
cd ai-agent-book/chapter2/local_llm_serving
python main.py --backend ollama

# 运行单个测试文件 / 单个测试（pytest 缺则先 pip install pytest）
python -m pytest tests/test_ch1_search_codegen_null_response.py
python -m pytest tests/test_ch1_search_codegen_null_response.py::test_name

# Lint（ruff：line-length 100，target py310；缺则先 pip install ruff）
ruff check <path>
ruff format <path>
```

`unsloth` 和 `vllm` 两套 GPU 栈互斥，不可同时安装。部分实验自带局部 `tests/` 目录和 `test_*.py`，也可直接 pytest 对应目录。

**API Key**：复制 `ai-agent-book/.env.example` 为 `.env`，填至少一个提供商 Key（OpenRouter 免费模型即可起步）；有些实验要求在其自身目录放 `.env`，以实验 README 为准。Provider 通过 `--provider` CLI 参数选择，只有明确列出 `ollama` 的实验才能用本地 Ollama。

## 架构

- `book/` — 中文正文正本：`introduction.md`、`chapter1.md`~`chapter10.md`、`reference-answers.md`（各章思考题参考答案）。其他语言版本是翻译，滞后于中文版。
- `chapterN/` — 每章配套实验，每个子目录是独立项目：`main.py` 入口、`agent.py` 核心逻辑、局部 `README.md`、局部 `tests/`、`EXPERIMENT_LEDGER.md`（该章实验运行记录）。
- `agentbook/` — 共享 Python 包，几乎所有实验都用它。`agentbook.providers` 是 LLM provider 解析的单一事实来源：所有实验都走 OpenAI 兼容端点，差异只是 base URL / 默认模型 / Key 环境变量，统一由 `resolve_backend("provider名")` 处理。新增 provider 只需在 `agentbook/providers/registry.py` 的 `PROVIDERS` 表加一项。
- `tests/` — 仓库级集中测试，命名 `test_chN_*.py` 按章组织。
- `chapterN/README.md` 中的实验类型图标：✅ 可独立运行 / 📖 复现指南（需自行 clone 外部仓库，如第 6–8 章的训练框架与评测基准，见主 README 附录的克隆脚本）/ 🚧 仅设计文档。

## 章节地图（学习时的定位）

| 章 | 主题 | Starter 实验 |
| :--: | --- | --- |
| 1 | Agent 基础：三要素、ReAct、Harness 工程 | `chapter1/context` |
| 2 | 上下文工程：KV Cache、提示注入、Agent Skills、压缩（全书最关键一章） | `chapter2/context-compression` |
| 3 | 用户记忆与 RAG | `chapter3/user-memory` |
| 4 | 工具：MCP、感知/执行/协作工具 | `chapter4/execution-tools` |
| 5 | Coding Agent：代码作为元能力 | `chapter5/coding-agent` |
| 6 | 交互扩展：异步、语音、Computer Use、机器人 | `chapter6/live-audio` |
| 7 | Agent 评估：指标、LLM-as-a-Judge、统计显著性 | `chapter7/tau2-bench-eval` |
| 8 | 模型后训练：SFT vs RL（唯一需要 ML 基础的一章） | `chapter8/cot-distillation` |
| 9 | 持续进化：从轨迹学习 | `chapter9/trajectory-verifier` |
| 10 | 多 Agent 协作 | `chapter10/parallel-web-research` |

完整学习路径见 `ai-agent-book/docs/zh-CN/LEARNING.md`。
