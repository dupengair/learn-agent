"""Exact GPT-5.6 Responses API agent for Experiment 1-3.

The previous companion sent Responses-style hosted tools to Chat Completions
through a proxy and then reported an empty ``tool_calls`` list.  This module
uses the actual ``/v1/responses`` protocol and preserves its typed output items
(``web_search_call``, ``code_interpreter_call``, messages, and citations).

Local Ollama path (``provider="ollama"``): Ollama speaks the same protocol but
has no server behind the hosted tool types, so ``web_search`` /
``code_interpreter`` degrade to function tools that this process executes
between model turns (see ``local_tools.py``).  Everything else — typed output
items, multi-turn linking, clarification-before-tools — is preserved.  Ollama
0.35.0 rejects ``previous_response_id`` plus a ``function_call_output`` array
with HTTP 500, so the local loop carries state as a plain input-items array
instead of server-side state.
"""

from __future__ import annotations

import json
import logging
import time
from typing import Any, Dict, List, Literal, Optional

import requests

# Repo-level tests load this file standalone via importlib, without the
# experiment directory on sys.path — same fallback pattern as config.py.
try:
    from local_tools import execute_local_tool
except ImportError:  # pragma: no cover - standalone module loading
    import sys as _sys

    _sys.path.insert(
        0, str(__import__("pathlib").Path(__file__).resolve().parent)
    )
    from local_tools import execute_local_tool

logger = logging.getLogger(__name__)

# Simplified function_call shape re-fed into the input array; matches the
# form verified against Ollama's /v1/responses in this repository's probes.
_FUNCTION_CALL_KEYS = ("type", "id", "call_id", "name", "arguments", "status")


