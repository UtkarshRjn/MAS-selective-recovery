"""Who&When failure logs (Zhang et al., ICML 2025).

Each case is a failed multi-agent trajectory with a human annotation of the
failure-responsible agent and the decisive error step. The logs come from
CaptainAgent (algorithm-generated) and Magentic-One (hand-crafted). Their tools
are not replayable here, so recovery is not rerun on them. What this module does:

  * download and parse both subsets
  * turn a log into the same provenance graph used by the booking workflow
  * measure the coarse-lineage suffix of the annotated decisive step
  * run the paper's three attribution prompts against a local model

Dataset: https://huggingface.co/datasets/Kevin355/Who_and_When
Paper:   https://arxiv.org/abs/2505.00212
"""

from __future__ import annotations

import json
import random
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import quote

import httpx

from .llm import LLM
from .provenance import Provenance
from .recovery import coarse_taint

HF_TREE = "https://huggingface.co/api/datasets/Kevin355/Who_and_When/tree/main"
HF_FILE = "https://huggingface.co/datasets/Kevin355/Who_and_When/resolve/main"
SUBSETS = {
    "algorithm": "Who&When/Algorithm-Generated",
    "handcrafted": "Who&When/Hand-Crafted",
}
TURN_CHARS = 800
PROMPT_CHARS = 14000

AGENT_RE = re.compile(r"Agent Name:\s*(.+)", re.IGNORECASE)
STEP_RE = re.compile(r"Step Number:\s*(\d+)", re.IGNORECASE)


@dataclass(frozen=True)
class FailureLog:
    subset: str
    file_id: str
    question: str
    answer: str
    history: tuple[dict[str, Any], ...]
    mistake_agent: str
    mistake_step: int
    mistake_reason: str

    def speaker(self, index: int) -> str:
        entry = self.history[index]
        key = "role" if self.subset == "handcrafted" else "name"
        return str(entry.get(key) or entry.get("name") or entry.get("role") or "unknown")

    @property
    def n_steps(self) -> int:
        return len(self.history)


def _clip(text: str, limit: int = TURN_CHARS) -> str:
    if len(text) <= limit:
        return text
    return text[:limit] + "\n...[truncated]"


