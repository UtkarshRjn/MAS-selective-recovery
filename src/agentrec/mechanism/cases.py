"""Mechanism and boundary scenarios.

Families A-F are the mechanism suite; G, H, lost acknowledgments, verified
source revocation, and the no-fault / high-contention controls are the
boundary suite. Every family has a `dev` template used while building the
harness and a held-out `test` template with a different graph shape or
intervention placement.

Checks are source-linked or derivational and run against the same world for
every policy. A derivational check recomputes a step from the exact input
versions an output cites; it cannot see a fault in those inputs, so detection
stays a property of the faulty source claim.
"""

from __future__ import annotations

import copy
from typing import Any, Callable

from ..action_contracts import ActionContract, all_of, applicable_report, approve, cited, field_matches, reject, typed
from ..contract import ArtifactRef, Evidence, EvidenceStatus, content_digest
from ..fault_schedule import FaultSpec
from ..verifiers import CheckContext, GlobalInvariant, applicable_report_check, failed, live_versions, passed, pending
from .spec import ActionSpec, CaseSpec, Schedule, StepContext, StepSpec, Trigger, World, effect_tool, read_tool

# ----------------------------------------------------------------- helpers


def source_check(source: str, key: str | None = None, project: Callable[[Any], Any] | None = None):
    def check(evidence: Evidence, ctx: CheckContext):
        world: World = ctx.tools
        record = world.source(source)
        expected = record if key is None else record.get(key)
        value = evidence.value if project is None else project(evidence.value)
        if value == expected:
            return passed(evidence)
        return failed(evidence, f"{evidence.claim_id} disagrees with source {source}")

    return check


def derivation_check(case_ref: list[CaseSpec], task_id: str):
    """Recompute the step from the cited input versions and compare."""

    def check(evidence: Evidence, ctx: CheckContext):
        case = case_ref[0]
        step = case.step(task_id)
        values: dict[str, Any] = {}
        found: dict[str, Evidence] = {}
        for ref in evidence.supporting_evidence:
            match = [item for item in ctx.versions(ref.claim_id) if item.version == ref.version]
            if match:
                found[ref.claim_id] = match[0]
                values[ref.claim_id] = copy.deepcopy(match[0].value)
        for claim_id in step.all_reads:
            if claim_id not in values:
                live = live_versions(ctx, claim_id)
                if not live:
                    return pending(claim_id)
                latest = max(live, key=lambda item: item.version)
                found[claim_id] = latest
                values[claim_id] = copy.deepcopy(latest.value)
        produced = step.compute(values, StepContext(ctx.tools, 0, found, case.identity))
        if produced.get(evidence.claim_id) == evidence.value:
            return passed(evidence)
        return failed(evidence, f"{evidence.claim_id} does not follow from its cited inputs")

    return check


def _derived_checks(case_ref: list[CaseSpec], sources: dict[str, Callable]) -> Callable[[World], dict[str, Any]]:
    def build(_world: World) -> dict[str, Any]:
        case = case_ref[0]
        checks = dict(sources)
        for step in case.steps:
            for claim_id in step.writes:
                checks.setdefault(claim_id, derivation_check(case_ref, step.task_id))
        return checks

    return build


def lookup_tools(extra: Callable[[World], dict[str, Callable]] | None = None):
    def build(world: World) -> dict[str, Callable]:
        tools: dict[str, Callable] = {"lookup": read_tool(lambda name: world.source(name))}
        if extra is not None:
            tools.update(extra(world))
        return tools

    return build


def append_to(key: str):
    def update(before: Any, arguments: dict[str, Any]) -> Any:
        items = list(before or [])
        items.append(dict(arguments))
        return items

    return update


def shift_day(value: Any) -> Any:
    year, month, day = str(value).split("-")
    return f"{year}-{month}-{int(day) + 1:02d}"


def matches_cited(argument: str, claim_id: str, key: str):
    return field_matches(argument, claim_id, key)


def ok_schedules(prefix_faults: tuple[str, ...], fault: str, *, inflight: bool = True) -> tuple[Schedule, ...]:
    """The four timing schedules shared by single-fault families."""
    return (
        Schedule("immediate", prefix_faults, {fault: Trigger("delay", delay=0.0)}),
        Schedule("inflight", prefix_faults, {fault: Trigger("timeline", point="inflight" if inflight else "after_draft")}),
        Schedule("pre_commit", prefix_faults, {fault: Trigger("timeline", point="pre_commit")}),
        Schedule("late", prefix_faults, {fault: Trigger("timeline", point="late")}),
    )


def email_contract(fields: dict[str, tuple[str, str]], recipient_claim: str, recipient_key: str) -> ActionContract:
    """`fields` maps an argument to the (claim, key) it must equal."""

    def validate(arguments, evidence, ctx):
        verdict = matches_cited("to", recipient_claim, recipient_key)(arguments, evidence, ctx)
        if not verdict.ok:
            return verdict
        for argument, (claim_id, key) in fields.items():
            verdict = matches_cited(argument, claim_id, key)(arguments, evidence, ctx)
            if not verdict.ok:
                return verdict
        return approve(arguments)

    return ActionContract(
        "send_email", validate,
        resource=lambda args: f"mailbox:{args.get('to')}",
        semantic_key=lambda args: f"email:{args.get('to')}:{args.get('subject')}",
        irreversible=True,
    )


# ------------------------------------------------------------------ case A