class GPT5NativeAgent:
    """GPT-5.6 Sol with OpenAI-hosted web search and Python tools.

    With ``provider="ollama"`` the same class runs the local-tool variant
    against an Ollama Responses endpoint (no hosted tools, no server state).
    """

    def __init__(
        self,
        api_key: str,
        base_url: str = "https://api.openai.com/v1",
        model: str = "gpt-5.6-sol",
        provider: Optional[str] = None,
        max_tool_rounds: int = 6,
    ):
        if not api_key:
            raise ValueError("An API key is required")
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.provider = provider or (
            "openai" if self.base_url == "https://api.openai.com/v1" else
            "openrouter" if "openrouter.ai" in self.base_url else
            "dashscope" if "dashscope" in self.base_url else
            "ollama" if "localhost" in self.base_url or "127.0.0.1" in self.base_url else
            "custom"
        )
        self.max_tool_rounds = max(1, int(max_tool_rounds))
        self.conversation_history: List[Dict[str, Any]] = []
        self.system_prompt = self._create_system_prompt()
        self.previous_response_id: Optional[str] = None
        self.api_turns: List[Dict[str, Any]] = []
        # Ollama path: full input-items history (user/assistant messages,
        # function_call + function_call_output pairs) carried across calls.
        self.local_items: List[Dict[str, Any]] = []

    def _create_system_prompt(self) -> str:
        if self.provider == "ollama":
            return """You are a deep-research assistant. 你是一名深度研究助手。

Hard rule / 硬性规则: when the user's research request leaves material
preferences ambiguous — for example which data source to use or which
technical indicators to compute — ask a concise clarifying question FIRST,
and do NOT call any tool until the user answers.
当用户的研究请求没有明确数据来源或具体分析指标时，必须先向用户提问澄清，
在用户回答之前不要调用任何工具。

After clarification: gather facts with the web_search function tool, and run
ALL quantitative work through the run_python function tool — never do mental
arithmetic, and never claim code ran unless a run_python call actually
completed. Search results come from a small bundled sample dataset, so say so
when reporting sources.
澄清之后：用 web_search 工具获取事实；所有定量计算必须通过 run_python 工具
实际执行，不得口算或声称运行了代码。搜索结果来自本地样本数据集，报告来源时
如实说明。"""

        return """You are a deep-research assistant. 你是一名深度研究助手。

Hard rule / 硬性规则: when the user's research request leaves material
preferences ambiguous — for example which data source to use or which
technical indicators to compute — ask a concise clarifying question FIRST
(for example “您偏好使用哪个数据源？需要分析哪些技术指标？”), and do NOT
call any tool until the user answers.
当用户的研究请求没有明确数据来源或具体分析指标时，必须先向用户提问澄清，
在用户回答之前不要调用任何工具。

After clarification, use hosted web search for current facts and cite
sources, and use the hosted Python/code-interpreter tool for quantitative
analysis; do not claim a calculation was run unless the response contains a
completed code_interpreter_call.
澄清之后：使用 web_search 获取最新事实并引用来源链接；所有定量计算必须通过
code_interpreter 实际执行，不得口算或声称运行了代码。"""

    def _tools(self) -> List[Dict[str, Any]]:
        if self.provider == "ollama":
            # Ollama has no hosted tools: the same capabilities become
            # function tools executed locally between model turns.
            return [
                {
                    "type": "function",
                    "name": "web_search",
                    "description": (
                        "Search the bundled local sample dataset (ASEAN "
                        "capitals and coordinates; synthetic BTC/USD daily "
                        "closes). Returns JSON."
                    ),
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "query": {
                                "type": "string",
                                "description": "Search keywords",
                            }
                        },
                        "required": ["query"],
                    },
                },
                {
                    "type": "function",
                    "name": "run_python",
                    "description": (
                        "Run multi-line Python 3 locally and get "
                        "exit_code/stdout/stderr as JSON."
                    ),
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "code": {
                                "type": "string",
                                "description": "Python source code to execute",
                            }
                        },
                        "required": ["code"],
                    },
                },
            ]
        if self.provider == "dashscope":
            # Exact structures from the Alibaba Model Studio Responses API guides.
            return [{"type": "web_search"}, {"type": "code_interpreter"}]
        # Exact structures from the official OpenAI Responses API guides.
        return [
            {"type": "web_search", "search_context_size": "medium"},
            {
                "type": "code_interpreter",
                "container": {"type": "auto", "memory_limit": "4g"},
            },
        ]

    def _build_responses_request(
        self,
        input_text: Any,
        *,
        use_tools: bool = True,
        tool_choice: Literal["auto", "none", "required"] = "auto",
        reasoning_effort: str = "low",
        verbosity: Optional[str] = None,
        max_output_tokens: Optional[int] = None,
        background: bool = False,
    ) -> Dict[str, Any]:
        if self.provider not in ("dashscope", "ollama"):
            if reasoning_effort not in {"none", "low", "medium", "high", "xhigh", "max"}:
                raise ValueError("Unsupported GPT-5.6 reasoning effort")
            if verbosity not in {None, "low", "medium", "high"}:
                raise ValueError("verbosity must be low, medium, or high")
        request: Dict[str, Any] = {
            "model": self.model,
            "instructions": self.system_prompt,
            "input": input_text,
        }
        if self.provider == "ollama":
            # Ollama runs qwen thinking natively and has no reasoning.effort /
            # text.verbosity / store / background knobs. It also rejects
            # previous_response_id combined with a function_call_output array
            # (HTTP 500), so the local loop is stateless and never sends it.
            pass
        elif self.provider == "dashscope":
            # DashScope runs thinking natively and has no reasoning.effort or
            # text.verbosity knobs; its gateway also drops non-streaming
            # requests that stay silent for ~60s, so streaming is mandatory.
            request["stream"] = True
        else:
            request["reasoning"] = {"effort": reasoning_effort}
            request["background"] = background
            request["store"] = True
            if verbosity:
                request["text"] = {"verbosity": verbosity}
        if max_output_tokens:
            request["max_output_tokens"] = max_output_tokens
        if use_tools:
            request["tools"] = self._tools()
            if self.provider != "ollama":
                request["tool_choice"] = tool_choice
        if self.previous_response_id:
            request["previous_response_id"] = self.previous_response_id
        return request

    @staticmethod
    def _output_text(response: Dict[str, Any]) -> str:
        if not isinstance(response, dict):
            return ""
        chunks: List[str] = []
        for item in response.get("output") or []:
            if not isinstance(item, dict) or item.get("type") != "message":
                continue
            for content in item.get("content") or []:
                if isinstance(content, dict) and content.get("type") == "output_text" and content.get("text"):
                    chunks.append(content["text"])
        return "\n".join(chunks).strip()

    @staticmethod
    def _tool_items(response: Dict[str, Any]) -> List[Dict[str, Any]]:
        if not isinstance(response, dict):
            return []
        return [
            item
            for item in response.get("output") or []
            if isinstance(item, dict)
            and item.get("type") in {
                "web_search_call",
                "code_interpreter_call",
                "hosted_tool_call",
            }
        ]

    @staticmethod
    def _citations(response: Dict[str, Any]) -> List[Dict[str, Any]]:
        citations = []
        if not isinstance(response, dict):
            return citations
        for item in response.get("output") or []:
            if not isinstance(item, dict):
                continue
            for content in item.get("content") or []:
                if not isinstance(content, dict):
                    continue
                for annotation in content.get("annotations") or []:
                    if isinstance(annotation, dict) and annotation.get("type") in {
                        "url_citation",
                        "container_file_citation",
                    }:
                        citations.append(annotation)
            # DashScope reports sources on the web_search_call item itself
            # instead of url_citation annotations; normalize them here.
            if item.get("type") == "web_search_call":
                action = item.get("action")
                if isinstance(action, dict):
                    for source in action.get("sources") or []:
                        url = source if isinstance(source, str) else (source.get("url") if isinstance(source, dict) else None)
                        if url:
                            citations.append(
                                {"type": "url_citation", "url": url}
                            )
        return citations

    def _post_responses(
        self, request: Dict[str, Any]
    ) -> tuple[int, Dict[str, Any], Optional[Dict[str, int]]]:
        """Send one Responses request and return (status, body, stream_events).

        DashScope requires streaming; the final ``response.completed`` event
        carries the same response object the non-streaming API returns, so both
        paths converge on an identical shape.
        """
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }
        if not request.get("stream"):
            http_response = requests.post(
                f"{self.base_url}/responses",
                headers=headers,
                json=request,
                timeout=900,
            )
            try:
                return http_response.status_code, http_response.json(), None
            except ValueError:
                return http_response.status_code, {"raw_text": http_response.text}, None

        event_counts: Dict[str, int] = {}
        final_response: Optional[Dict[str, Any]] = None
        with requests.post(
            f"{self.base_url}/responses",
            headers=headers,
            json=request,
            stream=True,
            timeout=900,
        ) as http_response:
            status_code = http_response.status_code
            if not http_response.ok:
                return status_code, {"raw_text": http_response.text}, event_counts
            for line in http_response.iter_lines(decode_unicode=True):
                if not line or not line.startswith("data:"):
                    continue
                data = line[len("data:"):].strip()
                if data == "[DONE]":
                    break
                try:
                    event = json.loads(data)
                except ValueError:
                    continue
                event_type = event.get("type") or "unknown"
                event_counts[event_type] = event_counts.get(event_type, 0) + 1
                if event_type in {"response.completed", "response.failed"}:
                    final_response = event.get("response")
        if final_response is None:
            return status_code, {"error": {"type": "stream_incomplete",
                                           "message": "stream ended without response.completed"}}, event_counts
        return status_code, final_response, event_counts

    @staticmethod
    def _function_calls(response: Dict[str, Any]) -> List[Dict[str, Any]]:
        if not isinstance(response, dict):
            return []
        return [
            item
            for item in response.get("output") or []
            if isinstance(item, dict) and item.get("type") == "function_call"
        ]

    def _process_request_with_local_tools(
        self,
        user_request: str,
        *,
        use_tools: bool = True,
        temperature: float = 0.3,
        max_tokens: Optional[int] = None,
        reasoning_effort: str = "low",
        verbosity: Optional[str] = None,
        background: bool = False,
    ) -> Dict[str, Any]:
        """Ollama variant: hosted tools become a local execute-and-resume loop.

        Request history is a plain input-items array (the class docstring
        explains why previous_response_id is unusable here). Each model turn
        that emits function_call items gets them executed by
        ``local_tools.execute_local_tool`` and their results appended as
        ``function_call_output`` items, until a plain message arrives or the
        round budget is spent. ``temperature`` stays unsent, as everywhere
        else: reasoning models ignore it anyway.
        """
        started = time.monotonic()
        items: List[Dict[str, Any]] = list(self.local_items)
        items.append({"type": "message", "role": "user", "content": user_request})

        all_output_items: List[Dict[str, Any]] = []
        tool_calls: List[Dict[str, Any]] = []
        round_trips: List[Dict[str, Any]] = []
        last_request: Dict[str, Any] = {}
        final_response: Optional[Dict[str, Any]] = None
        error: Optional[Dict[str, Any]] = None

        for round_index in range(self.max_tool_rounds + 1):
            last_request = self._build_responses_request(
                items,
                use_tools=use_tools,
                reasoning_effort=reasoning_effort,
                verbosity=verbosity,
                max_output_tokens=max_tokens,
                background=background,
            )
            turn_started = time.monotonic()
            status_code, response, _stream_events = self._post_responses(last_request)
            turn_elapsed = round(time.monotonic() - turn_started, 6)
            self.api_turns.append(
                {
                    "request": json.loads(json.dumps(last_request, ensure_ascii=False)),
                    "http_status": status_code,
                    "response": response,
                    "elapsed_seconds": turn_elapsed,
                }
            )
            if (
                not isinstance(response, dict)
                or status_code >= 400
                or response.get("error")
            ):
                error = (response.get("error") if isinstance(response, dict) else None) or {
                    "type": "http_error",
                    "message": (response.get("raw_text") if isinstance(response, dict) else None)
                    or (json.dumps(response)[:500] if response is not None else "Empty response"),
                }
                break

            all_output_items.extend(
                item
                for item in response.get("output") or []
                if isinstance(item, dict)
            )
            calls = self._function_calls(response)
            tool_calls.extend(calls)

            if not calls:
                final_response = response
                break
            if round_index == self.max_tool_rounds:
                error = {
                    "type": "max_tool_rounds",
                    "message": (
                        f"model kept calling tools through round "
                        f"{self.max_tool_rounds + 1}; giving up"
                    ),
                }
                final_response = response
                break

            # Execute every call of this round, then feed results back.
            for call in calls:
                output = execute_local_tool(
                    str(call.get("name", "")), str(call.get("arguments") or "")
                )
                round_trips.append(
                    {
                        "call_id": call.get("call_id"),
                        "name": call.get("name"),
                        "arguments": call.get("arguments"),
                        "output": output,
                    }
                )
                items.append({k: call[k] for k in _FUNCTION_CALL_KEYS if k in call})
                items.append(
                    {
                        "type": "function_call_output",
                        "call_id": call.get("call_id"),
                        "output": output,
                    }
                )

        elapsed = round(time.monotonic() - started, 6)
        text = self._output_text(final_response or {})
        success = (
            final_response is not None
            and error is None
            and final_response.get("status") == "completed"
            and bool(text)
        )
        if success:
            # Extend the carry-over history with this turn's answer; failed
            # turns leave self.local_items untouched.
            self.local_items = items + [
                {"type": "message", "role": "assistant", "content": text}
            ]
        self.conversation_history.extend(
            [
                {"role": "user", "content": user_request},
                {"role": "assistant", "content": text},
            ]
        )
        return {
            "success": success,
            "error": error,
            "response": text,
            "request": last_request,
            "raw_response": final_response,
            "output_items": all_output_items,
            "tool_calls": tool_calls,
            "tool_round_trips": round_trips,
            "local_tool_mode": True,
            "citations": [],
            "usage": (final_response or {}).get("usage") or {},
            "model": (final_response or {}).get("model") or self.model,
            "requested_model": self.model,
            "provider": self.provider,
            "base_url": self.base_url,
            "status": (final_response or {}).get("status"),
            "elapsed_seconds": elapsed,
            "temperature_omitted_for_reasoning_model": temperature is not None,
        }

    def process_request(
        self,
        user_request: str,
        use_tools: bool = True,
        tool_choice: Literal["auto", "none", "required"] = "auto",
        temperature: float = 0.3,
        max_tokens: Optional[int] = None,
        reasoning_effort: str = "low",
        verbosity: Optional[str] = None,
        dry_run: bool = False,
        background: bool = False,
    ) -> Dict[str, Any]:
        """Create one Responses API turn and retain its complete trace.

        ``temperature`` remains in the signature for legacy callers, but is not
        sent: GPT-5.6 reasoning requests use ``reasoning.effort`` instead.
        """
        if self.provider == "ollama" and not dry_run:
            return self._process_request_with_local_tools(
                user_request,
                use_tools=use_tools,
                temperature=temperature,
                max_tokens=max_tokens,
                reasoning_effort=reasoning_effort,
                verbosity=verbosity,
                background=background,
            )
        request = self._build_responses_request(
            user_request,
            use_tools=use_tools,
            tool_choice=tool_choice,
            reasoning_effort=reasoning_effort,
            verbosity=verbosity,
            max_output_tokens=max_tokens,
            background=background,
        )
        if dry_run:
            return {
                "success": True,
                "dry_run": True,
                "request": request,
                "response": None,
                "tool_calls": [],
                "model": self.model,
                "provider": self.provider,
            }

        started = time.monotonic()
        try:
            status_code, response, stream_events = self._post_responses(request)
            elapsed = round(time.monotonic() - started, 6)
            turn = {
                "request": json.loads(json.dumps(request, ensure_ascii=False)),
                "http_status": status_code,
                "response": response,
                "elapsed_seconds": elapsed,
            }
            if stream_events:
                turn["stream_event_counts"] = stream_events
            self.api_turns.append(turn)
            if not isinstance(response, dict) or status_code >= 400 or response.get("error"):
                error = (response.get("error") if isinstance(response, dict) else None) or {
                    "type": "http_error",
                    "message": (response.get("raw_text") if isinstance(response, dict) else None) or (json.dumps(response)[:500] if response is not None else "Empty response"),
                }
                return {
                    "success": False,
                    "error": error,
                    "response": None,
                    "request": request,
                    "raw_response": response,
                    "tool_calls": [],
                    "citations": [],
                    "usage": (response.get("usage") if isinstance(response, dict) else {}) or {},
                    "model": self.model,
                    "provider": self.provider,
                    "base_url": self.base_url,
                    "elapsed_seconds": elapsed,
                }

            self.previous_response_id = response.get("id")
            text = self._output_text(response)
            self.conversation_history.extend(
                [
                    {"role": "user", "content": user_request},
                    {"role": "assistant", "content": text},
                ]
            )
            return {
                "success": response.get("status") == "completed" and bool(text),
                "error": response.get("error"),
                "response": text,
                "request": request,
                "raw_response": response,
                "output_items": response.get("output") or [],
                "tool_calls": self._tool_items(response),
                "citations": self._citations(response),
                "usage": response.get("usage") or {},
                "model": response.get("model") or self.model,
                "requested_model": self.model,
                "provider": self.provider,
                "base_url": self.base_url,
                "response_id": response.get("id"),
                "status": response.get("status"),
                "elapsed_seconds": elapsed,
                "temperature_omitted_for_reasoning_model": temperature is not None,
            }
        except Exception as exc:
            elapsed = round(time.monotonic() - started, 6)
            self.api_turns.append(
                {
                    "request": request,
                    "elapsed_seconds": elapsed,
                    "error": {"class": type(exc).__name__, "message": str(exc)},
                }
            )
            return {
                "success": False,
                "error": {"class": type(exc).__name__, "message": str(exc)},
                "response": None,
                "request": request,
                "tool_calls": [],
                "citations": [],
                "model": self.model,
                "provider": self.provider,
                "base_url": self.base_url,
                "elapsed_seconds": elapsed,
            }

    def search_and_analyze(
        self, topic: str, analysis_code: Optional[str] = None
    ) -> Dict[str, Any]:
        code_requirement = (
            f"Run this supplied Python in the hosted tool and inspect its output:\n{analysis_code}"
            if analysis_code
            else "Use the hosted Python tool for all quantitative processing."
        )
        return self.process_request(
            f"Research current information about {topic}. {code_requirement} "
            "Cite web sources and distinguish searched facts from computed results.",
            use_tools=True,
            reasoning_effort="medium",
        )

    def clear_history(self) -> None:
        self.conversation_history = []
        self.previous_response_id = None
        self.api_turns = []
        self.local_items = []

    def get_history(self) -> List[Dict[str, Any]]:
        return json.loads(json.dumps(self.conversation_history, ensure_ascii=False))

    def set_system_prompt(self, prompt: str) -> None:
        self.system_prompt = prompt


class GPT5AgentChain:
    """Sequential Responses turns linked with ``previous_response_id``."""

    def __init__(self, agent: GPT5NativeAgent):
        self.agent = agent
        self.chain_results: List[Dict[str, Any]] = []

    def add_step(self, request: str, **kwargs: Any) -> "GPT5AgentChain":
        self.chain_results.append(
            {"request": request, "result": self.agent.process_request(request, **kwargs)}
        )
        return self

    def execute(self) -> List[Dict[str, Any]]:
        return self.chain_results

    def clear(self) -> None:
        self.chain_results = []
