# 实验 1-2 web-search-agent：依赖检查与本地 Ollama 改造方案

- **实验**：`ai-agent-book/chapter1/web-search-agent/`（Kimi K3 原生 Agent 能力，ReAct 搜索循环）
- **日期**：2026-10-14
- **目标**：① 确认当前环境依赖是否满足 requirements.txt；② 评估用本地 Ollama 模型 `qwen3.5-4b:q4` 跑通实验所需的改动
- **状态**：✅ **已实施并验证通过**——mock 搜索后端改造完成，冒烟测试全链路跑通，44 个局部测试全过，原有路径无回归

## 结论速览

1. 依赖检查用 `pip install --dry-run -r requirements.txt`（不是 `pip-compile --dry-run`）；实测当前 conda `ai-gpu` 环境**运行时依赖已全部满足**，缺的只是 pytest/black/pylint 等开发工具，跑实验不需要。
2. 这个实验**不能只改 .env 就切换到本地模型**：联网搜索是 Moonshot 服务端专有 API（Formula），Ollama 没有对应端点，且 `config.py` 根本不读 `OLLAMA_BASE_URL`。必须改 `agent.py` 中两处方法 + 自备一个搜索后端。
3. 实测 `qwen3.5-4b:q4` 经 Ollama 的 OpenAI 兼容端点**原生支持 function calling**（返回规范 `tool_calls`），方案技术上可行；但当前 Ollama 生效上下文只有 **4096**，多轮搜索前必须调大。
4. 本地模型没有联网能力：换成 Ollama 后，`web_search` 工具需要自备实现（mock / DuckDuckGo / SearXNG / 搜索 API 四选一）。

---

## 问题 1：如何检查当前环境是否满足 requirements.txt

### 结论：用 `pip install --dry-run`，`pip-compile` 语义不对

| 命令 | 实际语义 | 适合本需求吗 |
| --- | --- | --- |
| `pip-compile --dry-run requirements.txt` | pip-tools 的**全新依赖解析**（生成锁文件预览），不看当前环境已装了什么 | ❌ 语义不符 |
| `pip check` | 只检查已装包之间的版本冲突，不读 requirements.txt | ❌ 不读目标文件 |
| **`pip install --dry-run -r requirements.txt`** | 解析器对照**当前环境**逐条判断：已满足的打印 `Requirement already satisfied`，缺的汇总到 `Would install`，**不实际安装** | ✅ 正确工具（pip ≥ 22.2） |
| `uv pip install --dry-run -r requirements.txt` | 同上，更快；需先用 `-p`/激活指向目标环境 | ✅ 等价替代 |

实测（conda `ai-gpu` 环境，Python 3.12.12，2026-10-14）：

```text
Requirement already satisfied: openai>=1.12.0      (1.109.1)   # 要求 ≥1.12.0 ✓
Requirement already satisfied: requests>=2.31.0    (2.32.5)    ✓
Requirement already satisfied: python-dotenv>=1.0.0(1.2.1)     ✓
Requirement already satisfied: aiohttp>=3.9.0      (3.13.3)    # 可选依赖 ✓
Requirement already satisfied: rich>=13.7.0        (13.9.4)    # 可选依赖 ✓
Requirement already satisfied: tenacity>=8.2.3     (8.5.0)     # 可选依赖 ✓
Requirement already satisfied: httpx>=0.27,<1      (0.28.1)    # 开发依赖 ✓
Would install astroid black iniconfig isort mccabe mypy_extensions
             pathspec platformdirs pluggy pylint pytest pytokens tomlkit
```

`Would install` 列出的全部是 **dev 依赖**（pytest / black / pylint）及其传递依赖——跑实验不需要。

### 两点补充

- **import 实测**：主代码（main/agent/config/quickstart/examples.py）实际只 import 了 `openai`、`requests`、`python-dotenv` + 标准库。aiohttp/rich/tenacity 当前版本代码里没用到（README「开发计划（尚未实现）」里列的异步/重试功能），requirements.txt 把它们标为可选是准确的。
- **环境路径**：书推荐的是 `uv sync --locked --extra ch1`（在仓库根创建 `.venv`，目前**尚未创建过**）；requirements.txt 是迁移期兼容路径。当前 conda `ai-gpu` 环境直接可跑，无需安装任何东西。