def case_a(template: str = "dev") -> CaseSpec:
    """Semantic claim without a storage conflict: a wrong arrival date propagates."""
    test = template == "test"
    sources = {
        "booking": {"address": "12 Harbor Rd" if not test else "4 Quay St", "arrival": "2026-03-14" if not test else "2026-05-02"},
        "flights": {"flight": "UA12" if not test else "BA88", "lands": "14:05" if not test else "09:40"},
        "traveler": {"email": "ana@example.com" if not test else "li@example.com"},
        "visa": {"needed": False},
    }
    ref: list[CaseSpec] = []

    def research(_v, ctx):
        booking = ctx.world.source("booking")
        return {"address": booking["address"], "arrival_date": booking["arrival"]}

    steps = [
        StepSpec("research", "researcher", ("address", "arrival_date"), research, duration=4),
        StepSpec("flights", "flight_agent", ("flight",), lambda _v, ctx: {"flight": dict(ctx.world.source("flights"))},
                 duration=9 if not test else 12, tokens=(900, 200)),
        StepSpec("directions", "directions_agent", ("directions",),
                 lambda v, _c: {"directions": f"route to {v['address']}"}, reads=("address",), duration=7),
        StepSpec("transfer", "transfer_agent", ("transfer",),
                 lambda v, _c: {"transfer": {"date": v["arrival_date"], "pickup": "arrivals hall"}},
                 reads=("arrival_date",), duration=5),
        StepSpec("planner", "planner", ("itinerary",),
                 lambda v, _c: {"itinerary": {"date": v["transfer"]["date"], "flight": v["flight"]["flight"],
                                              "lands": v["flight"]["lands"]}},
                 reads=("transfer", "flight"), duration=6, tokens=(1200, 400)),
        StepSpec("notify", "notifier", ("notice",),
                 lambda v, ctx: {"notice": {"to": ctx.world.source("traveler")["email"], "date": v["itinerary"]["date"]}},
                 reads=("itinerary",), duration=2,
                 actions=(ActionSpec("send_email", lambda v, ctx: {
                     "to": v["notice"]["to"], "subject": "itinerary",
                     "date": v["itinerary"]["date"], "flight": v["itinerary"]["flight"],
                 }),)),
    ]
    if test:
        steps.append(StepSpec("visa", "visa_agent", ("visa",), lambda _v, ctx: {"visa": ctx.world.source("visa")},
                              duration=14, tokens=(1500, 300)))

    def checks_sources():
        return {
            "address": source_check("booking", "address"),
            "arrival_date": source_check("booking", "arrival"),
            "flight": source_check("flights"),
            "visa": source_check("visa"),
        }

    def contracts():
        return [email_contract({"date": ("itinerary", "date"), "flight": ("itinerary", "flight")}, "notice", "to")]

    def tools(world: World):
        return {"send_email": effect_tool(world, "send_email", lambda a: f"mailbox:{a['to']}", append_to("mail"))}

    def corrector(claim_id: str, world: World, _values):
        booking = world.source("booking")
        return {"address": booking["address"], "arrival_date": booking["arrival"]}.get(claim_id)

    faults = (
        FaultSpec("date", "research", "arrival_date", shift_day, "transient", "arrival date shifted by one day"),
        FaultSpec("date_persistent", "research", "arrival_date", shift_day, "persistent", "source agent keeps misreading the date"),
    )
    schedules = ok_schedules(("date",), "date") + (
        Schedule("persistent", ("date_persistent",), {"date_persistent": Trigger("timeline", point="inflight")},
                 params={"core": False}),
        Schedule("no_fault", (), {}, params={"core": False}),
        Schedule("zero_delay", ("date",), {"date": Trigger("delay", delay=-1.0)}, params={"core": False}),
    )
    case = CaseSpec(
        "A", template, "Semantic date fault with no storage conflict; address and flight work is independent.",
        tuple(steps), ("itinerary", "directions", "notice", "flight") + (("visa",) if test else ()),
        sources, {}, faults, schedules,
        _derived_checks(ref, checks_sources()), contracts, lookup_tools(tools), corrector,
        budgets=(25.0, 35.0, 50.0),
    )
    ref.append(case)
    return case


# ------------------------------------------------------------------ case B


ENV = ArtifactRef("env", content_digest("python3.12+deps@lock-7"))
SUITE = ArtifactRef("suite", content_digest("tests/test_keys.py@r3"))


def run_tests(patch: dict[str, Any]) -> bool:
    """The deterministic test tool: a mutated patch fails the suite."""
    return not patch.get("mutated", False)


def case_b(template: str = "dev") -> CaseSpec:
    """A true test report for P1 must not approve P2."""
    test = template == "test"
    sources = {
        "spec_a": {"module": "keys.py", "change": "hashkey handles kwargs", "mutated": False},
        "spec_b": {"module": "keys.py", "change": "typedkey distinguishes int/float" if not test else "methodkey skips self",
                   "mutated": False},
    }
    ref: list[CaseSpec] = []

    def report_for(claim_id: str):
        def compute(v, ctx):
            patch = v[claim_id]
            return {"report": {"artifact": ctx.evidence[claim_id].digest, "claim": claim_id, "passed": run_tests(patch)}}
        return compute

    def review(v, ctx):
        report = v["report"]
        return {"review": {"approve": bool(report["passed"]), "artifact": ctx.evidence["patch_b"].digest,
                           "report_for": report["artifact"]}}

    steps = (
        StepSpec("coder_a", "coder", ("patch_a",), lambda _v, ctx: {"patch_a": dict(ctx.world.source("spec_a"), base="main")},
                 duration=4, tokens=(2000, 900)),
        StepSpec("tester_a", "tester", ("report",), report_for("patch_a"), reads=("patch_a",), duration=12,
                 applies={"report": "patch_a"}, goal=True, checker_step=True),
        StepSpec("coder_b", "coder", ("patch_b",),
                 lambda v, ctx: {"patch_b": dict(ctx.world.source("spec_b"), base=content_digest(v["patch_a"]))},
                 reads=("patch_a",), duration=6, tokens=(2200, 1000)),
        StepSpec("tester_b", "tester", ("report",), report_for("patch_b"), reads=("patch_b",), duration=8,
                 applies={"report": "patch_b"}, goal=True, checker_step=True),
        StepSpec("reviewer", "reviewer", ("review",), review, reads=("patch_b",), late_reads=("report",),
                 bind={"report": "patch_b"}, duration=3, tokens=(1800, 300)),
        StepSpec("release", "release_agent", ("released",),
                 lambda v, ctx: {"released": {"artifact": ctx.evidence["patch_b"].digest if v["review"]["approve"] else None}},
                 reads=("review", "patch_b"), late_reads=("report",), bind={"report": "patch_b"}, duration=1,
                 actions=(ActionSpec("merge", lambda v, ctx: (
                     {"artifact": ctx.evidence["patch_b"].digest, "module": v["patch_b"]["module"]}
                     if v["review"]["approve"] else None)),)),
    )

    def report_check(evidence: Evidence, ctx: CheckContext):
        value = evidence.value
        candidates = [item for item in ctx.versions(value["claim"]) if item.digest == value["artifact"]]
        if not candidates:
            return failed(evidence, "report names an unknown artifact")
        if run_tests(candidates[0].value) == value["passed"]:
            return passed(evidence)
        return failed(evidence, "report disagrees with a rerun of the suite")

    def checks(world: World):
        out = _derived_checks(ref, {
            "patch_a": applicable_report_check("report", extra=(ENV, SUITE)),
            "patch_b": applicable_report_check("report", extra=(ENV, SUITE)),
            "report": report_check,
        })(world)
        return out

    def contracts():
        def approved(arguments, evidence, _ctx):
            review_ev = cited(evidence, "review")
            if review_ev is None or not review_ev.value.get("approve"):
                return reject("release cites no approving review")
            if review_ev.value.get("artifact") != arguments.get("artifact"):
                return reject("review approved a different artifact")
            return approve(arguments)

        return [ActionContract(
            "merge",
            all_of(typed({"artifact": str, "module": str}), approved, applicable_report("artifact", "report", extra=(ENV, SUITE))),
            resource=lambda args: f"branch:main:{args.get('module')}",
            semantic_key=lambda args: f"merge:{args.get('module')}",
            irreversible=True,
        )]

    def tools(world: World):
        return {"merge": effect_tool(world, "merge", lambda a: f"branch:main:{a['module']}", lambda _b, a: dict(a))}

    def corrector(claim_id: str, world: World, values):
        if claim_id == "patch_b":
            base = values.get("patch_a")
            return dict(world.source("spec_b"), base=content_digest(base) if base is not None else "")
        if claim_id == "patch_a":
            return dict(world.source("spec_a"), base="main")
        return None

    def mutate(patch: Any) -> Any:
        return dict(patch, mutated=True)

    faults = (FaultSpec("mutation", "coder_b", "patch_b", mutate, "transient", "P2 carries a semantic mutation"),)
    schedules = (
        Schedule("p1_late", ("mutation",), {}, durations={"tester_a": 12.0 if not test else 15.0, "tester_b": 8.0}),
        Schedule("p1_early", ("mutation",), {}, durations={"tester_a": 3.0, "tester_b": 8.0}),
        Schedule("p2_slow", ("mutation",), {}, durations={"tester_a": 12.0, "tester_b": 20.0}),
        Schedule("p1_after_p2", ("mutation",), {}, durations={"tester_a": 24.0, "tester_b": 6.0}),
    )
    case = CaseSpec(
        "B", template, "A passing report for P1 is historically true but does not apply to P2.",
        steps, ("review", "released"), sources, {}, faults, schedules,
        checks, contracts, lookup_tools(tools), corrector, identity=(ENV, SUITE), eager_claims=("report",),
    )
    ref.append(case)
    return case


