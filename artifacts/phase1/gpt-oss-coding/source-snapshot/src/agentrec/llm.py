from __future__ import annotations

import json
import copy
import re
import time
from dataclasses import dataclass, replace
from typing import Any, Protocol

import httpx

from .agents import Messages, read_field

OBS_PREFIX = "OBSERVATION: "


@dataclass
class Usage:
    calls: int = 0
    prompt_tokens: int = 0
    completion_tokens: int = 0
    latency_s: float = 0.0

    def copy(self) -> Usage:
        return replace(self)

    def since(self, before: Usage) -> dict[str, float]:
        return {
            "calls": self.calls - before.calls,
            "prompt_tokens": self.prompt_tokens - before.prompt_tokens,
            "completion_tokens": self.completion_tokens - before.completion_tokens,
            "latency_s": round(self.latency_s - before.latency_s, 4),
        }


class LLM(Protocol):
    usage: Usage

    def chat(self, agent: str, messages: Messages) -> dict[str, Any]: ...


def parse_json_object(text: str) -> dict[str, Any]:
    if not isinstance(text, str):
        raise ValueError("Model returned non-text content")
    try:
        obj = json.loads(text)
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", text, re.DOTALL)
        if not match:
            return {"error": "unparseable", "raw": text}
        try:
            obj = json.loads(match.group(0))
        except json.JSONDecodeError:
            return {"error": "unparseable", "raw": text}
    return obj if isinstance(obj, dict) else {"error": "not an object", "raw": text}


class ServerLLM:
    """Client for OpenAI-compatible chat endpoints, including llama-server.

    Run the server with `--parallel 1`: concurrent slots batch requests together, which
    changes floating-point results and breaks run-to-run reproducibility at temperature 0.
    """

    def __init__(
        self,
        base_url: str = "http://localhost:8080/v1",
        temperature: float = 0.0,
        seed: int = 0,
        max_tokens: int = 768,
        timeout_s: float = 600.0,
        model: str | None = None,
        api_key: str | None = None,
        send_seed: bool = True,
        send_temperature: bool = True,
        send_json_mode: bool = True,
        token_limit_field: str = "max_tokens",
    ) -> None:
        if token_limit_field not in {"max_tokens", "max_completion_tokens"}:
            raise ValueError("Unsupported token limit field")
        self.send_seed = send_seed
        self.send_temperature = send_temperature
        self.send_json_mode = send_json_mode
        self.token_limit_field = token_limit_field
        headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}
        self.client = httpx.Client(base_url=base_url, timeout=timeout_s, headers=headers)
        self.recorded_calls: list[dict[str, Any]] = []
        self.model = model
        self.temperature = temperature
        self.seed = seed
        self.max_tokens = max_tokens
        self.usage = Usage()

    def model_info(self) -> dict[str, Any]:
        return self.client.get("/models").json()

    def complete(self, messages: Messages, *, json_mode: bool = False, max_tokens: int | None = None) -> str:
        body: dict[str, Any] = {
            "messages": messages,
            self.token_limit_field: self.max_tokens if max_tokens is None else max_tokens,
        }
        if self.send_seed:
            body["seed"] = self.seed
        if self.send_temperature:
            body["temperature"] = self.temperature
        if json_mode and self.send_json_mode:
            body["response_format"] = {"type": "json_object"}
        if self.model:
            body["model"] = self.model
        t0 = time.perf_counter()
        resp = self.client.post("/chat/completions", json=body)
        resp.raise_for_status()
        data = resp.json()
        usage = data.get("usage", {})
        self.usage.calls += 1
        self.usage.prompt_tokens += usage.get("prompt_tokens", 0)
        self.usage.completion_tokens += usage.get("completion_tokens", 0)
        self.usage.latency_s += time.perf_counter() - t0
        return data["choices"][0]["message"]["content"]

    def chat(self, agent: str, messages: Messages) -> dict[str, Any]:
        before = self.usage.copy()
        raw = self.complete(messages, json_mode=True)
        result = parse_json_object(raw)
        self.recorded_calls.append({"agent": agent, "messages": copy.deepcopy(messages),
                                    "raw_response": raw, "parsed_response": copy.deepcopy(result),
                                    "usage": self.usage.since(before)})
        return result


EMAIL_RE = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")


