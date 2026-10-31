# 17 · chapter2/prompt-engineering 本地复现改造：从 Ollama 到智谱 GLM

> 对应实验：`ai-agent-book/chapter2/prompt-engineering`（实验 2-4 ★★：提示工程的消融实验，τ-bench airline）
> 日期：2026-10-30 ~ 10-31
> 结论先行：**litellm 路由下零代码切换云端 OpenAI 兼容端点；本地 Ollama（4B/35B-A3B）链路全通但能力低于 τ-bench 门槛，最终采用智谱 GLM Coding Plan（glm-5.3-flash）跑通六组消融。**

## 1. 实验是否依赖外部 API？——是，但可路由

所有 LLM 调用（agent 侧 + 用户模拟器侧）统一走 **litellm**，与 chapter1/context 的 agentbook 注册表是完全不同的抽象层：

| 调用方 | 文件 | 方式 |
| --- | --- | --- |
| Agent（tool-calling，15 工具） | `ablation_agent.py:186` | `litellm.completion(custom_llm_provider=provider, tools=..., max_tokens=4096, seed=...)` |
| 用户模拟器 | `tau_bench/envs/user.py:74` | `litellm.completion(custom_llm_provider=provider, max_tokens=1024, seed=...)` |
| provider 解析 | `run_ablation.py:209-233` | 裸 id → `openai`；带 `/` → `openrouter`；检测到 `OPENROUTER_API_KEY` 会强制改写 provider |

litellm 原生支持 `ollama`（provider_list 里有），因此理论零代码即可路由到本地 Ollama；同理，`openai` provider + `OPENAI_API_BASE` 覆盖即可路由到任何 OpenAI 兼容云端。

## 2. 概念辨析：Ollama / vLLM / litellm 不在一个层次

- **Ollama、vLLM**：本地推理服务（server），真正加载权重做生成，占 GPU。Ollama 暴露原生 `/api/chat` + OpenAI 兼容 `/v1` 两套端点；vLLM 是同类替代（高吞吐生产场景）。
- **litellm**：Python 客户端库/路由层，不跑模型、不占 GPU，只做"统一 OpenAI 格式 → 各家 API 格式"的翻译。装它只是因为上游代码 `from litellm import completion`。

本机 Ollama 服务运行在 `:8000`（非默认 11434），因此 `OLLAMA_API_BASE=http://localhost:8000` 必须显式覆盖。

## 3. 改造过程（三阶段）

### 阶段一：本地 Ollama 路线（方案 A：litellm ollama provider）

改动最小集：

1. `pip install litellm`（ai-gpu 环境原本没有；连带升级 `mcp` 修复 litellm 1.106 tools 路径的 `CacheableResult` ImportError）
2. `.env`：`OLLAMA_API_BASE=http://localhost:8000`（**勿设 OPENAI_API_KEY / OPENROUTER_API_KEY**，否则 parse_args 的回退逻辑改写 provider）
3. 运行：`--model <模型> --model-provider ollama --user-model <模型> --user-model-provider ollama`

预处理验证（单请求冒烟）发现并解决：

- **qwen3.5-4b 默认把思考过程写进 content**（"回复一个字"耗 213 tokens）。`/no_think` 软开关会污染 wiki 消融材料，弃用；**`think=False` 是正解**——采样层开关不进 prompt，litellm 原生透传给 Ollama。
- 为此给 `ablation_agent.py` 与 `tau_bench/envs/user.py` 各加了 2 行条件化代码（仅 `provider=="ollama"` 时附加 `think=False`，云端路径零影响）——这是全程唯一的代码改动。

### 阶段二：能力门槛的发现（本地模型全线失败）

| 冒烟 | 模型 | 链路 | 结果 | 失败模式 |
| --- | --- | --- | --- | --- |
| 1 | qwen3.5-4b:q4 | ✓ 全通 | 0/2 | task0：`get_user_details→list_all_airports→search_direct_flight` 三连死循环 30 步耗尽；task1：第 2 步即 `transfer_to_human_agents` 逃避 |
| 2 | qwen3.6-35b-a3b:q4 | ✓ 全通 | 0/2 | 两任务均 30 步全工具调用、**0 次 respond**、30 次工具错误：用记忆中的 id 查库 → not found → 再查 |

**关键发现——τ-bench 训练数据污染的活案例**：35B 直接套用 `sara_doe_496`（原版 τ-bench airline 标志性用户 id）查询，而这个 vendored 版本的数据库被书作者**刻意替换过**（`mia_li_3668` 等）。模型没有执行正确流程（respond 询问用户 id，模拟器 instruction 里有正确 id），而是把训练时"背下来"的原版 id 直接用上——评估基准已被训练数据污染的实证，呼应第 7 章评估主题。

**判定**：τ-bench 是给顶级模型设计的困难基准（书中 kimi-k3 baseline 仅 7/10，GPT-4o 原版 ~50%）。本机最大 35B-A3B（MoE 激活 3B）低于能力地板，reward 恒 0，消融无区分度。

### 阶段三：智谱 GLM Coding Plan 路线（最终采用）

用户持有 GLM Coding Plan 订阅。改造要点：

1. **key 复用**：不新增任何凭据——本机 Claude Code 连智谱的 `ANTHROPIC_AUTH_TOKEN` 就是同一个智谱 API key，`.env` 里用 dotenv 变量插值引用（`OPENAI_API_KEY=${ANTHROPIC_AUTH_TOKEN}`），key 不落盘；并给 `.gitignore` 补上 `.env`（上游漏了）。
2. **端点必须是 coding 专用**：`https://open.bigmodel.cn/api/coding/paas/v4`（订阅额度）；普通 `/api/paas/v4` 走按量计费，未充值报"余额不足或无可用资源包"。
3. 运行：`--model glm-5.3-flash --model-provider openai --user-model glm-5.3-flash --user-model-provider openai`。结果元数据里 provider 记为 `openai`（协议语义），实为智谱，由 `experiment_protocol.local.json` 的 provider 字段如实注明。
4. GLM-5.3-Flash 的思考内容在 `reasoning_content` 独立字段、短小（单次 51 tokens），不污染 content，1024 max_tokens 够用，无需关思考。