# ------------------------------------------------------------------ case C


def case_c(template: str = "dev") -> CaseSpec:
    """Late eligibility fault at a join, before a refund and an email leave the runtime."""
    test = template == "test"
    sources = {
        "account": {"customer": "C-17" if not test else "C-204", "email": "kim@example.com" if not test else "raj@example.com"},
        "order": {"order": "O-9" if not test else "O-311", "eligible": False, "amount": 80 if not test else 145},
        "delivery": {"address": "9 Elm St" if not test else "77 Pine Ave", "delivered": True},
    }
    ref: list[CaseSpec] = []

    def decide(v, _ctx):
        refund = bool(v["eligibility"]["eligible"]) and v["delivery"]["delivered"]
        return {"decision": {"refund": refund, "amount": v["eligibility"]["amount"] if refund else 0,
                             "order": v["eligibility"]["order"], "customer": v["customer"]["customer"]}}

    def draft(v, _ctx):
        verdict = "approved" if v["decision"]["refund"] else "not eligible"
        return {"email_draft": {"to": v["customer"]["email"], "subject": f"order {v['decision']['order']}",
                                "body": f"Your refund request is {verdict}."}}

    steps = [
        StepSpec("identity", "identity_agent", ("customer",), lambda _v, ctx: {"customer": ctx.world.source("account")}, duration=3),
        StepSpec("eligibility", "policy_agent", ("eligibility",), lambda _v, ctx: {"eligibility": ctx.world.source("order")}, duration=5),
        StepSpec("delivery", "logistics_agent", ("delivery",), lambda _v, ctx: {"delivery": ctx.world.source("delivery")}, duration=6),
        StepSpec("decide", "resolution_agent", ("decision",), decide, reads=("customer", "eligibility", "delivery"),
                 duration=5, tokens=(1400, 350)),
        StepSpec("draft", "writer", ("email_draft",), draft, reads=("decision", "customer"), duration=4, tokens=(900, 400)),
        StepSpec("send", "dispatcher", ("sent",), lambda v, _c: {"sent": {"order": v["decision"]["order"]}},
                 reads=("email_draft", "decision"), duration=1,
                 actions=(
                     ActionSpec("issue_refund", lambda v, _c: ({"order": v["decision"]["order"], "amount": v["decision"]["amount"]}
                                                              if v["decision"]["refund"] else None)),
                     ActionSpec("send_email", lambda v, _c: dict(v["email_draft"])),
                 )),
    ]
    if test:
        steps.insert(3, StepSpec("history", "history_agent", ("history",),
                                 lambda _v, ctx: {"history": {"customer": ctx.world.source("account")["customer"], "tickets": 2}},
                                 duration=10, tokens=(1600, 300)))

    def checks_sources():
        return {
            "customer": source_check("account"),
            "eligibility": source_check("order"),
            "delivery": source_check("delivery"),
            "history": lambda ev, ctx: passed(ev),
        }

    def contracts():
        def refund_ok(arguments, evidence, _ctx):
            decision = cited(evidence, "decision")
            if decision is None or not decision.value.get("refund"):
                return reject("no verified decision grants a refund")
            if arguments.get("amount") != decision.value.get("amount") or arguments.get("order") != decision.value.get("order"):
                return reject("refund differs from the decision")
            return approve(arguments)

        def email_ok(arguments, evidence, _ctx):
            source = cited(evidence, "email_draft")
            if source is None or dict(source.value) != dict(arguments):
                return reject("email differs from the verified draft")
            return approve(arguments)

        return [
            ActionContract("issue_refund", refund_ok, resource=lambda a: f"order:{a.get('order')}",
                           semantic_key=lambda a: f"refund:{a.get('order')}",
                           compensate=lambda a: ("reverse_refund", dict(a))),
            ActionContract("send_email", email_ok, resource=lambda a: f"mailbox:{a.get('to')}",
                           semantic_key=lambda a: f"email:{a.get('to')}:{a.get('subject')}", irreversible=True),
            ActionContract("reverse_refund", lambda a, _e, _c: approve(a), resource=lambda a: f"order:{a.get('order')}",
                           semantic_key=lambda a: f"reverse:{a.get('order')}"),
        ]

    def tools(world: World):
        return {
            "issue_refund": effect_tool(world, "issue_refund", lambda a: f"order:{a['order']}", append_to("refunds")),
            "send_email": effect_tool(world, "send_email", lambda a: f"mailbox:{a['to']}", append_to("mail")),
            "reverse_refund": effect_tool(world, "reverse_refund", lambda a: f"order:{a['order']}", append_to("reversals")),
        }

    def corrector(claim_id: str, world: World, _values):
        return {"customer": world.source("account"), "eligibility": world.source("order"),
                "delivery": world.source("delivery")}.get(claim_id)

    faults = (FaultSpec("eligibility", "eligibility", "eligibility", lambda v: dict(v, eligible=True), "transient",
                        "order wrongly marked refund-eligible"),)
    schedules = (
        Schedule("immediate", ("eligibility",), {"eligibility": Trigger("delay", delay=0.0)}),
        Schedule("after_draft", ("eligibility",), {"eligibility": Trigger("timeline", point="after_draft")}),
        Schedule("pre_commit", ("eligibility",), {"eligibility": Trigger("timeline", point="pre_commit")}),
        Schedule("late", ("eligibility",), {"eligibility": Trigger("timeline", point="late")}),
    )
    case = CaseSpec(
        "C", template, "Wrong eligibility reaches the refund decision and email draft at a join.",
        tuple(steps), ("decision", "email_draft", "sent"), sources, {}, faults, schedules,
        _derived_checks(ref, checks_sources()), contracts, lookup_tools(tools), corrector,
    )
    ref.append(case)
    return case


# ------------------------------------------------------------------ case D


