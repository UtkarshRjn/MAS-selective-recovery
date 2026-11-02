"""Small programs the coding team implements. The hidden suite lives here.

Agents see the request and the contract. They do not see these tests or the
reference bodies. The checker loads the submitted file and calls the suite.
"""

from __future__ import annotations

from typing import Any

_BUILTINS = {
    "min": min,
    "max": max,
    "len": len,
    "str": str,
    "int": int,
    "abs": abs,
    "range": range,
    "bool": bool,
}


def _clamp(missing: set[str]) -> str:
    if "swap" in missing:
        body = "    return min(max(x, lo), hi)\n"
    else:
        body = "    if lo > hi:\n        lo, hi = hi, lo\n    return min(max(x, lo), hi)\n"
    return "def clamp(x, lo, hi):\n" + body


def _nonzero(missing: set[str]) -> str:
    expr = "n > 0" if "negative" in missing else "n != 0"
    return f"def nonzero(n):\n    return {expr}\n"


def _clip(missing: set[str]) -> str:
    if "negative" in missing:
        body = "    return s[:n]\n"
    else:
        body = '    if n < 0:\n        return ""\n    return s[:n]\n'
    return "def clip_text(s, n):\n" + body


PROGRAMS: dict[str, dict[str, Any]] = {
    "clamp": {
        "entry": "clamp",
        "signature": "clamp(x, lo, hi)",
        "request": "Write clamp(x, lo, hi). Keep x inside [lo, hi]. If lo > hi, swap the ends before clamping.",
        "requirements": (
            {"id": "range", "text": "Keep x inside [lo, hi]."},
            {"id": "swap", "text": "If lo > hi, swap the ends before clamping."},
        ),
        "drop": "swap",
        "source": _clamp,
        "tests": (
            {"args": (5, 0, 10), "expect": 5},
            {"args": (-1, 0, 10), "expect": 0},
            {"args": (11, 0, 10), "expect": 10},
            {"args": (5, 10, 0), "expect": 5},
        ),
    },
    "nonzero": {
        "entry": "nonzero",
        "signature": "nonzero(n)",
        "request": "Write nonzero(n). Return False for 0. Return True for any other integer. Treat negative numbers as True.",
        "requirements": (
            {"id": "zero", "text": "Return False for 0."},
            {"id": "positive", "text": "Return True for any other integer."},
            {"id": "negative", "text": "Treat negative numbers as True."},
        ),
        "drop": "negative",
        "source": _nonzero,
        "tests": (
            {"args": (0,), "expect": False},
            {"args": (4,), "expect": True},
            {"args": (-3,), "expect": True},
        ),
    },
    "clip_text": {
        "entry": "clip_text",
        "signature": "clip_text(s, n)",
        "request": "Write clip_text(s, n). Return the first n characters. If n is negative, return the empty string.",
        "requirements": (
            {"id": "prefix", "text": "Return the first n characters."},
            {"id": "negative", "text": "If n is negative, return the empty string."},
        ),
        "drop": "negative",
        "source": _clip,
        "tests": (
            {"args": ("hello", 2), "expect": "he"},
            {"args": ("hello", 0), "expect": ""},
            {"args": ("hello", -1), "expect": ""},
        ),
    },
}


def contract_text(name: str) -> str:
    return " ".join(item["text"] for item in PROGRAMS[name]["requirements"])


def implement(name: str, contract: str) -> str:
    program = PROGRAMS[name]
    missing = {item["id"] for item in program["requirements"] if item["text"] not in contract}
    return program["source"](missing)


def broken(name: str) -> str:
    signature = PROGRAMS[name]["signature"]
    if not signature.startswith("def "):
        signature = f"def {signature}:"
    return signature + "\n    return None\n"


def strip_requirement(name: str, text: str) -> str:
    dropped = next(item for item in PROGRAMS[name]["requirements"] if item["id"] == PROGRAMS[name]["drop"])
    return " ".join(text.replace(dropped["text"], "").split())


def run_suite(src: str, name: str) -> dict[str, Any]:
    """Execute `src` and score it against the hidden suite for `name`."""
    program = PROGRAMS[name]
    namespace: dict[str, Any] = {}
    try:
        exec(src, {"__builtins__": _BUILTINS}, namespace)  # noqa: S102
        fn = namespace[program["entry"]]
    except Exception as exc:
        return {"passed": False, "n": len(program["tests"]), "n_passed": 0, "failures": [{"error": type(exc).__name__}]}
    failures = []
    for case in program["tests"]:
        try:
            got = fn(*case["args"])
        except Exception as exc:
            failures.append({"args": list(case["args"]), "expect": case["expect"], "error": type(exc).__name__})
            continue
        if got != case["expect"]:
            failures.append({"args": list(case["args"]), "expect": case["expect"], "got": got})
    n = len(program["tests"])
    return {"passed": not failures, "n": n, "n_passed": n - len(failures), "failures": failures}
