"""Mechanism, boundary, statistics, and suite-runner tests."""

from __future__ import annotations

import json

import pytest

from agentrec import policies
from agentrec.mechanism import cases
from agentrec.mechanism.harness import run_cell
from agentrec.mechanism.stats import (
    cluster_bootstrap,
    common_clean_population,
    compare,
    crsr_safe,
    mcnemar_exact,
    quantile,
    summarize,
)
from agentrec.mechanism.suite import SuiteConfig, load_rows, manifest, preset, run_suite

VOLATILE = {"wall_s", "lock", "idle_spins"}


def cell(family: str, schedule: str, policy: str, seed: int = 0, **kwargs) -> dict:
    case = cases.build(family, kwargs.pop("template", "dev"))
    chosen = next(item for item in case.schedules if item.name == schedule)
    return run_cell(case, chosen, seed, policies.get(policy), **kwargs).row


@pytest.mark.parametrize("family", sorted(cases.ALL_FAMILIES))
@pytest.mark.parametrize("template", cases.TEMPLATES)
def test_every_family_completes_cleanly_under_core_policies(family: str, template: str) -> None:
    case = cases.build(family, template)
    if not case.has_clean_condition:
        pytest.skip("the hazard is the schedule itself")
    for config in policies.CORE:
        row = run_cell(case, case.schedules[0], 0, config, clean=True).row
        assert row["outcome"] == "safe_completion", (family, template, config.name, row.get("error"))


@pytest.mark.parametrize("family", sorted(cases.MECHANISM_FAMILIES))
def test_core_policies_recover_safely_in_mechanism_families(family: str) -> None:
    case = cases.build(family, "dev")
    for schedule in cases.core_schedules(case):
        for name in ("restart", "suffix", "claim_rerun", "online", "verify_before"):
            row = run_cell(case, schedule, 1, policies.get(name)).row
            assert row["outcome"] == "safe_completion", (family, schedule.name, name, row["outcome"])
            assert row["forbidden_effects"] == 0 and row["duplicate_effects"] == 0


def test_runs_are_deterministic() -> None:
    first = cell("E", "overlap", "online", seed=3)
    second = cell("E", "overlap", "online", seed=3)
    assert {k: v for k, v in first.items() if k not in VOLATILE} == {
        k: v for k, v in second.items() if k not in VOLATILE
    }


def test_detection_delay_is_matched_across_policies() -> None:
    held = {name: cell("A", "inflight", name)["realized_ordering"]["date"]["released_at"] for name in
            ("suffix", "online", "verify_before", "advisory")}
    assert len(set(held.values())) == 1


def test_online_preserves_independent_work_that_restart_repeats() -> None:
    online = cell("A", "immediate", "online")
    restart = cell("A", "immediate", "restart")
    assert online["independent_preserved"] == online["independent_tasks"] > 0
    assert restart["independent_preserved"] == 0
    assert online["tokens"] < restart["tokens"]


def test_persistent_fault_needs_a_checked_correction() -> None:
    assert cell("A", "persistent", "online")["outcome"] == "safe_completion"
    assert cell("A", "persistent", "no_correction_model")["outcome"] == "safe_but_incomplete"


def test_applicability_and_versioned_handoff_prevent_a_forbidden_merge() -> None:
    assert cell("B", "p1_early", "online")["forbidden_effects"] == 0
    assert cell("B", "p1_early", "no_applicability")["forbidden_effects"] > 0
    assert cell("B", "p1_early", "unversioned_handoff")["forbidden_effects"] > 0


def test_invalidation_by_gate_ablation() -> None:
    assert cell("C", "pre_commit", "online")["outcome"] == "safe_completion"
    assert cell("C", "pre_commit", "inv_on_gate_off")["safe"] is False
    assert cell("C", "pre_commit", "inv_off_gate_on")["outcome"] == "safe_but_incomplete"
    assert cell("C", "pre_commit", "inv_off_gate_off")["safe"] is False


def test_posthoc_repair_dispatches_before_the_verdict() -> None:
    row = cell("C", "late", "posthoc_repair")
    assert row["safe"] is False and row["dispatched_on_dead_support"] + row["forbidden_effects"] > 0


def test_uncancellable_transport_is_billed_after_cancel_but_fenced() -> None:
    row = cell("F", "uncancellable", "online")
    assert row["outcome"] == "safe_completion"
    assert row["unobservable_cancels"] >= 1 and row["stale_outputs_accepted"] == 0
    assert cell("F", "uncancellable", "cancel_only")["outcome"] != "safe_completion"