def case_d(template: str = "dev") -> CaseSpec:
    """One bad sibling claim while an expensive independent branch is running."""
    test = template == "test"
    sources = {
        "supplier": {"delivery_date": "2026-04-03" if not test else "2026-06-11", "warehouse": "WH-North" if not test else "WH-Delta"},
        "regulations": {"region": "EU", "rules": 14},
    }
    ref: list[CaseSpec] = []

    def supplier(_v, ctx):
        record = ctx.world.source("supplier")
        return {"delivery_date": record["delivery_date"], "warehouse": record["warehouse"]}

    steps = (
        StepSpec("supplier", "supplier_agent", ("delivery_date", "warehouse"), supplier, duration=4),
        StepSpec("routing", "routing_agent", ("route",), lambda v, _c: {"route": f"lane via {v['warehouse']}"},
                 reads=("warehouse",), duration=8, tokens=(1200, 300)),
        StepSpec("procurement", "procurement_agent", ("procurement",),
                 lambda v, _c: {"procurement": {"order_by": v["delivery_date"], "qty": 40}},
                 reads=("delivery_date",), duration=6, tokens=(1000, 300)),
        StepSpec("compliance", "compliance_agent", ("compliance",),
                 lambda _v, ctx: {"compliance": {"cleared": True, "rules": ctx.world.source("regulations")["rules"]}},
                 duration=30 if not test else 34, tokens=(6000, 2500)),
        StepSpec("summary", "planner", ("summary",),
                 lambda v, _c: {"summary": {"route": v["route"], "order_by": v["procurement"]["order_by"],
                                            "cleared": v["compliance"]["cleared"]}},
                 reads=("route", "procurement", "compliance"), duration=3,
                 actions=(ActionSpec("submit_po", lambda v, _c: {"order_by": v["summary"]["order_by"],
                                                                  "route": v["summary"]["route"]}),)),
    )

    def checks_sources():
        return {"delivery_date": source_check("supplier", "delivery_date"), "warehouse": source_check("supplier", "warehouse")}

    def contracts():
        def po_ok(arguments, evidence, _ctx):
            summary = cited(evidence, "summary")
            if summary is None or summary.value.get("order_by") != arguments.get("order_by"):
                return reject("purchase order differs from the verified summary")
            return approve(arguments)

        return [ActionContract("submit_po", po_ok, resource=lambda a: "po:main", semantic_key=lambda a: "po:main",
                               irreversible=True)]

    def tools(world: World):
        return {"submit_po": effect_tool(world, "submit_po", lambda a: "po:main", lambda _b, a: dict(a))}

    def corrector(claim_id: str, world: World, _values):
        return world.source("supplier").get(claim_id)

    faults = (FaultSpec("date", "supplier", "delivery_date", shift_day, "transient", "supplier date off by one day"),)
    schedules = (
        Schedule("immediate", ("date",), {"date": Trigger("delay", delay=0.0)}),
        Schedule("t20", ("date",), {"date": Trigger("at", delay=20.0)}),
        Schedule("inflight", ("date",), {"date": Trigger("timeline", point="inflight")}),
        Schedule("late", ("date",), {"date": Trigger("timeline", point="late")}),
    )
    case = CaseSpec(
        "D", template, "Late date check while a 30-second independent compliance branch runs.",
        steps, ("route", "procurement", "compliance", "summary"), sources, {}, faults, schedules,
        _derived_checks(ref, checks_sources()), contracts, lookup_tools(tools), corrector,
        budgets=(40.0, 50.0, 70.0), token_budgets=(20000, 30000, 45000),
    )
    ref.append(case)
    return case


# ------------------------------------------------------------------ case E


def case_e(template: str = "dev") -> CaseSpec:
    """Two independent faults inside one recovery window."""
    test = template == "test"
    sources = {
        "flight": {"arrival_time": "14:05" if not test else "07:50"},
        "contact": {"recipient": "ops@example.com" if not test else "desk@example.com"},
        "hotel": {"hotel": "Harbor Inn" if not test else "Kite Hotel", "checkin": "15:00"},
        "passenger": {"name": "A. Rivera"},
    }
    ref: list[CaseSpec] = []

    def add_minutes(value: Any) -> Any:
        hour, minute = str(value).split(":")
        total = int(hour) * 60 + int(minute) + 95
        return f"{total // 60 % 24:02d}:{total % 60:02d}"

    steps = (
        StepSpec("flight_src", "flight_agent", ("arrival_time",),
                 lambda _v, ctx: {"arrival_time": ctx.world.source("flight")["arrival_time"]}, duration=4),
        StepSpec("contact_src", "crm_agent", ("recipient",),
                 lambda _v, ctx: {"recipient": ctx.world.source("contact")["recipient"]}, duration=4),
        StepSpec("hotel_src", "hotel_agent", ("hotel",), lambda _v, ctx: {"hotel": ctx.world.source("hotel")}, duration=5),
        StepSpec("transfer", "transfer_agent", ("transfer",),
                 lambda v, _c: {"transfer": {"pickup": v["arrival_time"], "dropoff": v["hotel"]["hotel"]}},
                 reads=("arrival_time", "hotel"), duration=5),
        StepSpec("itinerary", "planner", ("itinerary",),
                 lambda v, _c: {"itinerary": {"pickup": v["transfer"]["pickup"], "hotel": v["hotel"]["hotel"],
                                              "checkin": v["hotel"]["checkin"]}},
                 reads=("transfer", "hotel"), duration=5, tokens=(1200, 400)),
        StepSpec("notification", "comms_agent", ("greeting",),
                 lambda v, ctx: {"greeting": {"to": v["recipient"], "name": ctx.world.source("passenger")["name"]}},
                 reads=("recipient",), duration=4),
        StepSpec("join", "editor", ("message",),
                 lambda v, _c: {"message": {"to": v["greeting"]["to"], "subject": "trip",
                                            "pickup": v["itinerary"]["pickup"], "hotel": v["itinerary"]["hotel"]}},
                 reads=("itinerary", "greeting"), duration=3),
        StepSpec("send", "dispatcher", ("sent",), lambda v, _c: {"sent": {"to": v["message"]["to"]}},
                 reads=("message",), duration=1,
                 actions=(ActionSpec("send_email", lambda v, _c: dict(v["message"])),)),
    )

    def checks_sources():
        return {
            "arrival_time": source_check("flight", "arrival_time"),
            "recipient": source_check("contact", "recipient"),
            "hotel": source_check("hotel"),
        }

    def contracts():
        def ok(arguments, evidence, _ctx):
            message = cited(evidence, "message")
            if message is None or dict(message.value) != dict(arguments):
                return reject("email differs from the verified message")
            return approve(arguments)

        return [ActionContract("send_email", ok, resource=lambda a: f"mailbox:{a.get('to')}",
                               semantic_key=lambda a: "email:trip", irreversible=True)]

    def tools(world: World):
        return {"send_email": effect_tool(world, "send_email", lambda a: f"mailbox:{a['to']}", append_to("mail"))}

    def corrector(claim_id: str, world: World, _values):
        return {"arrival_time": world.source("flight")["arrival_time"], "recipient": world.source("contact")["recipient"],
                "hotel": world.source("hotel")}.get(claim_id)

    faults = (
        FaultSpec("arrival", "flight_src", "arrival_time", add_minutes, "transient", "arrival time off by 95 minutes"),
        FaultSpec("recipient", "contact_src", "recipient", lambda v: "ops-old@example.com", "transient", "stale recipient"),
        FaultSpec("hotel", "hotel_src", "hotel", lambda v: dict(v, hotel="Harbour Lodge"), "transient", "wrong hotel"),
    )
    second_delay = 1.0
    first = Trigger("timeline", point="inflight")
    schedules = (
        Schedule("disjoint", ("arrival", "recipient"),
                 {"arrival": first, "recipient": Trigger("after_event", delay=second_delay, event="obligation_opened", claim="arrival_time")}),
        Schedule("overlap", ("arrival", "hotel"),
                 {"arrival": first, "hotel": Trigger("after_event", delay=second_delay, event="obligation_opened", claim="arrival_time")}),
        Schedule("simultaneous", ("arrival", "recipient"),
                 {"arrival": Trigger("at", delay=9.0), "recipient": Trigger("at", delay=9.0)}),
        Schedule("during_activation", ("arrival", "recipient"),
                 {"arrival": first, "recipient": Trigger("after_event", delay=0.0, event="activation", claim="arrival_time")}),
    )
    case = CaseSpec(
        "E", template, "Arrival-time and recipient (or hotel) faults overlap in one recovery window.",
        steps, ("itinerary", "message", "sent"), sources, {}, faults, schedules,
        _derived_checks(ref, checks_sources()), contracts, lookup_tools(tools), corrector,
    )
    ref.append(case)
    return case


