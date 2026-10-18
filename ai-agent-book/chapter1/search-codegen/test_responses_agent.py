import json

from agent import GPT5NativeAgent
from config import Config
from local_tools import execute_local_tool
from run_experiment_1_3 import (
    AMBIGUOUS_TASK,
    acceptance,
    independent_asean_reference,
    validate_asean,
    validate_clarification,
)

DASHSCOPE_URL = "https://dashscope-intl.aliyuncs.com/compatible-mode/v1"


def test_request_uses_official_responses_tool_shapes():
    agent = GPT5NativeAgent("key")
    request = agent._build_responses_request(
        "task", reasoning_effort="max", verbosity="high"
    )
    assert request["reasoning"] == {"effort": "max"}
    assert request["text"] == {"verbosity": "high"}
    assert request["tools"] == [
        {"type": "web_search", "search_context_size": "medium"},
        {
            "type": "code_interpreter",
            "container": {"type": "auto", "memory_limit": "4g"},
        },
    ]


def test_dashscope_request_uses_hosted_tool_shapes_and_streaming():
    agent = GPT5NativeAgent("key", base_url=DASHSCOPE_URL, model="qwen3.7-plus")
    assert agent.provider == "dashscope"
    request = agent._build_responses_request(
        "task", reasoning_effort="high", verbosity="high"
    )
    # DashScope runs thinking natively: no reasoning.effort or text.verbosity,
    # and streaming is mandatory because its gateway drops idle connections.
    assert "reasoning" not in request
    assert "text" not in request
    assert request["stream"] is True
    assert request["tools"] == [
        {"type": "web_search"},
        {"type": "code_interpreter"},
    ]


def test_config_resolves_dashscope_backend():
    key, base_url, model = Config.resolve("dashscope")
    assert base_url == DASHSCOPE_URL
    assert model == Config.DASHSCOPE_MODEL
    assert isinstance(key, str)


def test_dashscope_citations_from_web_search_sources():
    agent = GPT5NativeAgent("key", base_url=DASHSCOPE_URL, model="qwen3.7-plus")
    response = {
        "output": [
            {
                "type": "web_search_call",
                "status": "completed",
                "action": {
                    "query": "ASEAN capitals",
                    "sources": [
                        {"type": "url", "url": "https://asean.test/one"},
                        {"type": "url", "url": "https://asean.test/two"},
                    ],
                },
            }
        ]
    }
    citations = agent._citations(response)
    assert citations == [
        {"type": "url_citation", "url": "https://asean.test/one"},
        {"type": "url_citation", "url": "https://asean.test/two"},
    ]

def test_dashscope_citations_from_web_search_string_url_sources():
    agent = GPT5NativeAgent("key", base_url=DASHSCOPE_URL, model="qwen3.7-plus")
    response = {
        "output": [
            {
                "type": "web_search_call",
                "status": "completed",
                "action": {
                    "query": "ASEAN capitals",
                    "sources": [
                        "https://asean.test/one",
                        "https://asean.test/two",
                    ],
                },
            }
        ]
    }
    citations = agent._citations(response)
    assert citations == [
        {"type": "url_citation", "url": "https://asean.test/one"},
        {"type": "url_citation", "url": "https://asean.test/two"},
    ]


def test_independent_asean_reference_is_kuala_lumpur_singapore():
    reference = independent_asean_reference()
    assert reference["pair"] == ["Kuala Lumpur", "Singapore"]
    assert reference["pair_count"] == 45
    assert 250 < reference["distance_km"] < 400


def test_asean_acceptance_requires_both_completed_hosted_tools():
    result = {
        "success": True,
        "requested_model": "gpt-5.6-sol",
        "model": "gpt-5.6-sol",
        "response": "Singapore and Kuala Lumpur are 316 km apart.",
        "output_items": [
            {"type": "web_search_call", "status": "completed"},
            {"type": "code_interpreter_call", "status": "completed"},
        ],
        "citations": [
            {"type": "url_citation", "url": "https://one.test"},
            {"type": "url_citation", "url": "https://two.test"},
        ],
    }
    assert validate_asean(result)["passed"] is True
    result["output_items"] = result["output_items"][:1]
    assert validate_asean(result)["passed"] is False


def test_asean_acceptance_rejects_model_substitution():
    result = {
        "success": True,
        "requested_model": "qwen3.7-plus",
        "model": "qwen3.7-flash",
        "response": "Singapore and Kuala Lumpur are 316 km apart.",
        "output_items": [
            {"type": "web_search_call", "status": "completed"},
            {"type": "code_interpreter_call", "status": "completed"},
        ],
        "citations": [
            {"type": "url_citation", "url": "https://one.test"},
            {"type": "url_citation", "url": "https://two.test"},
        ],
    }
    assert validate_asean(result)["checks"]["model_identity_exact"] is False
    assert validate_asean(result)["passed"] is False


def test_clarification_requires_no_tools_then_linked_tool_run():
    first = {
        "success": True,
        "response": "Which source and indicators do you prefer?",
        "tool_calls": [],
        "response_id": "resp_1",
    }
    second = {
        "success": True,
        "request": {"previous_response_id": "resp_1"},
        "response": "MA7, MA20, RSI14 and MACD(12,26,9) were computed.",
        "output_items": [
            {"type": "web_search_call", "status": "completed"},
            {"type": "code_interpreter_call", "status": "completed"},
        ],
        "citations": [{"type": "url_citation"}],
    }
    assert validate_clarification(first, second)["passed"] is True
    second_without_indicators = dict(second, response="Here is the report.")
    assert validate_clarification(first, second_without_indicators)["passed"] is False