---

## 问题 2：用本地 Ollama `qwen3.5-4b:q4` 跑实验的改造方案

### 为什么不能只改 .env（关键架构事实）

1. **搜索是 Moonshot 服务端专有能力，不是模型自带**。代码里搜索分两步，都走 Moonshot 专有的 Formula API：
   - `agent.py:169 _get_tools()` → `GET {base_url}/formulas/moonshot/web-search:latest/tools` 拉取工具声明；
   - `agent.py:232 _execute_formula()` → `POST {base_url}/formulas/.../fibers` 在 Moonshot 服务端执行搜索。
   Ollama 没有这两个端点：把 base_url 指到 Ollama 后，第一轮 `GET /formulas/...` 就会 404，循环直接以「搜索过程中出现错误」结束。
2. **`--provider` 没有 ollama 选项**（`main.py:151` 只有 `kimi` / `offline-demo`）。按书根 README 的规则，只有 CLI 明确列出 ollama 的实验才能用 `--provider ollama`。
3. **本实验不读 `OLLAMA_BASE_URL`**：`config.py` 只读 `MOONSHOT_API_KEY`/`KIMI_API_KEY`/`KIMI_BASE_URL`/`SEARCH_TIMEOUT`。根目录 `.env.example` 里的 Ollama 配置是给支持 `agentbook.providers` 注册表的其他章节实验用的，对本实验无效。

（注：根 `.env.example` 第 19 行实际写的是 `OLLAMA_BASE_URL=http://localhost:11434/v1`；实测本机 Ollama 服务运行在 **8000** 端口，方案按 8000 配置。）

### 可行性实测证据（2026-10-14，均为只读探测）

| 检查项 | 结果 |
| --- | --- |
| `GET localhost:8000/v1/models` | ✅ `qwen3.5-4b:q4` 在线 |
| 工具调用（带 `web_search` function 声明提问） | ✅ 返回 `finish_reason="tool_calls"`，`function.name="web_search"`，`arguments` 为合法 JSON（还自带了 `reasoning` 思考字段），prompt_tokens=288 |
| 模型能力 `/api/show` | ✅ `capabilities: [tools, thinking, completion]`；模型原生 context_length=262144 |
| **运行时上下文 `/api/ps`** | ⚠️ **生效 num_ctx 仅 4096**（远小于模型原生 256k） |

对 ReAct 循环其余环节的兼容性（读代码确认，无需改动）：`finish_reason` 判断（`tool_calls`/`stop`）通用 ✓；temperature 0.6 恰好是 Qwen3 thinking 模式官方推荐值 ✓；工具参数 JSON 非法时已有容错（`agent.py:439` 按空参数继续）✓。

### 改造方案（共 3 处，待确认后实施）

**① 新建 `chapter1/web-search-agent/.env`**（参考实验自带 `env.example` + 根 `.env.example` 格式）：

```env
KIMI_BASE_URL=http://localhost:8000/v1
MOONSHOT_API_KEY=ollama        # 占位值：OpenAI SDK 要求非空，Ollama 不校验
# SEARCH_TIMEOUT=180           # 可保留；本地 4b 模型推理快，180s 充裕
```

> 放实验目录而非仓库根：`load_dotenv()` 从 config.py 所在目录向上搜索，实验局部 `.env` 优先生效；且 `KIMI_BASE_URL` 指向 Ollama 后**不会污染**其他也读这个变量的章节实验（如 chapter1/context）。

**② `agent.py` 最小改造（2 个方法 + 1 个常量）**：

- `_get_tools()`（agent.py:169）：加 Ollama 分支——跳过 Formula tools 的 GET 请求，直接返回内置的 `web_search` function 声明（照 Moonshot 返回的 schema 手写一份即可）；
- `_execute_formula()`（agent.py:232）：加 Ollama 分支——改为调用**本地搜索实现**（这正是 README「开发计划」里说的 `search_impl` 扩展点思想：换搜索后端不用动 ReAct 主循环）；
- `max_tokens = 32768`（agent.py:161）：降到 2048～4096。32k 输出预算是为 kimi-k3 的长推理设计的，对 4b/q4 + 受限 num_ctx 只会挤占输入空间；
- （可选）思考内容字段映射：Ollama 返回 `reasoning` 字段，而 `agent.py:404` 用 `getattr(message, "reasoning_content", None)` 取思考内容——不映射的话 ReAct 轨迹会缺「💭 思考」步骤（OpenAI SDK 保留未知字段，取 `message.reasoning` 即可），不影响功能。

