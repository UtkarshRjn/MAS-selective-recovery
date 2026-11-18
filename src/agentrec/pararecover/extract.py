"""Select ParaRecover Level-2 motifs and freeze them as executable two-fault cases.

ParaRecover scores re-planning over recorded context; it does not execute
plans. This extract keeps what a runtime experiment needs from each selected
record: the plan DAG, the tools it names (with their schemas), the recorded
tool results where the rollout has them, the error tags, and the two plan
nodes the error analysis names first. Cases built from it are a new
experimental condition ("ParaRecover-derived motifs"), never a ParaRecover
score.

Selection rule (frozen), over `data/LEVEL-2/LEVEL-2_real_rollout.jsonl` in file
order:

1. at least two error tags (coexisting errors), every tag a value error that
   corrupts a node's output (parameter type/name/value, missing parameter,
   wrong tool name/selection, wrong or missing dependency, wrong
   parallelization); missing-tool, redundant-call, exception-handling, and
   wrong-serialization records are excluded because no single node output is
   wrong;
2. the plan parses, every dependency exists, and it has 3 to 12 tool nodes;
3. the error analysis names at least two distinct tool nodes; the first two
   named are the fault sites;
4. the two sites' cones (the node and its descendants, excluding the final
   response) are classified as nested, overlapping, or disjoint, and the first
   four records of each class are taken: two `dev`, two held-out `test`.
"""

from __future__ import annotations

import hashlib
import json
import re
import subprocess
from collections import defaultdict
from pathlib import Path
from typing import Any

SOURCE_FILE = "data/LEVEL-2/LEVEL-2_real_rollout.jsonl"
PER_CLASS = 4
CLASSES = ("nested", "overlap", "disjoint")
TAGS = {
    "参数类型错误": "parameter type error",
    "参数名称错误": "parameter name error",
    "参数值非法": "illegal parameter value",
    "必要参数缺失": "missing required parameter",
    "工具名称错误": "wrong tool name",
    "工具选择错误": "wrong tool selection",
    "依赖关系错误": "wrong dependency",
    "依赖关系缺失": "missing dependency",
    "错误并行": "wrong parallelization",
}
EXCLUDED_TAGS = {
    "必要工具缺失": "missing required tool",
    "工具调用冗余": "redundant tool call",
    "异常应对错误": "wrong exception handling",
    "错误串行": "wrong serialization",
}
# A tool whose name starts with one of these changes external state; the first group cannot be undone.
IRREVERSIBLE_PREFIXES = ("send_", "post_", "publish_", "pay_", "purchase_", "transfer_", "submit_", "place_",
                         "order_", "notify_", "email_", "share_", "broadcast_")
REVERSIBLE_PREFIXES = ("book_", "create_", "schedule_", "log_", "update_", "add_", "set_", "register_", "reserve_",
                       "install_", "adjust_", "save_", "write_", "record_", "enroll_", "apply_", "assign_",
                       "power_on_", "turn_on_", "turn_off_", "remove_", "delete_", "cancel_", "upload_")
_NODES = re.compile(r"节点\s*([0-9][0-9.]*(?:\s*[、,，和与及]\s*[0-9][0-9.]*)*)")


def effect_class(tool: str) -> str | None:
    if tool.startswith(IRREVERSIBLE_PREFIXES):
        return "irreversible"
    if tool.startswith(REVERSIBLE_PREFIXES):
        return "reversible"
    return None


def named_nodes(text: str) -> list[str]:
    out: list[str] = []
    for match in _NODES.finditer(text or ""):
        out += [item.rstrip(".") for item in re.split(r"\s*[、,，和与及]\s*", match.group(1)) if item]
    return list(dict.fromkeys(out))


def _descendants(tasks: dict[str, dict[str, Any]], node: str) -> set[str]:
    children: dict[str, set[str]] = defaultdict(set)
    for task_id, task in tasks.items():
        for dep in task.get("dep", []):
            children[dep].add(task_id)
    out: set[str] = set()
    stack = [node]
    while stack:
        for child in children[stack.pop()]:
            if child not in out:
                out.add(child)
                stack.append(child)
    return out - {"response"}


def relation(tasks: dict[str, dict[str, Any]], first: str, second: str) -> str:
    cone_a = _descendants(tasks, first) | {first}
    cone_b = _descendants(tasks, second) | {second}
    if second in cone_a or first in cone_b:
        return "nested"
    return "overlap" if cone_a & cone_b else "disjoint"