def transcript(case: FailureLog, start: int = 0, end: int | None = None) -> str:
    """Numbered transcript. Each turn is clipped so a local 16k context can hold it."""
    stop = case.n_steps if end is None else end
    lines = [
        f"Step {i} - {case.speaker(i)}: {_clip(str(case.history[i].get('content', '')))}"
        for i in range(start, stop)
    ]
    text = "\n".join(lines)
    if len(text) <= PROMPT_CHARS:
        return text
    head, tail = text[: PROMPT_CHARS // 2], text[-PROMPT_CHARS // 2 :]
    return head + "\n...[middle omitted]\n" + tail


def parse_case(raw: dict[str, Any], subset: str, file_id: str) -> FailureLog:
    history = raw.get("history") or []
    step = int(raw["mistake_step"])
    if not 0 <= step < len(history):
        raise ValueError(f"{subset}/{file_id}: mistake_step {step} outside 0..{len(history) - 1}")
    answer = raw.get("ground_truth", raw.get("groundtruth", ""))
    return FailureLog(
        subset=subset,
        file_id=file_id,
        question=str(raw.get("question", "")),
        answer=str(answer),
        history=tuple(history),
        mistake_agent=str(raw["mistake_agent"]),
        mistake_step=step,
        mistake_reason=str(raw.get("mistake_reason", "")),
    )


def load_dir(directory: Path, subset: str) -> list[FailureLog]:
    cases = []
    for path in sorted(directory.glob("*.json"), key=lambda p: int(p.stem) if p.stem.isdigit() else p.stem):
        raw = json.loads(path.read_text())
        cases.append(parse_case(raw, subset, path.stem))
    return cases


def load(root: Path, subset: str = "both") -> list[FailureLog]:
    wanted = list(SUBSETS) if subset == "both" else [subset]
    cases: list[FailureLog] = []
    for name in wanted:
        directory = root / Path(SUBSETS[name]).name
        if not directory.is_dir():
            raise FileNotFoundError(f"{directory} is missing. Run `agentrec who-when download` first.")
        cases.extend(load_dir(directory, name))
    return cases


def download(root: Path, subset: str = "both", client: httpx.Client | None = None) -> dict[str, int]:
    """Save the official JSON logs under root. Skips files already present."""
    own = client is None
    http = client or httpx.Client(timeout=60.0, follow_redirects=True)
    counts: dict[str, int] = {}
    try:
        for name, remote in SUBSETS.items():
            if subset not in (name, "both"):
                continue
            listing = http.get(f"{HF_TREE}/{quote(remote, safe='/')}")
            listing.raise_for_status()
            files = [item["path"] for item in listing.json() if item["path"].endswith(".json")]
            dest_dir = root / Path(remote).name
            dest_dir.mkdir(parents=True, exist_ok=True)
            wrote = 0
            for remote_path in files:
                dest = dest_dir / Path(remote_path).name
                if dest.exists() and dest.stat().st_size > 0:
                    continue
                resp = http.get(f"{HF_FILE}/{quote(remote_path, safe='/')}")
                resp.raise_for_status()
                dest.write_bytes(resp.content)
                wrote += 1
            counts[name] = len(files)
            print(f"{name}: {len(files)} files ({wrote} downloaded) -> {dest_dir}")
    finally:
        if own:
            http.close()
    return counts


def to_provenance(case: FailureLog) -> Provenance:
    """Linear conversation lineage: each turn reads the previous turn's output."""
    prov = Provenance()
    previous: str | None = None
    for i in range(case.n_steps):
        step = prov.add_step(f"s{i}", case.speaker(i))
        if previous is not None:
            prov.add_input(previous, step)
        previous = prov.add_artifact(
            f"s{i}",
            case.history[i].get("content", ""),
            producer=step,
            faulty=i == case.mistake_step,
        )
    return prov


def taint_span(case: FailureLog) -> dict[str, Any]:
    """Steps at or after the annotated decisive error, under coarse lineage.

    On a linear log this is the suffix starting at mistake_step. It is the share of
    the trajectory a restart-from-the-fault policy would have to redo. The prefix
    is the work that stays valid.
    """
    prov = to_provenance(case)
    tainted = {prov.nodes[n].key for n in coarse_taint(prov) if prov.nodes[n].kind == "step"}
    return {
        "file_id": case.file_id,
        "subset": case.subset,
        "n_steps": case.n_steps,
        "mistake_step": case.mistake_step,
        "mistake_agent": case.mistake_agent,
        "tainted_steps": case.n_steps - case.mistake_step,
        "tainted_fraction": round((case.n_steps - case.mistake_step) / case.n_steps, 4),
        "reused_steps": case.mistake_step,
        "lineage_matches_suffix": tainted == {f"s{i}" for i in range(case.mistake_step, case.n_steps)},
    }


def _answer_line(case: FailureLog, with_answer: bool) -> str:
    if not with_answer:
        return "The answer to the problem is not provided.\n"
    return f"The answer for the problem is: {case.answer}\n"


def all_at_once_messages(case: FailureLog, with_answer: bool = True) -> list[dict[str, str]]:
    prompt = (
        "You are an AI assistant tasked with analyzing a multi-agent conversation history when solving a real world problem. "
        f"The problem is: {case.question}\n"
        + _answer_line(case, with_answer)
        + "Identify which agent made an error, at which step, and explain the reason for the error. "
        "Here's the conversation:\n\n"
        + transcript(case)
        + "\n\nBased on this conversation, please predict the following:\n"
        "1. The name of the agent who made a mistake that should be directly responsible for the wrong solution. "
        "If there are no agents that make obvious mistakes, decide one single agent. Directly output the name of the agent.\n"
        "2. The step number where that agent first made the mistake. Steps are numbered from 0 in the transcript above.\n"
        "3. The reason for your prediction.\n"
        "Please answer in the format: Agent Name: (Your prediction)\nStep Number: (Your prediction)\nReason for Mistake:\n"
    )
    return [
        {"role": "system", "content": "You are a helpful assistant skilled in analyzing conversations."},
        {"role": "user", "content": prompt},
    ]


def step_messages(case: FailureLog, index: int, history_so_far: str, with_answer: bool = True) -> list[dict[str, str]]:
    speaker = case.speaker(index)
    prompt = (
        "You are an AI assistant tasked with evaluating the correctness of each step in an ongoing multi-agent conversation "
        f"aimed at solving a real-world problem. The problem being addressed is: {case.question}. "
        + _answer_line(case, with_answer)
        + f"Here is the conversation history up to the current step:\n{history_so_far}\n"
        + f"The most recent step ({index}) was by '{speaker}'.\n"
        + "Your task is to determine whether this most recent agent's action contains an error that could hinder the "
        "problem-solving process or lead to an incorrect solution. "
        "Please respond with 'Yes' or 'No' and provide a clear explanation for your judgment. "
        "Note: Please avoid being overly critical. Focus on errors that clearly derail the process. "
        "Respond ONLY in the format: 1. Yes/No.\n2. Reason: [Your explanation here]"
    )
    return [
        {"role": "system", "content": "You are a precise step-by-step conversation evaluator."},
        {"role": "user", "content": prompt},
    ]


def binary_messages(
    case: FailureLog, start: int, end: int, with_answer: bool = True
) -> list[dict[str, str]]:
    mid = start + (end - start) // 2
    prompt = (
        "You are an AI assistant tasked with analyzing a segment of a multi-agent conversation. "
        "Identify which half contains the single step where the crucial error occurs.\n"
        f"The problem to address is as follows: {case.question}\n"
        + _answer_line(case, with_answer)
        + f"Review the following conversation segment from step {start} to step {end}:\n\n"
        + transcript(case, start, end + 1)
        + f"\n\nPredict whether the most critical error is in the upper half (step {start} to step {mid}) "
        + f"or the lower half (step {mid + 1} to step {end}).\n"
        "Respond with ONLY 'upper half' or 'lower half'."
    )
    return [
        {"role": "system", "content": "You are an AI assistant specializing in localizing errors in conversation segments."},
        {"role": "user", "content": prompt},
    ]


def parse_prediction(text: str) -> tuple[str, int | None]:
    agent = AGENT_RE.search(text)
    step = STEP_RE.search(text)
    return (agent.group(1).strip() if agent else "", int(step.group(1)) if step else None)


def agent_match(predicted: str, gold: str) -> bool:
    return gold.lower() in predicted.lower()


def attribute_all_at_once(llm: LLM, case: FailureLog, with_answer: bool = True) -> dict[str, Any]:
    text = llm.complete(all_at_once_messages(case, with_answer))
    agent, step = parse_prediction(text)
    return _row(case, "all_at_once", agent, step, text)


def attribute_step_by_step(llm: LLM, case: FailureLog, with_answer: bool = True) -> dict[str, Any]:
    history = ""
    agent, step, text = "", None, ""
    for i in range(case.n_steps):
        history += f"Step {i} - {case.speaker(i)}: {_clip(str(case.history[i].get('content', '')))}\n"
        text = llm.complete(step_messages(case, i, history, with_answer))
        if text.lower().strip().startswith("1. yes"):
            agent, step = case.speaker(i), i
            break
    return _row(case, "step_by_step", agent, step, text)


def attribute_binary_search(
    llm: LLM, case: FailureLog, with_answer: bool = True, rng: random.Random | None = None
) -> dict[str, Any]:
    rng = rng or random.Random(0)
    start, end = 0, case.n_steps - 1
    last = ""
    while start < end:
        last = llm.complete(binary_messages(case, start, end, with_answer)).lower()
        mid = start + (end - start) // 2
        if "upper half" in last:
            end = mid
        elif "lower half" in last:
            start = min(mid + 1, end)
        elif rng.randrange(2) == 0:
            end = mid
        else:
            start = min(mid + 1, end)
    return _row(case, "binary_search", case.speaker(start), start, last)


def _row(case: FailureLog, method: str, agent: str, step: int | None, raw: str) -> dict[str, Any]:
    return {
        "subset": case.subset,
        "file_id": case.file_id,
        "method": method,
        "gold_agent": case.mistake_agent,
        "gold_step": case.mistake_step,
        "predicted_agent": agent,
        "predicted_step": step,
        "agent_correct": agent_match(agent, case.mistake_agent),
        "step_correct": step == case.mistake_step,
        "raw": raw,
    }


ATTRIBUTORS = {
    "all_at_once": attribute_all_at_once,
    "step_by_step": attribute_step_by_step,
    "binary_search": attribute_binary_search,
}

DEFAULT_ROOT = Path("data/who-and-when")


def _mean(values: list[float]) -> float:
    return sum(values) / len(values) if values else 0.0


def summarize(cases: list[FailureLog]) -> None:
    print(f"{len(cases)} annotated failure logs")
    for name in SUBSETS:
        group = [c for c in cases if c.subset == name]
        if not group:
            continue
        spans = [taint_span(c) for c in group]
        print(
            f"  {name:12s} n={len(group):3d}  "
            f"mean steps={_mean([c.n_steps for c in group]):5.1f}  "
            f"mean decisive step={_mean([c.mistake_step for c in group]):4.1f}  "
            f"suffix tainted={_mean([s['tainted_fraction'] for s in spans]):5.1%}  "
            f"mean steps redone={_mean([s['tainted_steps'] for s in spans]):4.1f}"
        )


def cmd_download(args: Any) -> None:
    download(Path(args.data), args.subset)


def cmd_summary(args: Any) -> None:
    summarize(load(Path(args.data), args.subset))


def cmd_attribute(args: Any) -> None:
    from .runner import Recorder, make_llm

    cases = [c for c in load(Path(args.data), args.subset) if args.ids is None or c.file_id in args.ids]
    cases = cases[: args.limit]
    llm = make_llm(args)
    fn = ATTRIBUTORS[args.method]
    rec = Recorder(Path(args.out) / f"who-when-{args.method}-{args.subset}")
    print(f"{args.method} on {len(cases)} {args.subset} logs, answer_in_prompt={args.answer} -> {rec.out_dir}")
    agent_hits = step_hits = 0
    for case in cases:
        before = llm.usage.copy()
        row = fn(llm, case, args.answer)
        agent_hits += row["agent_correct"]
        step_hits += row["step_correct"]
        saved = {k: v for k, v in row.items() if k != "raw"}
        rec.row(**saved, usage=llm.usage.since(before))
        (rec.out_dir / "traces").mkdir(exist_ok=True)
        (rec.out_dir / "traces" / f"{case.subset}_{case.file_id}.txt").write_text(row["raw"])
        print(
            f"  {case.subset}/{case.file_id}: gold={case.mistake_agent}@{case.mistake_step} "
            f"pred={row['predicted_agent']}@{row['predicted_step']} "
            f"agent={row['agent_correct']} step={row['step_correct']}"
        )
    n = len(cases)
    print(f"agent accuracy {agent_hits}/{n}    step accuracy {step_hits}/{n}")