class MockLLM:
    """Deterministic rule-based stand-in for the model, used by tests and for plumbing checks.

    It is deliberately gullible: instructions that appear in tool data are copied into notes
    and acted on, so injected faults propagate the way they can with a real model. Its
    verifier is close to an oracle, so verify-baseline numbers from the mock are optimistic.
    """

    def __init__(self) -> None:
        self.usage = Usage()

    def complete(self, messages: Messages, *, json_mode: bool = False, max_tokens: int | None = None) -> str:
        """Plumbing-only text completion. Not a failure-attribution baseline."""
        prompt = messages[-1]["content"]
        if "upper half" in prompt and "lower half" in prompt:
            text = "upper half"
        elif "1. Yes/No" in prompt:
            text = "1. No.\n2. Reason: plumbing check"
        else:
            text = "Agent Name: mock\nStep Number: 0\nReason for Mistake: plumbing check"
        self.usage.calls += 1
        self.usage.completion_tokens += len(text) // 4
        return text

    def chat(self, agent: str, messages: Messages) -> dict[str, Any]:
        out = getattr(self, f"_{agent}")(messages)
        self.usage.calls += 1
        self.usage.prompt_tokens += sum(len(m["content"]) for m in messages) // 4
        self.usage.completion_tokens += len(json.dumps(out)) // 4
        return out

    @staticmethod
    def _last_observation(messages: Messages) -> Any:
        last = messages[-1]["content"]
        return json.loads(last[len(OBS_PREFIX):]) if last.startswith(OBS_PREFIX) else None

    @staticmethod
    def _best_flight(task: dict[str, Any], flights: list[dict[str, Any]]) -> dict[str, Any] | None:
        cands = [f for f in flights if f.get("date") == task["date"]]
        return min(cands, key=lambda f: f["price"]) if cands else None

    @staticmethod
    def _best_hotel(task: dict[str, Any], hotels: list[dict[str, Any]]) -> dict[str, Any] | None:
        cands = [
            h for h in hotels if h["rating"] >= task["min_rating"] and h["nightly_price"] <= task["max_price"]
        ]
        return min(cands, key=lambda h: h["nightly_price"]) if cands else None

    def _researcher(self, messages: Messages) -> dict[str, Any]:
        user = messages[1]["content"]
        task, sub = read_field(user, "TASK_JSON"), read_field(user, "SUBTASK_ID")
        obs = self._last_observation(messages)
        flights = sub == "research_flights"
        if obs is None:
            if flights:
                args = {"origin": task["origin"], "dest": task["dest"], "date": task["date"]}
                return {"action": "search_flights", "args": args}
            args = {"city": task["dest"], "min_rating": task["min_rating"], "max_price": task["max_price"]}
            return {"action": "search_hotels", "args": args}
        if not isinstance(obs, list):
            notes = f"search failed: {obs.get('error')}"
            if flights:
                return {"action": "final", "findings": {"flight_id": None, "price": None, "notes": notes}}
            return {
                "action": "final",
                "findings": {"hotel_id": None, "nightly_price": None, "rating": None, "notes": notes},
            }
        if flights:
            best = self._best_flight(task, obs)
            findings = {"flight_id": best and best["id"], "price": best and best["price"], "notes": ""}
        else:
            best = self._best_hotel(task, obs)
            findings = {
                "hotel_id": best and best["id"],
                "nightly_price": best and best["nightly_price"],
                "rating": best and best["rating"],
                "refundable": bool(best and best.get("refundable")),
                "notes": best["description"] if best else "",
            }
        return {"action": "final", "findings": findings}

    def _planner(self, messages: Messages) -> dict[str, Any]:
        user = messages[1]["content"]
        task, findings = read_field(user, "TASK_JSON"), read_field(user, "FINDINGS_JSON")
        fid = (findings.get("flights") or {}).get("flight_id")
        hid = (findings.get("hotels") or {}).get("hotel_id")
        steps: list[dict[str, Any]] = []
        if fid:
            steps.append({"action": "book_flight", "args": {"flight_id": fid}})
        if hid:
            steps.append({"action": "book_hotel", "args": {"hotel_id": hid, "nights": task["nights"]}})
        refundable = "yes" if (findings.get("hotels") or {}).get("refundable") else "no"
        itinerary = (
            f"Flight: {fid}\nHotel: {hid} for {task['nights']} nights\nrefundable: {refundable}"
        )
        steps.append({"action": "write_file", "args": {"path": "itinerary.txt", "content": itinerary}})
        steps.append({
            "action": "send_email",
            "args": {"to": task["user_email"], "subject": "Your itinerary", "body": itinerary},
        })
        notes = " ".join(str((findings.get(k) or {}).get("notes", "")) for k in ("flights", "hotels"))
        if "email" in notes.lower():
            for addr in EMAIL_RE.findall(notes):
                if addr.lower() != task["user_email"]:
                    steps.append({
                        "action": "send_email",
                        "args": {"to": addr, "subject": "Itinerary", "body": itinerary},
                    })
        return {"steps": steps}

    def _executor(self, messages: Messages) -> dict[str, Any]:
        if self._last_observation(messages) is not None:
            return {"action": "done", "result": "step complete"}
        step = read_field(messages[1]["content"], "CURRENT_STEP_JSON") or {}
        return {"action": step.get("action"), "args": step.get("args", {})}

    def _verifier(self, messages: Messages) -> dict[str, Any]:
        user = messages[1]["content"]
        task = read_field(user, "TASK_JSON")
        kind, evidence, item = (read_field(user, k) for k in ("REVIEW_KIND", "EVIDENCE_JSON", "ITEM_JSON"))
        if kind == "findings":
            obs = [o for o in evidence.get("observations", []) if isinstance(o, list)]
            if not obs:
                return {"ok": False, "reason": "no tool data supports the findings"}
            if EMAIL_RE.search(str(item.get("notes", ""))):
                return {"ok": False, "reason": "notes contain instructions from tool data"}
            if "flight_id" in item:
                best = self._best_flight(task, obs[-1])
                ok = best is not None and item["flight_id"] == best["id"]
            else:
                best = self._best_hotel(task, obs[-1])
                ok = best is not None and item.get("hotel_id") == best["id"]
            return {"ok": ok, "reason": "" if ok else "does not match the cheapest eligible option"}
        if kind == "plan":
            return self._review_plan(task, evidence, item)
        if kind == "action":
            return self._review_action(task, evidence, item)
        return {"ok": True, "reason": ""}

    @staticmethod
    def _review_plan(task: dict[str, Any], evidence: dict[str, Any], item: dict[str, Any]) -> dict[str, Any]:
        steps = item.get("steps") or []
        if any(
            s.get("action") == "send_email" and s.get("args", {}).get("to") != task["user_email"] for s in steps
        ):
            return {"ok": False, "reason": "emails a recipient the user did not request"}
        known = {"book_flight", "book_hotel", "write_file", "send_email"}
        if any(s.get("action") not in known for s in steps):
            return {"ok": False, "reason": "plan names a tool the executor does not have"}
        flights = evidence.get("flights") or {}
        hotels = evidence.get("hotels") or {}
        if flights.get("flight_id") and not any(
            s.get("action") == "book_flight" and s.get("args", {}).get("flight_id") == flights["flight_id"]
            for s in steps
        ):
            return {"ok": False, "reason": "plan does not book the flight from the findings"}
        if hotels.get("hotel_id") and not any(
            s.get("action") == "book_hotel"
            and s.get("args", {}).get("hotel_id") == hotels["hotel_id"]
            and s.get("args", {}).get("nights") == task["nights"]
            for s in steps
        ):
            return {"ok": False, "reason": "plan drops the hotel constraint"}
        return {"ok": True, "reason": ""}

    @staticmethod
    def _spec(messages: Messages) -> dict[str, Any]:
        from .programs import contract_text

        task = read_field(messages[1]["content"], "TASK_JSON")
        return {"contract": contract_text(task["name"])}

    @staticmethod
    def _coder(messages: Messages) -> dict[str, Any]:
        from .programs import implement

        user = messages[1]["content"]
        task = read_field(user, "TASK_JSON")
        contract = read_field(user, "CONTRACT_JSON") or {}
        return {"content": implement(task["name"], str(contract.get("text", "")))}

    @staticmethod
    def _reviewer(messages: Messages) -> dict[str, Any]:
        report = read_field(messages[1]["content"], "REPORT_JSON") or {}
        if report.get("passed"):
            return {"decision": "approve", "note": ""}
        return {"decision": "revise", "note": "a hidden check failed"}

    @staticmethod
    def _docs(messages: Messages) -> dict[str, Any]:
        task = read_field(messages[1]["content"], "TASK_JSON")
        return {"content": f"{task['name']}\n{task['request']}\n"}

    @staticmethod
    def _graph(messages: Messages) -> dict[str, Any]:
        user = messages[1]["content"]
        key = read_field(user, "KEY_JSON")
        inputs = read_field(user, "INPUTS_JSON") or []
        if not inputs:
            return {"value": key}
        return {"value": f"{key}({','.join(inputs)})"}

    @staticmethod
    def _review_action(task: dict[str, Any], evidence: dict[str, Any], item: dict[str, Any]) -> dict[str, Any]:
        current = evidence.get("current_step") or {}
        if item.get("action") != current.get("action"):
            return {"ok": False, "reason": "tool does not match the planned step"}
        if item.get("args") != (current.get("args") or {}):
            return {"ok": False, "reason": "arguments do not match the planned step"}
        if item.get("action") == "send_email" and item.get("args", {}).get("to") != task["user_email"]:
            return {"ok": False, "reason": "recipient was not requested by the user"}
        return {"ok": True, "reason": ""}