**③ 运行命令**：

```bash
cd ai-agent-book/chapter1/web-search-agent
python main.py "2024年诺贝尔物理学奖获得者是谁？" \
  --provider kimi --model qwen3.5-4b:q4 \
  --base-url http://localhost:8000/v1 --api-key ollama
# .env 配好后 --base-url/--api-key 可省略
```

### 已确认的决策（2026-10-14 与用户逐条确认）

1. **搜索后端 → A. mock 搜索**：先用固定样本结果跑通全链路，真后端（DuckDuckGo / SearXNG / 搜索 API）之后再议。
2. **改动方式 → 直接改 `agent.py`**：加 `SEARCH_BACKEND` 分支，默认值 `formula` 保持原路径行为完全不变。
3. **num_ctx → 16384**：需用户重启 Ollama 时设置 `OLLAMA_CONTEXT_LENGTH=16384`（见文末「待办」）。

## 实施记录（2026-10-14）

### 改动 1：`agent.py`（5 处，默认行为不变）

| 位置 | 改动 |
| --- | --- |
| `search_impl` 之后 | 新增 `MOCK_WEB_SEARCH_TOOL` 常量（内置 `web_search` function 声明）和 `run_mock_search()`（固定样本结果，带查询回显） |
| `__init__` | 新增 `self._search_backend = os.getenv("SEARCH_BACKEND", "formula")`；`max_tokens = 32768` 改为 `int(os.getenv("MAX_TOKENS", "32768"))`——默认值不变，可用环境变量覆盖 |
| `_get_tools()` | `SEARCH_BACKEND=mock` 时跳过 Formula GET，直接返回内置声明；用 `getattr(self, "_search_backend", "formula")` 防御式读取（测试用 `__new__` 构造实例、不经过 `__init__`，与上游 `using_openrouter` 的防御风格一致） |
| `_execute_formula()` | `SEARCH_BACKEND=mock` 时转调 `run_mock_search()` |
| `search_and_answer()` | 思考内容读取兼容两个字段：`reasoning_content`（Moonshot）优先，回落 `reasoning`（Ollama） |

### 改动 2：新建 `chapter1/web-search-agent/.env`

```env
KIMI_BASE_URL=http://localhost:8000/v1
MOONSHOT_API_KEY=ollama
SEARCH_BACKEND=mock
MAX_TOKENS=2048
```

### 验证结果

1. **冒烟测试全链路** ✅：`python main.py "月球 diameter 是多少公里？请搜索后回答" --model qwen3.5-4b:q4`，ReAct 轨迹完整走通四步——💭 思考（Ollama `reasoning` 字段映射生效）→ 🔧 调用 `web_search`（参数 `{"query": "月球直径多少公里"}` 正确）→ 👀 观察（mock 样本返回）→ ✅ 最终答案。**有趣观察**：模型识别出 mock 数据「并非真实联网搜索结果」后，第二轮决定不再继续搜索、改用自身参数知识作答，答案正确（3,474 公里）。证据 JSON：`docs/ai-agent-book/evidence/ollama_mock_smoke.json`。
2. **实验局部测试** ✅：`pytest tests/` 44 passed（含 3 个 Formula 路径回归测试）。
3. **仓库级测试** ✅：`tests/test_ch1_ch2_system_hint_tool_error_null.py` 3 passed（从仓库根运行）。
4. **排查记录**：曾出现 3 failed，经 `git stash` 对照定位为**既有测试路径问题**——从实验子目录运行仓库级测试时，`sys.path` 前置导致 `import agent` 命中 web-search-agent 的 `agent.py`（模块名冲突），与本次改动无关；从仓库根运行即通过。教训：仓库级测试应在 `ai-agent-book/` 根目录运行。

