"""WorkBench pilot: parsing helpers, and (with a checkout) cases, policies, agents, and scoring."""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from agentrec.workbench.agents import fill, parse_reply
from agentrec.workbench.case import agrees
from agentrec.workbench.source import action_string, parse_action

ROOT = Path(os.environ.get("AGENTREC_WORKBENCH_ROOT", "/tmp/upstream/WorkBench"))
needs_checkout = pytest.mark.skipif(
    not (ROOT / "data/processed/tasks_and_outcomes/multi_domain_tasks_and_outcomes.csv").exists(),
    reason="set AGENTREC_WORKBENCH_ROOT to a WorkBench checkout",
)


def test_action_string_round_trips() -> None:
    args = {"recipient": "a@b.com", "subject": "Hi", "body": 'say "ok"\nthanks'}
    tool, parsed = parse_action(action_string("email.send_email", args))
    assert tool == "email.send_email" and parsed == args


def test_parse_reply_takes_answer_or_call() -> None:
    assert parse_reply('thinking\nCALL {"tool": "x", "arguments": {"a": 1}}') == ("CALL", {"tool": "x", "arguments": {"a": 1}})
    assert parse_reply('ANSWER {"email": "a@b"}') == ("ANSWER", {"email": "a@b"})
    assert parse_reply("ANSWER []") == ("ANSWER", [])
    assert parse_reply("no idea") is None


def test_fill_reads_params_and_inputs() -> None:
    text = fill("tasks of {assignee.email} named {name}", {"name": "dmitri"}, {"assignee": {"email": "d@x"}})
    assert text == "tasks of d@x named dmitri"


def test_agrees_normalizes_formats() -> None:
    assert agrees({"overdue": "true", "email": "A@X.com"}, {"overdue": True, "email": "a@x.com"}, ("overdue", "email"))
    assert agrees({"start": "2023-12-01 09:00"}, {"start": "2023-12-01 09:00:00"}, ("start",))
    assert agrees({"last_contact_date": "2023-11-01"}, {"last_contact_date": "2023-11-01 00:00:00"}, ("last_contact_date",))
    assert not agrees({"crossed": False}, {"crossed": True}, ("crossed",))
    assert not agrees("text", {"crossed": True}, ("crossed",))


# ------------------------------------------------------------ with a checkout


@pytest.fixture(scope="module")
def wb():
    from agentrec.workbench.source import WorkBench

    return WorkBench(ROOT)


def _row(wb, template_key: str, index: int):
    return next(task for task in wb.tasks() if task.index == index)


def _case(wb, key: str, index: int, agent=None):
    from agentrec.workbench.agents import ReferenceAgent
    from agentrec.workbench.case import WorkBenchCase
    from agentrec.workbench.templates import BY_KEY

    agent = agent or ReferenceAgent(wb)
    return WorkBenchCase(wb, _row(wb, key, index), BY_KEY[key], agent, corrector=agent, max_wait_s=5).build()


def _run(case, schedule: str, policy: str):
    from agentrec import policies
    from agentrec.mechanism.harness import run_cell

    return run_cell(case, case.schedule(schedule), 0, policies.get(policy), clean=schedule == "clean",
                    horizon=60, correction_budget=2, real_clock=True).row


@needs_checkout
def test_selection_reproduces_ground_truth(wb) -> None:
    from agentrec.workbench.pilot import select

    selection = select(wb)
    assert len(selection["selected"]) == 12
    assert {item["template"] for item in selection["selected"]} == {
        "overdue_meeting_email", "crm_stale_meeting", "metric_alert_email", "metric_task_or_email"}


@needs_checkout
@pytest.mark.parametrize("policy", ["restart", "suffix", "claim_rerun", "online", "atomix_style"])
@pytest.mark.parametrize("schedule", ["clean", "inflight", "pre_effect"])
def test_reference_is_safe_under_every_pilot_policy(wb, policy: str, schedule: str) -> None:
    row = _run(_case(wb, "overdue_meeting_email", 150), schedule, policy)
    assert row["outcome"] == "safe_completion"
    assert row["workbench_correct"] and not row["workbench_side_effects"]
    assert row["official_result_eligible"] is False
    if schedule != "clean":
        assert row["faults_detected"] == {"lookup_fault": True}


@needs_checkout
def test_ungated_posthoc_repair_sends_the_wrong_email(wb) -> None:
    row = _run(_case(wb, "overdue_meeting_email", 150), "pre_effect", "posthoc_repair")
    assert row["outcome"] != "safe_completion"
    assert row["forbidden_effects"] >= 1
    assert row["compensated_effects"] >= 1  # the meeting is reverted; the email cannot be