def test_acceptance_is_multi_provider_not_openai_gated():
    passing_run = {
        "backend": "dashscope",
        "started": True,
        "requested_model": "qwen3.7-plus",
        "asean_validation": {"passed": True},
        "clarification": {"validation": {"passed": True}},
    }
    blocked_openai = {"backend": "openai", "started": True,
                      "requested_model": "gpt-5.6-sol",
                      "asean_validation": {"passed": False},
                      "clarification": {"validation": {"passed": False}}}
    result = acceptance([blocked_openai, passing_run])
    assert result["passed"] is True
    assert result["acceptance_backend"] == "dashscope"
    # OpenAI keeps priority when both pass.
    blocked_openai["asean_validation"] = {"passed": True}
    blocked_openai["clarification"] = {"validation": {"passed": True}}
    result = acceptance([blocked_openai, passing_run])
    assert result["acceptance_backend"] == "openai"
    # OpenRouter alone can never close the experiment.
    openrouter_run = dict(passing_run, backend="openrouter")
    result = acceptance([openrouter_run])
    assert result["passed"] is False
    assert result["openrouter_is_diagnostic_not_acceptance"] is True


# ---- Local Ollama path (offline shape/semantic tests) ----


def test_ollama_provider_inferred_from_localhost():
    agent = GPT5NativeAgent(
        "ollama", base_url="http://localhost:8000/v1", model="qwen3.5-4b:q4"
    )
    assert agent.provider == "ollama"


def test_ollama_request_uses_local_function_tools():
    agent = GPT5NativeAgent(
        "ollama",
        base_url="http://localhost:8000/v1",
        model="qwen3.5-4b:q4",
        provider="ollama",
    )
    request = agent._build_responses_request(
        "task", use_tools=True, reasoning_effort="high", verbosity="high"
    )
    # Ollama runs thinking natively and keeps no server state: none of the
    # hosted-path knobs may leak into the request.
    for absent in ("reasoning", "text", "store", "background", "tool_choice", "stream"):
        assert absent not in request
    tools = request["tools"]
    assert [tool["name"] for tool in tools] == ["web_search", "run_python"]
    assert all(tool["type"] == "function" for tool in tools)


def test_hosted_request_shapes_are_unchanged():
    openai_agent = GPT5NativeAgent("key")
    assert openai_agent.provider == "openai"
    request = openai_agent._build_responses_request("task")
    assert request["reasoning"] == {"effort": "low"}
    assert request["store"] is True
    assert request["tools"][0]["type"] == "web_search"


def test_local_tools_mock_routing_and_sandbox():
    btc = json.loads(execute_local_tool("web_search", '{"query": "比特币 bitcoin 走势"}'))
    assert btc["source"] == "local_mock_dataset"
    assert btc["series"]["sessions"] == 60

    asean = json.loads(
        execute_local_tool("web_search", '{"query": "ASEAN capitals coordinates"}')
    )
    assert len(asean["capitals"]) == 10

    miss = json.loads(execute_local_tool("web_search", '{"query": "weather in Paris"}'))
    assert miss["result"] is None

    bad_args = json.loads(execute_local_tool("run_python", "not-json"))
    assert "error" in bad_args

    run = json.loads(execute_local_tool("run_python", '{"code": "print(21*2)"}'))
    assert run["exit_code"] == 0
    assert run["stdout"].strip() == "42"

    unknown = json.loads(execute_local_tool("nope", "{}"))
    assert "unknown tool" in unknown["error"]


def test_asean_acceptance_local_degraded_uses_round_trips():
    result = {
        "success": True,
        "provider": "ollama",
        "requested_model": "qwen3.5-4b:q4",
        "model": "qwen3.5-4b:q4",
        "response": "Kuala Lumpur and Singapore: 316.35 km",
        "tool_round_trips": [
            {"name": "web_search", "output": '{"capitals": "..."}'},
            {"name": "run_python", "output": '{"stdout": "316.35"}'},
        ],
    }
    validated = validate_asean(result)
    assert validated["mode"] == "local_degraded"
    assert validated["passed"] is True

    # Losing either local tool receipt breaks acceptance, as with hosted runs.
    result["tool_round_trips"] = result["tool_round_trips"][:1]
    assert validate_asean(result)["passed"] is False


def test_clarification_local_continuation_via_input_history():
    first = {
        "success": True,
        "provider": "ollama",
        "response": "您希望使用哪个数据源？需要分析哪些技术指标？",
        "tool_calls": [],
        "request": {"input": AMBIGUOUS_TASK},
    }
    second = {
        "success": True,
        "provider": "ollama",
        "request": {
            "input": [
                {"type": "message", "role": "user", "content": AMBIGUOUS_TASK},
                {"type": "message", "role": "assistant", "content": first["response"]},
            ]
        },
        "response": "MA7/MA20、RSI14 与 MACD 均已通过 run_python 计算。",
        "tool_round_trips": [
            {"name": "web_search", "output": '{"series": "..."}'},
            {"name": "run_python", "output": '{"stdout": "..."}'},
        ],
    }
    validated = validate_clarification(first, second)
    assert validated["mode"] == "local_degraded"
    assert validated["checks"]["continuation_used_input_history"] is True
    assert validated["passed"] is True