# ------------------------------------------------------------------ case F


def case_f(template: str = "dev") -> CaseSpec:
    """Late results from cancelled attempts, with cooperative and uncancellable transports."""
    test = template == "test"
    sources = {
        "event": {"date": "2026-07-09" if not test else "2026-08-21", "venue": "Hall 3" if not test else "Pier 9"},
        "weather": {"forecast": "clear"},
    }
    ref: list[CaseSpec] = []

    def source(_v, ctx):
        record = ctx.world.source("event")
        return {"date": record["date"], "venue": record["venue"]}

    steps = (
        StepSpec("source", "researcher", ("date", "venue"), source, duration=2),
        StepSpec("planner", "planner", ("plan",), lambda v, _c: {"plan": {"date": v["date"], "venue": v["venue"]}},
                 reads=("date", "venue"), duration=6, tokens=(2000, 800)),
        StepSpec("weather", "weather_agent", ("weather",), lambda _v, ctx: {"weather": ctx.world.source("weather")}, duration=8),
        StepSpec("booker", "booker", ("booking",), lambda v, _c: {"booking": dict(v["plan"])}, reads=("plan",), duration=1,
                 actions=(ActionSpec("book_venue", lambda v, _c: dict(v["plan"])),)),
    )

    def checks_sources():
        return {"date": source_check("event", "date"), "venue": source_check("event", "venue"), "weather": source_check("weather")}

    def contracts():
        def ok(arguments, evidence, _ctx):
            plan = cited(evidence, "plan")
            if plan is None or dict(plan.value) != dict(arguments):
                return reject("booking differs from the verified plan")
            return approve(arguments)

        return [ActionContract("book_venue", ok, resource=lambda a: f"venue:{a.get('venue')}",
                               semantic_key=lambda a: "book:event", irreversible=True)]

    def tools(world: World):
        return {"book_venue": effect_tool(world, "book_venue", lambda a: f"venue:{a['venue']}", append_to("bookings"))}

    def corrector(claim_id: str, world: World, _values):
        return world.source("event").get(claim_id)

    faults = (FaultSpec("date", "source", "date", shift_day, "transient", "event date off by one day"),)
    verdict = {"date": Trigger("delay", delay=3.0)}
    schedules = (
        Schedule("coop", ("date",), verdict, attempt_durations={("planner", 1): 14.0, ("planner", 2): 6.0}),
        Schedule("uncancellable", ("date",), verdict, attempt_durations={("planner", 1): 14.0, ("planner", 2): 6.0},
                 uncancellable=frozenset({"planner"})),
        Schedule("uncancellable_early", ("date",), verdict, attempt_durations={("planner", 1): 8.0, ("planner", 2): 9.0},
                 uncancellable=frozenset({"planner"})),
        Schedule("coop_late_verdict", ("date",), {"date": Trigger("timeline", point="pre_commit")},
                 attempt_durations={("planner", 1): 6.0}),
    )
    case = CaseSpec(
        "F", template, "Planner on date@1 returns after planner@2; only fencing keeps it out.",
        steps, ("plan", "booking", "weather"), sources, {}, faults, schedules,
        _derived_checks(ref, checks_sources()), contracts, lookup_tools(tools), corrector,
    )
    ref.append(case)
    return case


# ------------------------------------------------------------------ case G


def _minutes(clock: str) -> int:
    hour, minute = clock.split(":")
    return int(hour) * 60 + int(minute)