@needs_checkout
def test_final_actions_drop_reverted_effects(wb) -> None:
    from agentrec.workbench.agents import ReferenceAgent
    from agentrec.workbench.case import REVERT, WorkBenchCase
    from agentrec.workbench.templates import BY_KEY

    case = WorkBenchCase(wb, _row(wb, "crm_stale_meeting", 0), BY_KEY["crm_stale_meeting"], ReferenceAgent(wb))

    class FakeWorld:
        effects = [("calendar.create_event", {"event_name": "x"}), (REVERT + "calendar.create_event", {"event_name": "x"}),
                   ("calendar.create_event", {"event_name": "y"})]

    assert case.final_actions(FakeWorld()) == ['calendar.create_event.func(event_name="y")']


@needs_checkout
def test_plan_check_rejects_a_plan_that_does_not_follow(wb) -> None:
    from agentrec.contract import Evidence
    from agentrec.workbench.agents import ReferenceAgent
    from agentrec.workbench.case import WorkBenchCase
    from agentrec.workbench.templates import BY_KEY

    template = BY_KEY["metric_alert_email"]
    task = _row(wb, "metric_alert_email", 90)
    case = WorkBenchCase(wb, task, template, ReferenceAgent(wb))
    found = {"metric": template.lookup("metric").derive(wb, case.params, {})}

    class Ctx:
        def versions(self, claim_id):
            return [Evidence(claim_id, 1, found[claim_id], "lookup:metric", ())]

    def check(actions):
        evidence = Evidence("plan", 1, case._plan(actions), "decide", ())
        return case._plan_check(evidence, Ctx()).ok

    assert check(template.decide(case.params, found))
    assert not check([])


class ScriptedLLM:
    def __init__(self, replies: list[str]) -> None:
        self.replies = list(replies)
        self.seen: list[list[dict[str, str]]] = []

    def complete(self, messages, max_tokens=None):
        self.seen.append([dict(item) for item in messages])
        return self.replies.pop(0)


@needs_checkout
def test_model_agent_runs_a_tool_loop_without_ground_truth(wb) -> None:
    from agentrec.workbench.agents import LookupRequest, ModelAgent
    from agentrec.workbench.templates import BY_KEY

    template = BY_KEY["overdue_meeting_email"]
    task = _row(wb, "overdue_meeting_email", 150)
    llm = ScriptedLLM([
        'CALL {"tool": "company_directory.find_email_address", "arguments": {"name": "carlos"}}',
        'ANSWER {"name": "carlos", "email": "carlos.rodriguez@atlas.com"}',
    ])
    metered: list[tuple[int, int]] = []
    agent = ModelAgent(lambda _seed: llm, wb)
    params = template.parse(task.task)
    value = agent.lookup(LookupRequest(task.task, template, template.lookup("assignee"), params, {}, 1, 0),
                         lambda p, c, _s, _e: metered.append((p, c)))
    assert value == {"name": "carlos", "email": "carlos.rodriguez@atlas.com"}
    assert len(metered) == 2
    transcript = json.dumps(llm.seen)
    assert "Result:" in transcript
    assert all(action not in transcript for action in task.outcome)
    assert "Today's date is Thursday, 2023-11-30" in transcript


@needs_checkout
def test_model_agent_refuses_tools_outside_its_lookup(wb) -> None:
    from agentrec.workbench.agents import LookupRequest, ModelAgent
    from agentrec.workbench.templates import BY_KEY

    template = BY_KEY["metric_alert_email"]
    task = _row(wb, "metric_alert_email", 90)
    llm = ScriptedLLM([
        'CALL {"tool": "email.send_email", "arguments": {"recipient": "x"}}',
        'ANSWER {"crossed": true}',
    ])
    agent = ModelAgent(lambda _seed: llm, wb)
    agent.lookup(LookupRequest(task.task, template, template.lookup("metric"), template.parse(task.task), {}, 1, 0), None)
    assert "Unknown tool email.send_email" in llm.seen[-1][-1]["content"]


@needs_checkout
def test_frozen_pilot_refuses_a_changed_config(wb, tmp_path: Path) -> None:
    from agentrec.workbench.pilot import freeze, run

    freeze(ROOT, tmp_path, models=())
    with pytest.raises(FileExistsError):
        freeze(ROOT, tmp_path, models=())
    result = run(tmp_path, reference=True, rows=(92,), policies=("online",), progress=False)
    assert result["ran"] == 9
    assert run(tmp_path, reference=True, rows=(92,), policies=("online",), progress=False)["ran"] == 0
    config = json.loads((tmp_path / "pilot.json").read_text())
    config["seeds"] = [0]
    (tmp_path / "pilot.json").write_text(json.dumps(config))
    with pytest.raises(RuntimeError):
        run(tmp_path, reference=True, rows=(92,), policies=("online",), progress=False)