def test_trust_models_on_a_global_invariant() -> None:
    assert cell("G", "local_commit_ambiguous", "online")["outcome"] == "safe_but_incomplete"
    for schedule in ("global_settle_ambiguous", "revocable_attributed", "global_settle_attributed"):
        assert cell("G", schedule, "online")["outcome"] == "safe_completion", schedule


def test_conditional_writes_are_needed_against_concurrent_writers() -> None:
    for schedule in ("two_sellers", "address_change_before_check"):
        assert cell("H", schedule, "online")["safe"] is False, schedule
        assert cell("H", schedule, "online_cas")["safe"] is True, schedule


def test_lost_acknowledgments_need_reconciliation() -> None:
    online = cell("lost_ack", "ack_lost_once", "online")
    assert online["outcome"] == "safe_completion" and online["duplicate_effects"] == 0
    assert cell("lost_ack", "ack_lost_once", "naive_retry")["duplicate_effects"] > 0
    unreconciled = cell("lost_ack", "ack_lost_always", "no_reconcile")
    assert unreconciled["uncertain_effects"] > 0 and unreconciled["outcome"] != "safe_completion"


def test_revocation_of_verified_source_evidence() -> None:
    assert cell("revocation", "commit_change_early", "online")["safe"] is False
    assert cell("revocation", "revocable_change_early", "online")["outcome"] == "safe_completion"


def test_contention_with_frequent_faults_completes() -> None:
    row = cell("contention", "frequent_slow_check", "online")
    assert row["outcome"] == "safe_completion" and row["faults_fired"]


def test_oracle_verifier_mode_is_labeled() -> None:
    row = cell("A", "immediate", "online", verifier="oracle")
    assert row["verifier"] == "oracle" and row["outcome"] == "safe_completion"


def test_quantile_and_mcnemar() -> None:
    assert quantile([1, 2, 3, 4], 0.5) == 2.5
    assert quantile([], 0.5) is None
    pairs = [("c", 1.0, 0.0)] * 10 + [("c", 0.0, 1.0)] * 2 + [("c", 1.0, 1.0)] * 5
    result = mcnemar_exact(pairs)
    assert (result["a_only"], result["b_only"]) == (10, 2)
    assert result["p"] == pytest.approx(0.03857, abs=1e-4)
    assert mcnemar_exact([("c", 1.0, 1.0)])["p"] == 1.0


def test_cluster_bootstrap_resamples_whole_clusters() -> None:
    pairs = [("x", 1.0, 0.0)] * 4 + [("y", 0.0, 0.0)] * 4
    result = cluster_bootstrap(pairs, iterations=500, seed=1)
    assert result["diff"] == pytest.approx(0.5)
    assert result["clusters"] == 2
    assert 0.0 <= result["low"] <= result["diff"] <= result["high"] <= 1.0
    assert cluster_bootstrap(pairs, iterations=500, seed=1) == result


def _row(policy: str, seed: int, outcome: str, condition: str = "fault", family: str = "A") -> dict:
    return {
        "family": family, "template": "dev", "cluster": f"{family}:dev", "schedule": "s", "seed": seed,
        "policy": policy, "condition": condition, "outcome": outcome, "safe": outcome != "correct_but_unsafe",
        "makespan": 10.0 + seed, "tokens": 100, "obligations": [{"opened": 1.0, "discharged": 3.0}],
    }


def test_crsr_safe_uses_the_common_clean_population() -> None:
    rows = [
        _row("a", 0, "safe_completion", "clean"), _row("b", 0, "safe_completion", "clean"),
        _row("a", 1, "safe_completion", "clean"), _row("b", 1, "timeout", "clean"),
        _row("a", 0, "safe_completion"), _row("b", 0, "safe_but_incomplete"),
        _row("a", 1, "safe_completion"), _row("b", 1, "safe_completion"),
    ]
    assert common_clean_population(rows) == {("A", "dev", 0)}
    result = crsr_safe(rows, ["a", "b"])
    assert result["a"] == {"n": 1, "crsr_safe": 1.0}
    assert result["b"] == {"n": 1, "crsr_safe": 0.0}
    summary = summarize(rows)
    assert summary["a"]["n"] == 2 and summary["a"]["restore_p50"] == 2.0
    comparison = compare(rows, "a", "b", iterations=200)
    assert comparison["safe_completion"]["mcnemar"]["a_only"] == 1