**GLM 冒烟（2 任务）**：task0 **reward=1.0**（11 步、4 工具、0 错误，标准流程：问 user ID → 搜航班 → 列选项 → 预订）；task1 reward=0（复杂改签，轨迹合理）。与 kimi-k3 baseline 同档——**过了能力门槛，具备跑消融的前提**。

## 4. 六组消融运行（本地改编协议）

- 协议：`experiment_protocol.local.json`（基于冻结协议改编：model=glm-5.3-flash、temperature 0、seed 20261030、task_ids [0,1,2]；`--protocol` 参数零代码绕过 kimi-k3 硬校验）
- 命令见 `.env` 注释；`--all` 六组 = baseline / tone_trump / tone_casual / wiki_random / no_tool_desc / all_ablations
- 产物：`runs/exp2-4-glm-5.3-flash-20261030-v1/`（manifest.json 哈希 + 各组 checkpoint + comparison.json）

### 4.1 成功率对比表（2026-10-31 实测）

```
Experiment                        Success Rate      Tasks        Relative
----------------------------------------------------------------------
baseline                         66.7%         2/  3      100.0%  ⭐
tone_trump                       66.7%         2/  3      100.0%
tone_casual                      66.7%         2/  3      100.0%
wiki_random                      66.7%         2/  3      100.0%
no_tool_desc                     33.3%         1/  3       50.0%
all_ablations                    33.3%         1/  3       50.0%
----------------------------------------------------------------------
```

### 4.2 各组逐任务明细

| arm | reward (task0/1/2) | 失败任务 | 步数 | 工具调用 | 工具错误 |
| --- | --- | --- | --- | --- | --- |
| baseline | 1.0 / 0.0 / 1.0 | [1] | 12/20/28 | 5/10/24 | 0/0/0 |
| tone_trump | 1.0 / 1.0 / 0.0 | [2] | 10/10/30 | 4/5/25 | 0/0/0 |
| tone_casual | 1.0 / 1.0 / 0.0 | [2] | 10/9/29 | 4/5/25 | 0/0/0 |
| wiki_random | 1.0 / 0.0 / 1.0 | [1] | 11/20/29 | 4/9/25 | 0/0/0 |
| no_tool_desc | 1.0 / 0.0 / 0.0 | [1,2] | 12/17/30 | 6/9/25 | 0/0/0 |
| all_ablations | 1.0 / 0.0 / 0.0 | [1,2] | 10/18/30 | 4/10/20 | 0/0/0 |

### 4.3 与书中预期排序的对照

| 书中预期 | 本次实测 | 判定 |
| --- | --- | --- |
| 1. Baseline 最佳 | baseline 并列第一（66.7%） | ✓ |
| 2. 语气变化对成功率影响不大 | trump/casual 与 baseline 完全一致 | ✓（本样本内零影响） |
| 3. Wiki 随机化严重损害 | 本次无差异（66.7% 持平） | ✗ 未复现（n=3 信号不足；书中 Kimi K3 完整运行同样出现 wiki_random 8/10 > baseline 7/10） |
| 4. 无工具描述 → 大量错误 | 相对下降 50%（唯二只剩 1/3 的组之一） | ✓ 方向一致 |
| 5. 组合消融最差 | 并列最差（33.3%） | ✓ |

### 4.4 值得注意的细节

1. **task2 是难度天花板**：6 组里 5 组失败于 task2（28–30 步、20–25 次工具调用的大任务），唯一通过它的是 wiki_random 组。任务难度主导了小样本方差。
2. **六组 tool_error 全 0**：GLM-5.3-flash 的工具调用格式极其干净。no_tool_desc 组的成功率损失**不是**以"可见的参数错误"形式出现（τ-bench 的 tool_errors 只计 observation 以 Error: 开头者），而是业务决策层面的退化——现代模型对"没有描述的工具"有从 schema 推断的预训练韧性，但精确选择仍依赖描述。
3. **与上游 Kimi K3 完整运行的差异**：那次 no_tool_desc 反而 9/10（高于 baseline 7/10）。两个运行共同说明：**no_tool_desc 的效应强烈依赖模型与任务集**，本书历史"30–80% 下滑"的点估计不可外推——这正是 ledger 中"historical percentages not reproduced"限定语的含义。
4. n=3 的本运行是**链路验证 + 方向性演示**，不可做统计结论；要量化需扩到 10 任务 × 多 seed。

## 5. 经验清单

1. **多 provider 实验的最小改造路径**：先读调用层抽象（agentbook vs litellm vs 裸 SDK），再找对应的环境变量覆盖点，通常零代码。
2. **思考型本地模型必须关思考**：`think=False`（Ollama 采样层）优于 prompt 软开关（污染实验材料）。
3. **评估基准的能力门槛**：链路验证通过 ≠ 实验可复现；模型能力低于门槛时 reward 恒 0，先冒烟再投入完整套，避免浪费小时级算力。
4. **τ-bench 已被训练数据污染**：小模型会直接复述记忆中的原版实体 id；换库/换实体的 vendored 版本反而成了检验"真流程 vs 背答案"的试金石。
5. **`.gitignore` 保护 `.env`**：上游仓库不一定覆盖；key 用 dotenv 插值引用环境变量而非落盘。