def case_g(template: str = "dev") -> CaseSpec:
    """A global invariant fails although each claim passes its local check."""
    test = template == "test"
    sources = {
        "flights": {"UA1": {"id": "UA1", "arrive": "22:30"}, "UA2": {"id": "UA2", "arrive": "18:00" if not test else "17:10"}},
        "preference": {"prefer": "UA1", "fallback": "UA2"},
        "hotel": {"checkin_by": "22:00"},
        "transfer": {"minutes": 60 if not test else 75},
    }
    ref: list[CaseSpec] = []

    def feasible(flight: dict, checkin: dict, transfer: dict) -> bool:
        return _minutes(flight["arrive"]) + transfer["minutes"] <= _minutes(checkin["checkin_by"])

    def choose(world: World) -> dict:
        prefs = world.source("preference")
        flights = world.source("flights")
        checkin = world.source("hotel")
        transfer = world.source("transfer")
        for flight_id in (prefs["prefer"], prefs["fallback"]):
            if feasible(flights[flight_id], checkin, transfer):
                return dict(flights[flight_id])
        return dict(flights[prefs["fallback"]])

    steps = (
        StepSpec("flight", "flight_agent", ("flight",), lambda _v, ctx: {"flight": choose(ctx.world)}, duration=4),
        StepSpec("hotel", "hotel_agent", ("checkin",), lambda _v, ctx: {"checkin": ctx.world.source("hotel")}, duration=3),
        StepSpec("transfer", "transfer_agent", ("transfer_minutes",),
                 lambda _v, ctx: {"transfer_minutes": ctx.world.source("transfer")}, duration=3),
        StepSpec("schedule", "planner", ("schedule",),
                 lambda v, _c: {"schedule": {"flight": v["flight"]["id"], "arrive": v["flight"]["arrive"],
                                             "minutes": v["transfer_minutes"]["minutes"], "checkin_by": v["checkin"]["checkin_by"]}},
                 reads=("flight", "checkin", "transfer_minutes"), duration=4),
        StepSpec("book", "booker", ("booked",), lambda v, _c: {"booked": {"flight": v["schedule"]["flight"]}},
                 reads=("schedule",), duration=1,
                 actions=(ActionSpec("book_flight", lambda v, _c: {"flight_id": v["schedule"]["flight"]}),)),
    )

    def flight_exists(evidence: Evidence, ctx: CheckContext):
        flights = ctx.tools.source("flights")
        if flights.get(evidence.value.get("id")) == evidence.value:
            return passed(evidence)
        return failed(evidence, "flight is not offered")

    def checks_sources():
        return {"flight": flight_exists, "checkin": source_check("hotel"), "transfer_minutes": source_check("transfer")}

    def invariants(schedule: Schedule):
        attributed = schedule.params.get("attributed", False)

        def check(values: dict[str, Any]):
            if feasible(values["flight"], values["checkin"], values["transfer_minutes"]):
                return True, "", ()
            return False, "arrival plus transfer misses check-in", (("flight",) if attributed else ())

        return (GlobalInvariant("arrival_fits_checkin", ("flight", "checkin", "transfer_minutes"), check),)

    def contracts():
        def ok(arguments, evidence, _ctx):
            schedule = cited(evidence, "schedule")
            if schedule is None or schedule.value.get("flight") != arguments.get("flight_id"):
                return reject("booking differs from the verified schedule")
            return approve(arguments)

        return [ActionContract("book_flight", ok, resource=lambda a: f"flight:{a.get('flight_id')}",
                               semantic_key=lambda a: "book:trip", irreversible=True)]

    def tools(world: World):
        return {"book_flight": effect_tool(world, "book_flight", lambda a: f"flight:{a['flight_id']}", append_to("bookings"))}

    def corrector(claim_id: str, world: World, _values):
        if claim_id == "flight":
            return choose(world)
        return {"checkin": world.source("hotel"), "transfer_minutes": world.source("transfer")}.get(claim_id)

    faults = (FaultSpec("preferred", "flight", "flight", lambda v: {"id": "UA1", "arrive": "22:30"}, "transient",
                        "flight agent keeps the preferred flight without checking the connection"),)
    schedules = (
        Schedule("local_commit_ambiguous", ("preferred",), {}, trust_model="local_commit"),
        Schedule("global_settle_ambiguous", ("preferred",), {}, trust_model="global_settle"),
        Schedule("revocable_attributed", ("preferred",), {}, trust_model="revocable", params={"attributed": True}),
        Schedule("global_settle_attributed", ("preferred",), {}, trust_model="global_settle", params={"attributed": True}),
    )
    case = CaseSpec(
        "G", template, "Locally valid flight, check-in, and transfer claims violate a global schedule invariant.",
        steps, ("schedule", "booked"), sources, {}, faults, schedules,
        _derived_checks(ref, checks_sources()), contracts, lookup_tools(tools), corrector,
        invariant_factory=invariants, suite="boundary",
    )
    ref.append(case)
    return case


# ------------------------------------------------------------------ case H


def case_h(template: str = "dev") -> CaseSpec:
    """Shared state changes after validation and before the effect."""
    test = template == "test"
    sku = "LAMP-1" if not test else "DESK-7"
    sources = {"customer_address": {"order": "O-55", "address": "1 Old Rd"}}
    resources = {f"inv:{sku}": {"sku": sku, "qty": 1}}
    ref: list[CaseSpec] = []

    def offer(customer: str):
        def compute(_v, ctx):
            record, version = ctx.world.store.read(f"inv:{sku}")
            return {f"offer_{customer}": {"sku": sku, "qty": record["qty"], "version": version, "customer": customer}}
        return compute

    def address(_v, ctx):
        return {"address": dict(ctx.world.source("customer_address"), version=ctx.world.source_version("customer_address"))}

    sell = lambda customer: ActionSpec(  # noqa: E731
        "sell",
        lambda v, _c: {"sku": sku, "customer": customer} if v[f"offer_{customer}"]["qty"] >= 1 else None,
        preconditions=lambda v, _c: {f"inv:{sku}": v[f"offer_{customer}"]["version"]},
    )
    steps = (
        StepSpec("seller_a", "seller", ("offer_alice",), offer("alice"), duration=3),
        StepSpec("seller_b", "seller", ("offer_bob",), offer("bob"), duration=3),
        StepSpec("notify_a", "seller", ("sale_alice",), lambda v, _c: {"sale_alice": dict(v["offer_alice"])},
                 reads=("offer_alice",), duration=1, actions=(sell("alice"),)),
        StepSpec("notify_b", "seller", ("sale_bob",), lambda v, _c: {"sale_bob": dict(v["offer_bob"])},
                 reads=("offer_bob",), duration=1, actions=(sell("bob"),)),
        StepSpec("address", "crm_agent", ("address",), address, duration=2),
        StepSpec("ship", "shipper", ("shipment",), lambda v, _c: {"shipment": {"order": v["address"]["order"]}},
                 reads=("address",), duration=2,
                 actions=(ActionSpec("ship", lambda v, _c: {"order": v["address"]["order"], "address": v["address"]["address"]},
                                     preconditions=lambda v, _c: {"src:customer_address": v["address"]["version"]}),)),
    )

    def stock_check(evidence: Evidence, ctx: CheckContext):
        record, version = ctx.tools.store.read(f"inv:{sku}")
        value = evidence.value
        if value["qty"] == record["qty"] and value["version"] == version:
            return passed(evidence)
        return failed(evidence, "stock changed")

    def address_check(evidence: Evidence, ctx: CheckContext):
        record = ctx.tools.source("customer_address")
        if evidence.value.get("address") == record["address"]:
            return passed(evidence)
        return failed(evidence, "address changed")

    def checks_sources():
        return {"offer_alice": stock_check, "offer_bob": stock_check, "address": address_check}

    def contracts():
        def sell_ok(arguments, evidence, _ctx):
            offer_ev = next((item for item in evidence if item.claim_id.startswith("offer_")), None)
            if offer_ev is None or offer_ev.value.get("qty", 0) < 1:
                return reject("no verified stock")
            return approve(arguments)

        def ship_ok(arguments, evidence, _ctx):
            address_ev = cited(evidence, "address")
            if address_ev is None or address_ev.value.get("address") != arguments.get("address"):
                return reject("shipment address differs from the verified address")
            return approve(arguments)

        return [
            ActionContract("sell", sell_ok, resource=lambda a: f"inv:{a.get('sku')}",
                           semantic_key=lambda a: f"sell:{a.get('sku')}:{a.get('customer')}", irreversible=True),
            ActionContract("ship", ship_ok, resource=lambda a: f"order:{a.get('order')}",
                           semantic_key=lambda a: f"ship:{a.get('order')}", irreversible=True),
        ]

    def tools(world: World):
        from ..effects import ConflictError

        async def sell_tool(arguments, *, preconditions, idempotency_key):
            import asyncio
            await asyncio.sleep(world.case.tool_latency)
            target = f"inv:{arguments['sku']}"
            result = world.store.write(
                target, lambda before: dict(before, qty=before["qty"] - 1), operation="sell",
                expected_version=preconditions.get(target), idempotency_key=idempotency_key,
            )
            if not result.get("duplicate_suppressed"):
                world.effects.append(("sell", dict(arguments)))
            return result

        async def ship_tool(arguments, *, preconditions, idempotency_key):
            import asyncio
            await asyncio.sleep(world.case.tool_latency)
            expected = preconditions.get("src:customer_address")
            if expected is not None and world.store.version("src:customer_address") != expected:
                world.store.conflicts += 1
                raise ConflictError("customer address changed since it was read")
            result = world.store.write(f"order:{arguments['order']}", lambda _b: dict(arguments), operation="ship",
                                       idempotency_key=idempotency_key)
            if not result.get("duplicate_suppressed"):
                world.effects.append(("ship", dict(arguments)))
            return result

        return {"sell": sell_tool, "ship": ship_tool}

    def oracle(effects, _reference, world: World):
        sales = [args for tool, args in effects if tool == "sell"]
        ships = [args for tool, args in effects if tool == "ship"]
        address_now = world.source("customer_address")["address"]
        forbidden = max(0, len(sales) - 1) + sum(1 for item in ships if item["address"] != address_now)
        duplicates = len(ships) - len({item["order"] for item in ships}) if ships else 0
        missing = (0 if sales else 1) + (0 if any(item["address"] == address_now for item in ships) else 1)
        return {"forbidden": forbidden, "duplicates": max(0, duplicates), "missing": missing, "compensated": 0}

    async def change_address(monitor, world: World, schedule: Schedule):
        import asyncio
        when = schedule.params.get("change", "after_authorize")
        while True:
            types = [event.event_type for event in monitor.graph.events
                     if event.event_type in {"action_authorized", "output_accepted"}]
            authorized = any(event.event_type == "action_authorized" and event.payload.get("action_id", "").find("ship") >= 0
                             for event in monitor.graph.events)
            if when == "after_authorize" and authorized:
                break
            if when == "before_check" and "output_accepted" in types and any(
                event.event_type == "output_accepted" and event.payload.get("claim_id") == "address" for event in monitor.graph.events
            ):
                break
            await asyncio.sleep(0.01)
            if asyncio.get_running_loop().time() > 60:
                return
        world.store.external_write("src:customer_address", {"order": "O-55", "address": "2 New Ave"})
        monitor.record("external_write", "world", {"resource": "src:customer_address"})

    schedules = (
        Schedule("two_sellers", (), {}, durations={"seller_a": 3.0, "seller_b": 3.0}),
        Schedule("two_sellers_staggered", (), {}, durations={"seller_a": 3.0, "seller_b": 3.6}),
        Schedule("address_change", (), {}, hook="change_address", params={"change": "after_authorize"}),
        Schedule("address_change_before_check", (), {"address": Trigger("delay", delay=1.0)}, hook="change_address",
                 params={"change": "before_check"}),
    )
    case = CaseSpec(
        "H", template, "Two sellers see the last unit; an address changes after its check.",
        steps, (), sources, resources, (), schedules,
        _derived_checks(ref, checks_sources()), contracts, lookup_tools(tools), None,
        effect_oracle=oracle, hooks={"change_address": change_address}, suite="boundary",
        has_clean_condition=False,
    )
    ref.append(case)
    return case


