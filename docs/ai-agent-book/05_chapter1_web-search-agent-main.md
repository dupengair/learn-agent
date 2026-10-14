# 实验 1-2 web-search-agent：main.py 对齐本地化（默认跟随 .env）

- **实验**：`ai-agent-book/chapter1/web-search-agent/main.py`
- **日期**：2026-10-14
- **前置**：03（quickstart）、04（examples）号文档；决策沿用：跟随 .env、无需显式参数
- **状态**：✅ 已实施并验证通过

## 改造前差异

`main.py` 原本就是三个入口里最接近可用的：`--base-url` 默认取 `Config.KIMI_BASE_URL`（跟随 .env），key 走 `Config.get_api_key()`。差异只剩两处：

1. `--model` 默认 `Config.DEFAULT_MODEL`（config.py 硬编码 `kimi-k3`）→ 本地模型必须显式传 `--model qwen3.5-4b:q4`；
2. key 缺失时的处理是 Kimi 导向（直接报错退出，提示设 Moonshot key）。

## 实施记录（main.py 3 处 + 测试 1 处）

1. **import** 增加 `default_model, is_local_backend`（来自 agent.py 共享函数）；
2. **`--model` 默认值** `Config.DEFAULT_MODEL` → `default_model()`：本地模式自动取 `OLLAMA_MODEL`（qwen3.5-4b:q4），Kimi 模式 `kimi-k3`；`--help` 文本同步说明；argparse 的 `description` 与 `--provider` help 去掉 Kimi 专用表述；
3. **key 检查 provider 感知**：本地模式且无 key 时自动补占位值 `"ollama"`（与 quickstart/examples 一致），Kimi 模式保留原报错与 OpenRouter 兜底提示；
4. **`tests/test_main.py` 契约更新**：`test_parser_defaults_to_interactive_kimi_mode` 原断言 `args.model == Config.DEFAULT_MODEL`（旧契约：写死 kimi-k3）→ 改为断言 `args.model == default_model()`，并在 docstring 记录契约变更原因。

## 验证结果

- **零参数运行** ✅：`python main.py "月球的直径是多少公里？"`（不带任何参数）自动使用 qwen3.5-4b:q4 @ localhost:8000，ReAct 循环两轮完成、答案正确；
- **`--help`** ✅：`--model` 显示「默认 qwen3.5-4b:q4，跟随 .env 模式，本地模式读 OLLAMA_MODEL」；
- **回归** ✅：`pytest tests/` 44 passed；`py_compile` 语法 OK；新增行行宽 ≤100（检出的 3 行超宽均为上游原有代码）。

## 最终状态：三个入口完全一致

```bash
python main.py "问题"        # 零参数，跟随 .env
python quickstart.py         # 跟随 .env
python examples.py           # 跟随 .env
```

配置源唯一（`.env`）：`KIMI_BASE_URL`（端点）、`OLLAMA_MODEL`（本地模型名）、`MOONSHOT_API_KEY`（占位）、`SEARCH_BACKEND=mock`（搜索后端）、`MAX_TOKENS`。切回 Kimi 联网模式 = 改 `.env`（真实 key + 去掉 SEARCH_BACKEND=mock + 端点回官方），三个入口无需改代码。
