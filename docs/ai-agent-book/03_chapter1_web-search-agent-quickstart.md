# 实验 1-2 web-search-agent：quickstart.py 改造为跟随 .env 适配本地 Ollama

- **实验**：`ai-agent-book/chapter1/web-search-agent/quickstart.py`
- **日期**：2026-10-14
- **前置**：[01_chapter1_web-search-agent.md](01_chapter1_web-search-agent.md)（agent.py 的 mock 改造与 .env 配置）
- **状态**：✅ 已实施并验证通过

## 原版对 Kimi 的依赖分析

| 位置 | 依赖 | 性质 |
| --- | --- | --- |
| banner / 结尾文案 | 写死「基于 Kimi API」 | 软性（文案） |
| `check_api_key()` | 读 `MOONSHOT_API_KEY`，缺失即**强制交互输入**，提示去 Moonshot 申请 | 硬依赖 ① |
| `WebSearchAgent(api_key=api_key)` | **不传 `base_url` / `model`**，落到 `__init__` 签名默认值 `https://api.moonshot.cn/v1` + `kimi-k3` | 硬依赖 ② |
| `demo_questions` | 三个时效性问题（「OpenAI 最新产品」等） | 软性（mock 下无意义） |

### 关键隐藏坑：quickstart.py 读不到 .env

- `quickstart.py` 只 `from agent import WebSearchAgent`；`agent.py` 顶部不 import config，`config.py` 的 `load_dotenv()` 要到 `WebSearchAgent.__init__` 内部（延迟 import）才触发；
- 而 `check_api_key()` 在此**之前**执行 → `.env` 里的 `MOONSHOT_API_KEY=ollama` 对它不可见 → 强制交互 → 输 `skip` 直接退出。这就是「main.py 能直接跑、quickstart.py 不能」的原因；
- 另一个 Python 语义坑：`def __init__(self, base_url="https://api.moonshot.cn/v1")` 的默认值在**函数定义时**求值——即使 `.env` 配了 `KIMI_BASE_URL`，不显式传参也用不上。

## 已确认的决策（与用户逐条确认）

1. **模式定位 → 跟随 .env**：一份代码，`.env` 是唯一配置源；配本地端点即 Ollama 版，配 Moonshot key 即 Kimi 版。
2. **模型名来源 → `.env` 的 `OLLAMA_MODEL`**（默认回退 `qwen3.5-4b:q4`），换模型不改代码。
3. **演示问题 → 按模式区分**：mock 模式用不依赖时效的中性问题（演示重点是链路）；真实搜索模式保留原时效性问题。

## 实施记录

### quickstart.py 改动（4 处）

1. **模块顶部** `load_dotenv()`（修隐藏坑）+ `from config import Config`（复用端点解析与空值回退）；
2. **新增 `_is_local_backend()`**（`SEARCH_BACKEND=mock` 或端点含 localhost/127.0.0.1）与 **`_display_model()`**（本地读 `OLLAMA_MODEL`，Kimi 用 `kimi-k3`）；
3. **`check_api_key()` provider 感知**：本地模式下 key 只是占位符，缺失时自动补 `"ollama"`（本地服务不校验，但 OpenAI SDK 要求非空），不再强制交互；Kimi 模式保留原申请引导；
4. **构造 Agent 显式传参** `WebSearchAgent(api_key, base_url=Config.KIMI_BASE_URL, model=_display_model())`（修硬依赖②）；banner/结尾文案与 `demo_questions` 按模式动态化。

### .env 追加

```env
OLLAMA_MODEL=qwen3.5-4b:q4
```

## 验证结果

- **端到端**（`printf "1\n1\nn\n" | python quickstart.py`，选演示模式→第一个问题）✅：banner 正确显示本地模式与 `qwen3.5-4b:q4`；key 从 `.env` 自动读取（无交互）；ReAct 循环 3 轮完成（**本轮模型先搜中文关键词、再搜英文关键词、第 3 轮作答**——比 01 文档的冒烟测试多一轮，mock 结果固定时模型会尝试换关键词，正好展示多轮搜索形态）；
- **回归**：`pytest tests/` 44 passed；`py_compile` 语法 OK；新增代码行宽 ≤100（顺手修复了改造时合并 docstring 引入的一行 102 字符超宽）。

## 遗留观察点

- `agent.py` 的内部日志文案（如「开始调用 Kimi 搜索工具...」）仍是 Kimi 导向——上游原文，不影响功能，未改；
- `examples.py` 的 `AdvancedWebSearchAgent` 有独立的 Kimi 构造路径，本次未涉及，如需本地化可参照同思路改造。