def _acyclic(tasks: dict[str, dict[str, Any]]) -> bool:
    state: dict[str, int] = {}

    def visit(node: str) -> bool:
        if state.get(node) == 1:
            return False
        if state.get(node) == 2:
            return True
        state[node] = 1
        ok = all(visit(dep) for dep in tasks[node].get("dep", []))
        state[node] = 2
        return ok

    return all(visit(node) for node in tasks)


def candidate(record: dict[str, Any]) -> tuple[dict[str, Any] | None, str]:
    tags = [tag for tag in (record.get("tag_1"), record.get("tag_2"), record.get("tag_3")) if tag]
    if len(tags) < 2:
        return None, "fewer than two error tags"
    if not all(tag in TAGS for tag in tags):
        return None, "a tag that does not corrupt a node output"
    try:
        plan = json.loads(record["plan"]) if isinstance(record["plan"], str) else record["plan"]
        tasks = {str(task["id"]): task for task in plan["tasks"]}
    except (KeyError, TypeError, ValueError):
        return None, "plan does not parse"
    if "response" not in tasks or any(str(dep) not in tasks for task in tasks.values() for dep in task.get("dep", [])):
        return None, "plan has a dangling dependency"
    if not _acyclic(tasks):
        return None, "plan is cyclic"
    tools = [task_id for task_id in tasks if task_id not in ("root", "response")]
    if not 3 <= len(tools) <= 12:
        return None, "plan size outside 3..12 tool nodes"
    sites = [node for node in named_nodes(record.get("error_reason", "")) if node in tools]
    if len(sites) < 2:
        return None, "error analysis names fewer than two tool nodes"
    schemas = {item["name"]: item for item in json.loads(record["tool_list"])}
    recorded: dict[str, str] = {}
    for call in record.get("fun_call") or []:
        recorded.setdefault(call["name"], str(call.get("results", "")))
    nodes = []
    for task_id in tools:
        task = tasks[task_id]
        name = str(task.get("name", ""))
        nodes.append({
            "id": task_id, "tool": name, "deps": [str(dep) for dep in task.get("dep", []) if dep != "root"],
            "schema": schemas.get(name, {}), "recorded_result": recorded.get(name),
            "effect": effect_class(name),
        })
    motif = {
        "record_id": record["id"], "query_id": record.get("query_id"), "query": record["query"],
        "tags": [TAGS[tag] for tag in tags], "tags_zh": tags, "error_reason": record.get("error_reason", ""),
        "sites": sites[:2], "relation": relation(tasks, sites[0], sites[1]), "nodes": nodes,
        "response_deps": [str(dep) for dep in tasks["response"].get("dep", []) if dep != "root"],
        "round_gt": record.get("round_gt"), "funcall_times_gt": record.get("funcall_times_gt"),
    }
    return motif, ""


def select(root: str | Path) -> dict[str, Any]:
    root = Path(root)
    path = root / SOURCE_FILE
    commit = subprocess.run(["git", "-C", str(root), "rev-parse", "HEAD"], capture_output=True, text=True,
                            check=True).stdout.strip()
    chosen: dict[str, list[dict[str, Any]]] = {name: [] for name in CLASSES}
    reasons: dict[str, int] = defaultdict(int)
    for line in path.read_text().splitlines():
        if not line.strip():
            continue
        motif, reason = candidate(json.loads(line))
        if motif is None:
            reasons[reason] += 1
            continue
        bucket = chosen[motif["relation"]]
        if len(bucket) < PER_CLASS:
            bucket.append(motif)
        else:
            reasons[f"{motif['relation']} class already full"] += 1
    for name, bucket in chosen.items():
        if len(bucket) < PER_CLASS:
            raise ValueError(f"only {len(bucket)} {name} motifs qualify")
    return {
        "source": {"repository": "ParaRecover", "commit": commit, "file": SOURCE_FILE, "license": "MIT", "copyright": "Copyright (c) 2026 ParaRecover Contributors",
                   "sha256": hashlib.sha256(path.read_bytes()).hexdigest()},
        "rule": __doc__.split("Selection rule (frozen)", 1)[1].strip(),
        "official_result_eligible": False,
        "motifs": {name: bucket for name, bucket in chosen.items()},
        "exclusions": dict(sorted(reasons.items())),
    }


def main() -> None:
    import argparse

    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("root")
    parser.add_argument("--out", default=str(Path(__file__).with_name("motifs.json")))
    args = parser.parse_args()
    data = select(args.root)
    Path(args.out).write_text(json.dumps(data, ensure_ascii=False, indent=1, sort_keys=True) + "\n")
    print(json.dumps({name: [m["record_id"] for m in items] for name, items in data["motifs"].items()}))


if __name__ == "__main__":
    main()


__all__ = ["CLASSES", "TAGS", "candidate", "effect_class", "named_nodes", "relation", "select"]
