"""Unit tests for the runtime pieces the mechanism suite builds on."""

from __future__ import annotations

import asyncio

import pytest

from agentrec.action_contracts import (
    ActionContract,
    ContractContext,
    ContractRegistry,
    GatewayViolation,
    ToolGateway,
    applicable_report,
)
from agentrec.contract import ArtifactRef, Evidence, EvidenceRef, ProposedAction, applies
from agentrec.effects import (
    ConflictError,
    ConditionalStore,
    EffectLedger,
    EffectPhase,
    UncertainOutcome,
    idempotency_key,
)
from agentrec.fault_schedule import FaultSchedule, FaultSpec, logical_task
from agentrec.instrumentation import TimedLock, VirtualClock
from agentrec import policies


def _action(action_id: str = "a1", tool: str = "charge", **arguments) -> ProposedAction:
    return ProposedAction(action_id, tool, dict(arguments or {"amount": 5}), (EvidenceRef("price", 1),), "buyer:attempt:1")


def test_logical_task_strips_every_visit_suffix() -> None:
    assert logical_task("research") == "research"
    assert logical_task("research:2") == "research"
    assert logical_task("research:2:3") == "research"
    assert logical_task("total:2:2") == "total"


def test_fault_persistence_modes() -> None:
    transient = FaultSpec("t", "research", "date", lambda v: v + 1, "transient")
    until = FaultSpec("u", "research", "other", lambda v: v + 1, "until_detected")
    schedule = FaultSchedule([transient, until])
    out, fired = schedule.apply("research", "research:attempt:1", {"date": 1, "other": 1})
    assert out == {"date": 2, "other": 2} and sorted(fired) == ["t", "u"]
    out, fired = schedule.apply("research:2", "research:2:attempt:1", {"date": 1, "other": 1})
    assert out == {"date": 1, "other": 2} and fired == ["u"]
    assert schedule.detected({"other"}) == ["u"]
    out, fired = schedule.apply("research:3", "research:3:attempt:1", {"date": 1, "other": 1})
    assert out == {"date": 1, "other": 1} and fired == []


def test_conditional_store_cas_idempotency_and_lost_acks() -> None:
    store = ConditionalStore({"stock": 1})
    _value, version = store.read("stock")
    store.write("stock", lambda v: v - 1, operation="sell", expected_version=version, idempotency_key="k1")
    with pytest.raises(ConflictError):
        store.write("stock", lambda v: v - 1, operation="sell", expected_version=version, idempotency_key="k2")
    again = store.write("stock", lambda v: v - 1, operation="sell", idempotency_key="k1")
    assert again["duplicate_suppressed"] and store.read("stock")[0] == 0

    store.lose_ack["charge"] = 1
    with pytest.raises(UncertainOutcome):
        store.write("card", lambda v: (v or 0) + 5, operation="charge", idempotency_key=idempotency_key("pay"))
    assert store.lookup(idempotency_key("pay"))["ok"]
    store.external_write("stock", 9)
    assert store.read("stock") == (9, 3) and store.history[-1].external


def test_effect_ledger_counts_duplicates_and_uncertain_outcomes() -> None:
    ledger = EffectLedger(clock=lambda: (0.0, 0.0))
    first = _action("a1")
    second = _action("a2")
    for action in (first, second):
        ledger.append(EffectPhase.DISPATCHED, action, semantic_key="charge:5", resource="card")
        ledger.append(EffectPhase.COMMITTED, action, semantic_key="charge:5", resource="card")
    third = _action("a3")
    ledger.append(EffectPhase.DISPATCHED, third, semantic_key="charge:7", resource="card")
    ledger.append(EffectPhase.UNCERTAIN, third, semantic_key="charge:7", resource="card")
    assert ledger.physical_calls() == 3
    assert ledger.duplicates() == {"charge:5": 2}
    assert ledger.uncertain() == ["a3"]
    assert [row["phase"] for row in ledger.rows()][:2] == ["dispatched", "committed"]


def test_gateway_refuses_direct_mutation() -> None:
    calls = []
    gateway = ToolGateway(
        {"lookup": lambda **kw: {"ok": True}, "charge": lambda args, **kw: calls.append(args)},
        ContractRegistry(),
        read_only={"lookup"},
    )
    assert gateway.read("lookup", id=1) == {"ok": True}
    with pytest.raises(GatewayViolation):
        gateway.read("charge", amount=5)
    assert gateway.violations == ["charge"]
    asyncio.run(gateway.execute(_action(), idempotency_key="k", preconditions={}))
    assert calls == [{"amount": 5}]


def test_applicable_report_binds_release_to_the_tested_artifact() -> None:
    validate = applicable_report("patch", "report", extra=(ArtifactRef("suite", "s1"),))
    tested = ArtifactRef("patch", "sha-a")
    report = Evidence(
        "report", 1, {"passed": True}, "tester:attempt:1", (),
        applies_to=(tested, ArtifactRef("suite", "s1")),
    )
    assert validate({"patch": "sha-a"}, [report], ContractContext()).ok
    assert not validate({"patch": "sha-b"}, [report], ContractContext()).ok
    assert validate({"patch": "sha-b"}, [report], ContractContext(enforce_applicability=False)).ok
    assert applies((tested,), (tested,)) and not applies((tested,), (ArtifactRef("patch", "sha-b"),))


def test_contract_registry_defaults() -> None:
    registry = ContractRegistry([ActionContract("ship", lambda a, e, c: None, irreversible=True)])
    assert registry.irreversible("ship")
    assert registry.resource("ship", {"order": 1})
    assert registry.semantic_key("ship", {"order": 1}) != registry.semantic_key("ship", {"order": 2})


def test_virtual_clock_skips_sleeps_and_timed_lock_records_waits() -> None:
    clock = VirtualClock()

    async def body() -> tuple[float, dict]:
        loop = asyncio.get_running_loop()
        lock = TimedLock()
        start = loop.time()

        async def hold() -> None:
            async with lock:
                await asyncio.sleep(5)

        await asyncio.gather(hold(), hold())
        return loop.time() - start, lock.stats()

    elapsed, stats = clock.run(body())
    assert elapsed == pytest.approx(10.0)
    # Lock statistics are real time (monitor overhead), so virtual sleeps do not inflate them.
    assert stats["acquisitions"] == 2 and stats["wait_s"] < 1.0


def test_policy_registry_groups_and_ablation_axes() -> None:
    core = {config.name for config in policies.select("core")}
    assert core == {"restart", "suffix", "coarse", "claim_rerun", "online", "verify_before", "advisory"}
    two_by_two = {
        (config.invalidation, config.action_gate)
        for config in policies.select("online,inv_on_gate_off,inv_off_gate_on,inv_off_gate_off")
    }
    assert two_by_two == {(True, True), (True, False), (False, True), (False, False)}
    assert policies.get("no_cancel").fencing and not policies.get("no_cancel").cancellation
    assert policies.get("online_cas").effect_consistency == "cas"
    for config in policies.select("extended"):
        assert config.adaptation_of or config.name == "posthoc_repair"