def test_suite_runner_freezes_its_manifest_and_resumes(tmp_path) -> None:
    config = SuiteConfig("tiny", ("A",), ("online", "suffix"), (0,), schedules="core")
    out = tmp_path / "tiny"
    first = run_suite(config, out, progress=False)
    assert first["ran"] == 4 * 2 + 2 and first["infrastructure_errors"] == 0
    rows = load_rows(out)
    assert {row["condition"] for row in rows} == {"fault", "clean"}
    saved = json.loads((out / "manifest.json").read_text())
    assert saved["config_sha256"] == manifest(config)["config_sha256"]
    assert saved["frozen"]["policies"]["online"]["invalidation"] is True
    assert run_suite(config, out, progress=False)["skipped"] == first["ran"]
    changed = SuiteConfig("tiny", ("A",), ("online",), (0,), schedules="core")
    with pytest.raises(RuntimeError):
        run_suite(changed, out, progress=False)


def test_presets_match_the_plan() -> None:
    mechanism = preset("mechanism")
    case_count = sum(len(cases.core_schedules(cases.build(f, "dev"))) for f in mechanism.families)
    assert len(mechanism.families) == 6 and case_count == 24
    assert case_count * len(mechanism.seeds) * len(mechanism.policies) == 1680
    assert set(preset("boundary").families) == set(cases.BOUNDARY_FAMILIES)



@pytest.mark.parametrize("policy", ["restart", "suffix", "online"])
def test_activation_trigger_sees_every_kind_of_repair_activation(policy: str) -> None:
    row = cell("PR_disjoint_2", "during_activation", policy, horizon=400, correction_budget=3)
    assert row["outcome"] == "safe_completion" and not row["stalled"]
    assert all(not item["fallback"] for item in row["realized_ordering"].values())


def test_a_pending_scheduled_release_is_not_a_stall() -> None:
    from agentrec.mechanism.spec import Trigger

    case = cases.build("PR_disjoint_2", "dev")
    base = case.schedule("simultaneous")
    slow = type(base)(base.name, base.faults, {
        **base.triggers, "first": Trigger("after_event", delay=0.0, event="never_happens", claim="x", max_wait=45.0),
    })
    row = run_cell(case, slow, 0, policies.get("online"), horizon=400, correction_budget=3).row
    assert not row["stalled"] and row["outcome"] == "safe_completion"
    assert row["realized_ordering"]["first"]["fallback"]


def test_a_slow_real_model_call_is_not_a_stall() -> None:
    import asyncio
    import dataclasses

    from agentrec.mechanism.spec import reference

    case = cases.build("PR_disjoint_2", "dev")
    schedule = case.schedule("simultaneous")
    expected = reference(case, schedule, 0)
    case.reference_factory = lambda _schedule, _seed: expected

    def slow(step):
        async def delayed(values, ctx):
            await asyncio.sleep(45.0)
            return step.compute(values, ctx)

        # Checks recompute synchronously (no context, or attempt number 0); agent attempts are slow.
        def compute(values, ctx):
            return delayed(values, ctx) if ctx is not None and ctx.attempt_number >= 1 else step.compute(values, ctx)
        return dataclasses.replace(step, compute=compute)

    case.steps = tuple(slow(step) if step.task_id == "response" else step for step in case.steps)
    row = run_cell(case, schedule, 0, policies.get("online"), clean=True, horizon=400).row
    assert not row["stalled"] and row["outcome"] == "safe_completion"


def test_activation_trigger_ignores_repairs_of_other_claims() -> None:
    from types import SimpleNamespace

    from agentrec.mechanism.harness import VerdictHolds
    from agentrec.mechanism.spec import Trigger

    events: list = []
    monitor = SimpleNamespace(graph=SimpleNamespace(events=events))
    holds = VerdictHolds(None, None, 0, None, None)
    trigger = Trigger("after_event", event="activation", claim="a")

    def add(kind: str, **payload) -> bool:
        events.append(SimpleNamespace(event_type=kind, payload=payload))
        return holds._event_seen(monitor, trigger)

    assert not add("obligation_opened", claim_id="a", version=1)
    assert not add("obligation_opened", claim_id="b", version=1)
    assert not add("correction_published", claim_id="b", version=2)
    assert not add("recovery_round", claims=["b"], plan=1)
    assert not add("plan_activated", version=2)
    assert not add("recovery_round", claims=["a"], plan=2)
    assert add("plan_activated", version=3)
    events.clear()
    assert not add("obligation_opened", claim_id="a", version=1)
    assert add("correction_published", claim_id="a", version=2)
