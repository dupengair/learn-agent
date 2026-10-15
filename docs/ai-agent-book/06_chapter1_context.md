# 实验 1-1 context：本地 Ollama 改造（默认本地化）

- **实验**：`ai-agent-book/chapter1/context/`（上下文消融实验：full / no_history / no_reasoning / no_tool_calls / no_tool_results 五种模式对照）
- **日期**：2026-10-15
- **前置**：01–05 号笔记（web-search-agent 改造，方案与命名约定沿用）
- **状态**：✅ **改造完成并全链路验证通过**（num_ctx=16384，冒烟 2 轮迭代 + 1 次工具调用，答案正确）

## 与 web-search-agent 的架构差异（改造量趋零的原因）

| | web-search-agent | context |
| --- | --- | --- |
| provider 接入 | 硬编码 Moonshot 端点 + Formula API | 标准 `agentbook.providers` 注册表，**`ollama` 原生支持**（`requires_key=False`，自动占位 key `"not-needed"`，`OLLAMA_BASE_URL` 可覆盖端点） |
| 工具实现 | `web_search` 在 Moonshot 服务端执行（专有） | **纯客户端 Python**（`ToolRegistry`：parse_pdf / convert_currency / calculate / code_interpreter），与模型端点无关 |
| 所需代码改造 | agent.py 加 mock 搜索后端 | 仅改 CLI 默认值（对齐"零参数即本地"） |

## 分析发现的 4 个关键点

1. **CLI 默认值硬编码**：`main.py` `--provider` 默认 `"doubao"`；不传 `--model` 走注册表默认 `qwen3:8b`（本地不存在）。`config.py` 的 `LLM_PROVIDER` / `MODEL_NAME` / `MODEL_MAX_TOKENS` 在 main.py 主路径**不被读取**（代码定义了但没用，`.env` 设了也无效）。
2. **消融脚本要显式占位 key**：`run_experiment_1_1.py` 的 `resolve_key()` 遍历 provider 的 `key_vars` 找环境变量，缺失即 `RuntimeError` 停止（拒绝 OpenRouter 兜底，保证证据的 direct-API 溯源）——ollama 的 key 变量是 `OLLAMA_API_KEY`，需设占位值。
3. **`max_tokens` 硬编码 8192**（agent.py:762/779），且 `parse_pdf` 会把 PDF 文本放进上下文——num_ctx=4096 必然截断。
4. **推理速度实测 ~11 token/s**（qwen3.5-4b:q4，thinking 模型，200 token / 18 秒）——单轮 ReAct（thinking 2000+ token + 工具调用 + 回答）需数分钟，全套消融（5 模式 × N 任务 × 多轮）需数小时。速度是本地 4b 模型的物理现实，非配置问题。

## 已确认决策

1. **改造范围 → B2 默认本地化**（与 web-search-agent 最终形态一致：零参数运行即本地）
2. **num_ctx → 16384**（需用户重启 Ollama 生效；4096 下实测一轮都跑不完）

## 实施记录（3 处，全部限于本实验目录）

1. **新建 `chapter1/context/.env`**：
   ```env
   OLLAMA_BASE_URL=http://localhost:8000/v1
   OLLAMA_API_KEY=ollama      # 占位：消融脚本 resolve_key 需要找到该变量
   OLLAMA_MODEL=qwen3.5-4b:q4
   ```
   并注释说明 `LLM_PROVIDER` / `MODEL_NAME` / `MODEL_MAX_TOKENS` 主路径无效的坑。
2. **`main.py` argparse**：`--provider` 默认 `"doubao"` → `"ollama"`；`--model` 默认读 `OLLAMA_MODEL`（回退 `qwen3.5-4b:q4`）；help 文本同步。
3. **`env.example`**：头部补本地 Ollama 配置块（复制为 `.env` 即可零参数运行）。

## 验证结果

- **`--help`** ✅：默认 provider=ollama、默认 model=qwen3.5-4b:q4 正确显示
- **回归** ✅：`pytest tests/` 25 passed；新增行行宽 ≤100（检出的 3 行超宽为上游原有）
- **num_ctx** ✅：重启后 `/api/ps` 确认 16384 生效
- **冒烟全链路** ✅：`--mode single --task "3.5 USD 等于多少人民币？"` → 2 轮迭代、1 次 `convert_currency` 工具调用、答案正确（3.5 × 7.24 = 25.34），`cached_tokens: 760` 说明前缀缓存也在工作

### 排查记录：两个曾误导排查方向的问题

1. **`--task` 不等于单任务模式**：main.py 需要 `--mode single`（默认 `interactive`）。只传 `--task` 会进入交互模式，而交互循环对 `input()` 的 EOF 处理是 `continue` 而非 `break`——非交互环境下（无 stdin）**EOF 死循环吃满 CPU**（py-spy 无权限，靠 `/proc/<pid>/status` 的 `State: R` + `socket 数 0` + faulthandler 栈定位到 `main.py:965 interactive_mode`）。上游小缺陷，未修（交互终端下无影响）。
2. **num_ctx=4096 的截断与 EOF 死循环症状不同**：前者是请求发出后模型输出被 clamp（等响应超时），后者是根本没发请求（本地死循环）。诊断时「模型未加载 + 进程满 CPU + 零 socket」三件套指向后者。

## 用法

```bash
cd ai-agent-book/chapter1/context
python main.py --mode single --task "你的任务"    # 零参数默认 ollama + qwen3.5-4b:q4
python main.py                                    # 交互模式（菜单）
python main.py --mode single --task "..." --provider kimi --model kimi-k3  # 切 Kimi（需真实 key）
python run_experiment_1_1.py --provider ollama    # 消融实验（依赖 .env 的占位 OLLAMA_API_KEY）
```

## 待办

- [ ] 可选：`run_experiment_1_1.py` 消融全套（耗时长，建议先 `--cases 1` 单模式试）
