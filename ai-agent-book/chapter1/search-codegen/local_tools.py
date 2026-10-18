"""Local client-side implementations of the two hosted tools, for Ollama runs.

Ollama speaks the Responses protocol but has no server-side ``web_search`` or
``code_interpreter``: there is nothing behind those tool types to execute the
calls.  On the local path the same two capabilities degrade to function tools
(``web_search`` / ``run_python``) that this process executes between model
turns.

Honesty note (mirrored in the README): the mock corpus below is *not* a web
search.  The ASEAN coordinates deliberately mirror
``run_experiment_1_3.ASEAN_CAPITAL_COORDS``, so a local run can only validate
the *computation* leg (45 haversine pairs, nearest pair) against the
independent reference — never the provenance of the searched facts.  The
Bitcoin series is synthetic, so technical-analysis runs demonstrate the
tool loop, not market analysis.
"""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
from typing import Any, Dict

# Standard coordinates of the ten ASEAN capitals.  Same values as the
# independent reference in run_experiment_1_3.py — on purpose (see the module
# docstring), and the README/ledger say so explicitly.
MOCK_ASEAN_CAPITALS: Dict[str, Dict[str, Any]] = {
    "Brunei": {"capital": "Bandar Seri Begawan", "lat": 4.9031, "lon": 114.9398},
    "Cambodia": {"capital": "Phnom Penh", "lat": 11.5564, "lon": 104.9282},
    "Indonesia": {"capital": "Jakarta", "lat": -6.2088, "lon": 106.8456},
    "Laos": {"capital": "Vientiane", "lat": 17.9757, "lon": 102.6331},
    "Malaysia": {"capital": "Kuala Lumpur", "lat": 3.1390, "lon": 101.6869},
    "Myanmar": {"capital": "Naypyidaw", "lat": 19.7633, "lon": 96.0785},
    "Philippines": {"capital": "Manila", "lat": 14.5995, "lon": 120.9842},
    "Singapore": {"capital": "Singapore", "lat": 1.3521, "lon": 103.8198},
    "Thailand": {"capital": "Bangkok", "lat": 13.7563, "lon": 100.5018},
    "Vietnam": {"capital": "Hanoi", "lat": 21.0278, "lon": 105.8342},
}

# Fixed synthetic BTC/USD daily closes (60 sessions: rally to 112.6k, ~15%
# drawdown, partial recovery).  Written out literally so every local run is
# reproducible; never presented as real market data.
MOCK_BTC_DAILY_CLOSES = [
    96250, 95800, 97100, 96550, 98300, 99200, 98800,
    100450, 101600, 100900, 103250, 104800, 106100, 105300,
    107450, 108900, 108200, 110350, 111800, 111200, 112600,
    111850, 110900, 111650, 110200, 108750, 109400, 107800,
    106250, 105100, 103900, 104650, 103100, 101800, 102450,
    100900, 101650, 100200, 98750, 99400, 97800, 96500,
    97250, 95800, 96900, 98250, 99100, 98450, 99800,
    100750, 101400, 100650, 101900, 103250, 104100, 103450,
    104850, 105900, 105250, 106450,
]

_MOCK_BTC_KEYWORDS = ("bitcoin", "btc", "比特币")
_MOCK_ASEAN_KEYWORDS = (
    "asean", "东盟", "capital", "首都", "coordinate", "坐标",
    "brunei", "cambodia", "indonesia", "laos", "malaysia", "myanmar",
    "philippines", "singapore", "thailand", "vietnam",
    "文莱", "柬埔寨", "印度尼西亚", "老挝", "马来西亚", "缅甸",
    "菲律宾", "新加坡", "泰国", "越南", "吉隆坡", "曼谷", "河内", "雅加达",
)


def mock_web_search(query: str) -> Dict[str, Any]:
    """Route a query against the bundled sample dataset."""
    lowered = (query or "").lower()
    if any(k in lowered for k in _MOCK_BTC_KEYWORDS):
        return {
            "source": "local_mock_dataset",
            "note": (
                "Synthetic sample data for offline local-model runs; "
                "not real market prices."
            ),
            "topic": "BTC/USD synthetic daily closes",
            "series": {
                "metric": "daily_close_usd",
                "sessions": len(MOCK_BTC_DAILY_CLOSES),
                "values": MOCK_BTC_DAILY_CLOSES,
            },
        }
    if any(k in lowered for k in _MOCK_ASEAN_KEYWORDS):
        return {
            "source": "local_mock_dataset",
            "note": (
                "Static sample dataset bundled with local_tools.py; mirrors "
                "the independent reference in run_experiment_1_3.py."
            ),
            "topic": "ASEAN member state capitals and coordinates",
            "capitals": MOCK_ASEAN_CAPITALS,
        }
    return {
        "source": "local_mock_dataset",
        "note": "No matching documents in the local sample dataset.",
        "query": query,
        "result": None,
    }


def run_python_sandbox(code: str, timeout_seconds: int = 30) -> Dict[str, Any]:
    """Execute model-authored Python in a throwaway interpreter.

    The snippet runs with ``cwd`` set to a temp directory (any files it writes
    land there and vanish afterwards) and a hard timeout.  stdout/stderr are
    truncated before re-entering the model context: a 4B model's context
    window cannot absorb an unbounded tool result.
    """
    if not isinstance(code, str) or not code.strip():
        return {"exit_code": 1, "stdout": "", "stderr": "empty code"}
    with tempfile.TemporaryDirectory() as tmp:
        script = tempfile.mkstemp(suffix=".py", dir=tmp)[1]
        with open(script, "w", encoding="utf-8") as handle:
            handle.write(code)
        try:
            proc = subprocess.run(
                [sys.executable, "-X", "utf8", script],
                capture_output=True,
                text=True,
                timeout=timeout_seconds,
                cwd=tmp,
            )
        except subprocess.TimeoutExpired:
            return {
                "exit_code": None,
                "stdout": "",
                "stderr": f"timed out after {timeout_seconds}s",
            }
        return {
            "exit_code": proc.returncode,
            "stdout": _truncate(proc.stdout, 4000),
            "stderr": _truncate(proc.stderr, 2000),
        }


def _truncate(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    return text[:limit] + f"\n…[truncated, {len(text) - limit} chars dropped]"


def execute_local_tool(name: str, raw_arguments: str) -> str:
    """Dispatch one function_call and return the JSON string for its output.

    Malformed arguments come back as an error payload instead of raising, so
    the model can see the failure and self-correct on the next round.
    """
    try:
        arguments = json.loads(raw_arguments or "{}")
        if not isinstance(arguments, dict):
            raise ValueError("arguments must be a JSON object")
    except ValueError as exc:
        return json.dumps(
            {"error": f"invalid tool arguments: {exc}"}, ensure_ascii=False
        )

    if name == "web_search":
        result = mock_web_search(str(arguments.get("query", "")))
    elif name == "run_python":
        result = run_python_sandbox(str(arguments.get("code", "")))
    else:
        result = {"error": f"unknown tool: {name}"}
    return json.dumps(result, ensure_ascii=False)
