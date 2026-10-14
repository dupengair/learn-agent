# 实验 1-2 web-search-agent：examples.py 改造与后端判断函数共享化

- **实验**：`ai-agent-book/chapter1/web-search-agent/examples.py`
- **日期**：2026-10-14
- **前置**：[03_chapter1_web-search-agent-quickstart.md](03_chapter1_web-search-agent-quickstart.md)（quickstart 同思路改造，决策沿用：跟随 .env / OLLAMA_MODEL / 问题按模式区分）
- **状态**：✅ 已实施并验证通过

## 原版依赖分析（与 quickstart 的差异）

| 检查项 | quickstart.py | examples.py |
| --- | --- | --- |
| 加载 .env | ❌ 不加载（隐藏坑） | ✅ 顶部 `from config import Config` 触发 `load_dotenv()` |
| 构造 Agent 传 base_url/model | ❌ 硬编码默认值 | ❌ 同样问题：6 处 `WebSearchAgent/AdvancedWebSearchAgent(Config.get_api_key())` 只传 key |
| Kimi 导向文案 | banner + 结尾 | `main()` 标题、`Config.validate()` |
| 时效性问题 | 3 个全时效 | 仅示例 1 第一问（「OpenAI 最新 GPT」）；其余 5 个示例均为知识性问题，mock 下可用 |

## 实施记录

### 1. agent.py：抽取共享函数（消除 UI 层重复）

在 `run_mock_search` 之后新增两个模块级函数，语义与 `SEARCH_BACKEND` 机制同源：

- `is_local_backend()`：`SEARCH_BACKEND=mock` 或端点含 localhost/127.0.0.1 → 本地模式；
- `default_model()`：本地模式读 `OLLAMA_MODEL`（回退 `qwen3.5-4b:q4`），Kimi 模式返回 `kimi-k3`。

### 2. quickstart.py：改为导入共享函数

删除本地的 `_is_local_backend()` / `_display_model()` 副本，统一从 `agent` 导入（7 处调用点同步替换）。

### 3. examples.py 改动（4 处）

1. **`_make_agent(agent_cls)` 工厂函数**：显式传 `api_key` / `base_url=Config.KIMI_BASE_URL` / `model=default_model()`，替换全部 6 处构造（1 处 `WebSearchAgent` + 5 处 `AdvancedWebSearchAgent`）；
2. **示例 1 问题按模式区分**：本地模式换「什么是 ReAct 循环？」等知识性问题，Kimi 模式保留原时效性问题；
3. **`main()` 的 `Config.validate()` provider 感知**：本地模式跳过 Kimi key 校验；
4. **标题文案动态化**：`🎯 Web Search Agent - 高级示例（本地模式 · qwen3.5-4b:q4）`。

## 验证结果

- **端到端**（`printf "1\n" | python examples.py` 跑示例 1）✅：菜单正确显示「本地模式 · qwen3.5-4b:q4」，问题集已切换，ReAct 循环正常生成结构化答案；
- **回归**：`pytest tests/` 44 passed（含 `test_examples.py` 对 `AdvancedWebSearchAgent` 的单测——测试用 `__new__` 构造实例，不触及工厂与示例函数，改动无影响）；`py_compile` 三个文件语法 OK；新增行行宽 ≤100。

## 现状小结（web-search-agent 三个入口的本地化状态）

| 入口 | 状态 | 说明 |
| --- | --- | --- |
| `main.py` | ✅ 已对齐改造 | 见 [05 号文档](05_chapter1_web-search-agent-main.md)：默认模型跟随 .env，零参数运行 |
| `quickstart.py` | ✅ 已改造 | 03 号文档 |
| `examples.py` | ✅ 已改造 | 本文档 |

三个入口现在共用 `agent.py` 的 `is_local_backend()` / `default_model()`，`.env` 是唯一配置源。
