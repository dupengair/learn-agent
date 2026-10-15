# learn-agent · AI Agent 学习仓库

个人 AI Agent 开发学习仓库：以《深入理解 AI Agent：设计原理与工程实践》为主线，边读边做实验，沉淀学习笔记，并把配套实验改造为可在**本地 Ollama 模型**上运行的个人版本。

## 仓库结构

```
├── CLAUDE.md            # Claude Code 工作约定（文档沉淀规范、常用命令等）
├── docs/                # 学习笔记与经验输出（本次学习的主要产出）
│   └── ai-agent-book/
│       ├── 01_chapter1_web-search-agent.md            # 依赖检查 + Ollama/mock 改造方案与实施
│       ├── 02_chapter1_web-search-agent.md            # ReAct 轨迹（想→做→看）代码流程解析
│       ├── 03_chapter1_web-search-agent-quickstart.md # quickstart.py 本地化改造
│       ├── 04_chapter1_web-search-agent-examples.md   # examples.py 本地化 + 共享函数抽取
│       ├── 05_chapter1_web-search-agent-main.md       # main.py 对齐（三入口统一）
│       ├── 06_chapter1_context.md                     # 实验 1-1 context 消融实验解析
│       ├── 07_chapter1_context-ablation-analysis.md   # 消融五组实测轨迹差异分析（Ollama）
│       ├── evidence/    # 实验运行证据（ReAct 轨迹 JSON）
│       └── patches/     # 预留
└── ai-agent-book/       # 教科书个人修改版（详见下方说明）
```

## 关于 ai-agent-book/

内容来自 [bojieli/ai-agent-book](https://github.com/bojieli/ai-agent-book)（《深入理解 AI Agent：设计原理与工程实践》，Apache-2.0），感谢原作者与社区贡献者。本仓库中的副本是**个人学习用修改版**：

- 已删除不需要的翻译版本等内容
- 对 `chapter1/web-search-agent/` 做了本地化改造（见下）
- 阅读正文请看 `ai-agent-book/book/chapter1.md` ~ `chapter10.md`，各章实验在 `chapterN/` 下

## 本地化改造：web-search-agent 支持本地 Ollama

原实验的联网搜索依赖 Moonshot 服务端专有 API（Formula），只能用 Kimi K3。改造后（`chapter1/web-search-agent/`）：

- **`agent.py`**：新增 `SEARCH_BACKEND=mock` 搜索后端（固定样本结果，绕开 Moonshot 专有端点），`MAX_TOKENS` 环境变量化，兼容 Ollama 的 `reasoning` 字段；默认行为完全不变
- **三个入口统一跟随 `.env`**：`main.py` / `quickstart.py` / `examples.py` 均可零参数运行，共用 `agent.py` 的 `is_local_backend()` / `default_model()`
- 改造原理与验证过程见 `docs/ai-agent-book/01` ~ `05` 号笔记

### 环境与快速开始

- Python 3.11–3.13；本地 [Ollama](https://ollama.com/) 运行 `qwen3.5-4b:q4`（OpenAI 兼容端点，需支持 function calling）
- 配置 `ai-agent-book/chapter1/web-search-agent/.env`（参考同目录 `env.example`）：

  ```env
  KIMI_BASE_URL=http://localhost:8000/v1   # 本机 Ollama 的 OpenAI 兼容端点
  MOONSHOT_API_KEY=ollama                  # 占位 key，本地服务不校验
  OLLAMA_MODEL=qwen3.5-4b:q4
  SEARCH_BACKEND=mock
  MAX_TOKENS=2048
  ```

- 运行（在 `ai-agent-book/chapter1/web-search-agent/` 下）：

  ```bash
  python main.py "月球的直径是多少公里？"      # 单次问答，打印 ReAct 轨迹
  python quickstart.py                        # 引导式体验
  python examples.py                          # 高级示例（批量/比较/事实核查）
  ```

- 回归测试：`python -m pytest tests/`（44 个用例）

> 切回 Kimi 联网模式：`.env` 换成真实 Moonshot key、去掉 `SEARCH_BACKEND=mock`、端点回官方即可，代码零改动。本地 mock 模式下模型不联网，演示的是 ReAct 循环链路本身。

## 本地化改造：chapter1/context 消融实验默认本地 Ollama

实验 1-1（上下文消融）改造后（`chapter1/context/`）：

- **`main.py` / `run_experiment_1_1.py`**：`--provider` 默认改为 `ollama`，`--model` 默认读 `OLLAMA_MODEL`（复制 `env.example` 为 `.env` 即可零参数运行）
- **`env.example`**：新增本地 Ollama 配置段（`OLLAMA_BASE_URL` / `OLLAMA_API_KEY` / `OLLAMA_MODEL`）
- **`agent.py`**：修复 `openai.APITimeoutError` 的导入方式，增加 `RateLimitError` 捕获
- **实测**：五组消融已用 `qwen3.5-4b:q4` 跑通（2026-10-15），实测记录见 `chapter1/context/README.md`「本地 Ollama 执行」一节，逐组轨迹分析见 `docs/ai-agent-book/07_chapter1_context-ablation-analysis.md`

## 学习进度

| 章节 | 状态 | 笔记 |
| --- | --- | --- |
| 第 1 章 Agent 基础（实验 1-1 context、实验 1-2 web-search-agent） | ✅ 已跑通 + 本地化改造 | docs/ai-agent-book/ 01–07 |
| 第 2–10 章 | 📚 待学习 | — |

## 许可

- `ai-agent-book/` 内容遵循上游的 [Apache-2.0](ai-agent-book/LICENSE) 许可
- `docs/` 与根目录文件为个人学习产出