# --------------------------------------------------------- lost acknowledgment


def case_lost_ack(template: str = "dev") -> CaseSpec:
    test = template == "test"
    sources = {"invoice": {"customer": "acme" if not test else "globex", "amount": 250 if not test else 990}}
    ref: list[CaseSpec] = []
    steps = (
        StepSpec("invoice", "billing_agent", ("amount",), lambda _v, ctx: {"amount": ctx.world.source("invoice")}, duration=2),
        StepSpec("pay", "payments_agent", ("payment",), lambda v, _c: {"payment": dict(v["amount"])}, reads=("amount",),
                 duration=1, actions=(ActionSpec("charge", lambda v, _c: dict(v["amount"])),)),
    )

    def contracts():
        def ok(arguments, evidence, _ctx):
            amount = cited(evidence, "amount")
            if amount is None or dict(amount.value) != dict(arguments):
                return reject("charge differs from the verified invoice")
            return approve(arguments)

        return [ActionContract("charge", ok, resource=lambda a: f"account:{a.get('customer')}",
                               semantic_key=lambda a: f"charge:{a.get('customer')}:{a.get('amount')}", irreversible=True)]

    def tools(world: World):
        return {"charge": effect_tool(world, "charge", lambda a: f"account:{a['customer']}", append_to("charges"))}

    schedules = (
        Schedule("no_loss", (), {}),
        Schedule("ack_lost_once", (), {}, lose_ack={"charge": 1}),
        Schedule("ack_lost_twice", (), {}, lose_ack={"charge": 2}),
        Schedule("ack_lost_always", (), {}, lose_ack={"charge": 9}),
    )
    case = CaseSpec(
        "lost_ack", template, "A charge commits remotely but its acknowledgment is lost.",
        steps, ("payment",), sources, {}, (), schedules,
        _derived_checks(ref, {"amount": source_check("invoice")}), contracts, lookup_tools(tools), None, suite="boundary",
    )
    ref.append(case)
    return case


# --------------------------------------------------------- source revocation