### 使用方式

```bash
cd ai-agent-book/chapter1/web-search-agent
python main.py "你的问题" --model qwen3.5-4b:q4
# .env 已配好端点与 mock 后端；--output result.json 可保存 ReAct 轨迹证据
```

### 设计辨析：`SEARCH_BACKEND=mock` 与「支持 Ollama」是什么关系

代码里**没有**名为 `ollama` 的分支，也不需要——每次运行由两条**正交链路**组成：

1. **模型链路**（谁在思考）：`agent.py __init__` 的 `OpenAI(api_key, base_url)` 走 OpenAI SDK，天然兼容任何 OpenAI 兼容端点。Ollama 暴露了 `/v1/chat/completions`，所以 `.env` 里 `KIMI_BASE_URL=http://localhost:8000/v1` + `--model qwen3.5-4b:q4` 就完成了模型切换，**这部分零代码改动**（变量名 `KIMI_BASE_URL` 是历史命名，本质是「chat 端点」）。
2. **工具链路**（web_search 怎么执行）：原代码的 `_get_tools()` / `_execute_formula()` 无论模型是谁都硬编码打 Moonshot 专有 URL。`SEARCH_BACKEND=mock` 分支做的是把**工具的声明与执行从 Moonshot 解耦**——声明本地给（`MOCK_WEB_SEARCH_TOOL`），结果本地造（`run_mock_search()`），ReAct 循环只依赖「模型会说 tool_calls」这个 OpenAI 通用协议。

组合矩阵：

| 模型链路 | 工具链路 | 效果 |
| --- | --- | --- |
| Moonshot key | `formula`（默认） | 原行为：kimi-k3 + 真实联网搜索 |
| Ollama | `mock` | 本次跑通：本地模型 + ReAct 全链路 |
| Ollama | 不设（=formula） | ❌ 工具仍打 Moonshot URL → 404，说明两个开关缺一不可 |
| Moonshot key | `mock` | 也成立：kimi-k3 + mock（正交性的直接证明） |

**为什么叫 `mock` 不叫 `ollama`**：Ollama 本身不提供搜索能力（本地模型无联网），不存在「ollama 搜索后端」这个东西。`SEARCH_BACKEND` 描述的是工具实现来源；将来接真搜索时新增 `searxng` / `duckduckgo` 等值，届时即得「Ollama 模型 + 真实联网搜索」的完整组合。对应书中的观点：Harness 的职责就是把这些维度拆开——模型可换、工具可换，ReAct 主循环（`search_and_answer`）一行不动。

### 待办（用户操作）

- [ ] 重启 Ollama 并把生效上下文调到 16384（当前 4096，多轮搜索可能静默截断）：
  - WSL/Linux：`OLLAMA_CONTEXT_LENGTH=16384 ollama serve`（或写入 systemd 环境变量）；Windows 服务则在系统环境变量加 `OLLAMA_CONTEXT_LENGTH=16384` 后重启 Ollama
  - 验证：`curl -s localhost:8000/api/ps` 中 `context_length` 应为 16384
- [ ] （远期）接入真实搜索后端替换 `run_mock_search`，观察真实 ReAct 多轮搜索行为

### 关键代码位置

| 位置 | 作用 |
| --- | --- |
| `agent.py:115` `__init__` | base_url/model/api_key 组装，`resolve_llm_backend` 决定 Moonshot/OpenRouter 兜底 |
| `agent.py:169` `_get_tools()` | GET Formula 工具声明（Ollama 分支需替换处 ①） |
| `agent.py:232` `_execute_formula()` | POST Fiber 执行搜索（Ollama 分支需替换处 ②） |
| `agent.py:317` `_chat()` | chat.completions 调用；kimi-k3 专属 `reasoning_effort=max`，qwen 不触发 |
| `agent.py:364` `search_and_answer()` | ReAct 主循环（换后端**不需要动**） |
| `agent.py:404` | `reasoning_content` 字段读取（Ollama 是 `reasoning`） |
| `config.py:46-67` | 实际读取的环境变量清单（不含 OLLAMA_BASE_URL） |
| `main.py:151` | `--provider` 选项定义（仅 kimi / offline-demo） |