def case_revocation(template: str = "dev") -> CaseSpec:
    test = template == "test"
    sources = {"quote": {"item": "GPU-node" if not test else "storage-tier", "price": 120 if not test else 75}}
    new_price = 135 if not test else 90
    ref: list[CaseSpec] = []
    steps = (
        StepSpec("quote", "pricing_agent", ("price",), lambda _v, ctx: {"price": ctx.world.source("quote")}, duration=2),
        StepSpec("approve", "finance_agent", ("approval",), lambda v, _c: {"approval": {"limit_ok": v["price"]["price"] <= 200,
                                                                                         "price": v["price"]["price"]}},
                 reads=("price",), duration=6),
        StepSpec("purchase", "buyer", ("order",), lambda v, _c: {"order": {"item": v["price"]["item"], "price": v["approval"]["price"]}},
                 reads=("approval", "price"), duration=1,
                 actions=(ActionSpec("buy", lambda v, _c: {"item": v["price"]["item"], "price": v["approval"]["price"]}),)),
    )

    def contracts():
        def ok(arguments, evidence, _ctx):
            approval = cited(evidence, "approval")
            if approval is None or approval.value.get("price") != arguments.get("price"):
                return reject("purchase differs from the verified approval")
            return approve(arguments)

        return [ActionContract("buy", ok, resource=lambda a: f"po:{a.get('item')}",
                               semantic_key=lambda a: f"buy:{a.get('item')}", irreversible=True)]

    def tools(world: World):
        return {"buy": effect_tool(world, "buy", lambda a: f"po:{a['item']}", append_to("orders"))}

    def corrector(claim_id: str, world: World, _values):
        return world.source("quote") if claim_id == "price" else None

    async def change_price(monitor, world: World, schedule: Schedule):
        import asyncio
        when = schedule.params.get("change", "after_verify")
        while asyncio.get_running_loop().time() < 60:
            verified = any(event.event_type == "status_changed" and event.payload.get("claim_id") == "price"
                           and event.payload.get("status") == "verified" for event in monitor.graph.events)
            dispatched = any(event.event_type == "remote_commit" for event in monitor.graph.events)
            if (when == "after_verify" and verified) or (when == "after_dispatch" and dispatched):
                break
            await asyncio.sleep(0.01)
        else:
            return
        await asyncio.sleep(schedule.params.get("lag", 0.5))
        record = dict(world.source("quote"), price=new_price)
        world.store.external_write("src:quote", record)
        monitor.record("external_write", "world", {"resource": "src:quote"})
        await monitor.revoke_source("price", "quote changed at the source")

    def reference_sources(schedule: Schedule):
        out = copy.deepcopy(sources)
        if schedule.params.get("change") != "after_dispatch":
            out["quote"]["price"] = new_price
        return out

    schedules = (
        Schedule("commit_change_early", (), {}, trust_model="local_commit", hook="change_price", params={"change": "after_verify"}),
        Schedule("revocable_change_early", (), {}, trust_model="revocable", hook="change_price", params={"change": "after_verify"}),
        Schedule("revocable_change_late", (), {}, trust_model="revocable", hook="change_price",
                 params={"change": "after_verify", "lag": 4.0}),
        Schedule("revocable_after_dispatch", (), {}, trust_model="revocable", hook="change_price",
                 params={"change": "after_dispatch"}),
    )
    case = CaseSpec(
        "revocation", template, "A verified quote changes at the source after verification.",
        steps, ("order",), sources, {}, (), schedules,
        _derived_checks(ref, {"price": source_check("quote")}), contracts, lookup_tools(tools), corrector,
        hooks={"change_price": change_price}, reference_sources=reference_sources, suite="boundary",
    )
    ref.append(case)
    return case


# ------------------------------------------------- no fault / high contention


def case_contention(template: str = "dev") -> CaseSpec:
    """Many agents and claims; cheap checks; no faults or frequent faults."""
    width = 12 if template == "dev" else 16
    sources = {f"s{index}": {"value": index * 7} for index in range(width)}
    ref: list[CaseSpec] = []
    steps: list[StepSpec] = []
    for index in range(width):
        steps.append(StepSpec(
            f"producer{index}", f"producer{index}", (f"c{index}a", f"c{index}b"),
            (lambda i: lambda _v, ctx: {f"c{i}a": ctx.world.source(f"s{i}")["value"], f"c{i}b": ctx.world.source(f"s{i}")["value"] + 1})(index),
            duration=2 + (index % 3), tokens=(300, 80),
        ))
    for index in range(0, width, 2):
        steps.append(StepSpec(
            f"join{index}", f"join{index}", (f"j{index}",),
            (lambda i: lambda v, _c: {f"j{i}": v[f"c{i}a"] + v[f"c{i + 1}b"]})(index),
            reads=(f"c{index}a", f"c{index + 1}b"), duration=1.5, tokens=(300, 80),
        ))
    joins = tuple(f"j{index}" for index in range(0, width, 2))
    steps.append(StepSpec("total", "aggregator", ("total",), lambda v, _c: {"total": sum(v[name] for name in joins)},
                          reads=joins, duration=1, tokens=(400, 80)))

    def source_checks():
        out = {}
        for index in range(width):
            out[f"c{index}a"] = (lambda i: lambda ev, ctx: passed(ev) if ev.value == ctx.tools.source(f"s{i}")["value"]
                                 else failed(ev, "differs"))(index)
            out[f"c{index}b"] = (lambda i: lambda ev, ctx: passed(ev) if ev.value == ctx.tools.source(f"s{i}")["value"] + 1
                                 else failed(ev, "differs"))(index)
        return out

    def corrector(claim_id: str, world: World, _values):
        index = int(claim_id[1:-1])
        base = world.source(f"s{index}")["value"]
        return base if claim_id.endswith("a") else base + 1

    faults = tuple(
        FaultSpec(f"f{index}", f"producer{index}", f"c{index}{'a' if index % 2 == 0 else 'b'}",
                  lambda v: v + 1000, "transient", "off by 1000")
        for index in range(0, width, 3)
    )
    schedules = (
        Schedule("no_fault", (), {}),
        Schedule("frequent_cheap", tuple(item.fault_id for item in faults),
                 {item.fault_id: Trigger("delay", delay=0.0) for item in faults}),
        Schedule("frequent_slow_check", tuple(item.fault_id for item in faults),
                 {item.fault_id: Trigger("delay", delay=6.0) for item in faults}),
        Schedule("single_late", (faults[0].fault_id,), {faults[0].fault_id: Trigger("timeline", point="late")}),
    )
    case = CaseSpec(
        "contention", template, "Wide graph with many claims and cheap checks; frequent or no faults.",
        tuple(steps), ("total",), sources, {}, faults, schedules,
        _derived_checks(ref, source_checks()), lambda: [], lookup_tools(), corrector,
        concurrency=width, check_latency=0.05, correction_latency=0.5, suite="boundary",
    )
    ref.append(case)
    return case


MECHANISM_FAMILIES: dict[str, Callable[[str], CaseSpec]] = {
    "A": case_a, "B": case_b, "C": case_c, "D": case_d, "E": case_e, "F": case_f,
}
BOUNDARY_FAMILIES: dict[str, Callable[[str], CaseSpec]] = {
    "G": case_g, "H": case_h, "lost_ack": case_lost_ack, "revocation": case_revocation, "contention": case_contention,
}


def _pararecover(family: str) -> Callable[[str], CaseSpec]:
    def build_motif(template: str) -> CaseSpec:
        from ..pararecover.cases import build as build_pararecover

        return build_pararecover(family, template)

    return build_motif


PARARECOVER_FAMILIES: dict[str, Callable[[str], CaseSpec]] = {
    f"PR_{relation}_{index}": _pararecover(f"PR_{relation}_{index}")
    for relation in ("nested", "overlap", "disjoint") for index in (1, 2)
}
ALL_FAMILIES = {**MECHANISM_FAMILIES, **BOUNDARY_FAMILIES, **PARARECOVER_FAMILIES}
TEMPLATES = ("dev", "test")


def build(family: str, template: str = "dev") -> CaseSpec:
    if family not in ALL_FAMILIES:
        raise KeyError(f"unknown family {family}; known: {', '.join(ALL_FAMILIES)}")
    if template not in TEMPLATES:
        raise KeyError(f"unknown template {template}")
    return ALL_FAMILIES[family](template)


def core_schedules(case: CaseSpec) -> tuple[Schedule, ...]:
    return tuple(item for item in case.schedules if item.params.get("core", True))


__all__ = [
    "ALL_FAMILIES", "BOUNDARY_FAMILIES", "MECHANISM_FAMILIES", "PARARECOVER_FAMILIES", "TEMPLATES", "build",
    "core_schedules",
    "EvidenceStatus",
]
