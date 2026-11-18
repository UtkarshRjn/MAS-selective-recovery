"""Typed action contracts and the gateway every mutating tool goes through.

A contract names a tool, validates its exact arguments against verified
evidence, and says how the target identifies the resource the call touches.
Resource identity is normalized from the arguments that matter, so two calls
with different argument dictionaries that sell the same inventory item share a
resource. The semantic key identifies the request itself, so a retry of the
same request is recognized as the same effect.

Agents may call read-only tools directly. A mutating tool can be invoked only
by the monitor's dispatcher, through `ToolGateway.execute`.
"""

from __future__ import annotations

import inspect
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable

from .contract import DEAD_STATUSES, Evidence, EvidenceStatus, ProposedAction, applies, canonical
from .verification import Verdict, verify_action

Validator = Callable[[dict[str, Any], list[Evidence], "ContractContext"], Verdict]


@dataclass
class ContractContext:
    required: dict[str, Any] = field(default_factory=dict)
    enforce_applicability: bool = True
    versions: Callable[[str], list[Evidence]] = lambda _claim: []


def _default_resource(tool: str) -> Callable[[dict[str, Any]], str]:
    return lambda arguments: f"{tool}:{canonical(arguments)}"


def _default_semantic(tool: str) -> Callable[[dict[str, Any]], str]:
    return lambda arguments: f"{tool}:{canonical(arguments)}"


@dataclass(frozen=True)
class ActionContract:
    tool: str
    validate: Validator
    resource: Callable[[dict[str, Any]], str] | None = None
    semantic_key: Callable[[dict[str, Any]], str] | None = None
    mutating: bool = True
    irreversible: bool = False
    compensate: Callable[[dict[str, Any]], tuple[str, dict[str, Any]]] | None = None

    def resource_of(self, arguments: dict[str, Any]) -> str:
        return (self.resource or _default_resource(self.tool))(arguments)

    def semantic_of(self, arguments: dict[str, Any]) -> str:
        return (self.semantic_key or _default_semantic(self.tool))(arguments)


def reject(reason: str) -> Verdict:
    return Verdict(False, reason, (), False)


def approve(arguments: dict[str, Any]) -> Verdict:
    return Verdict(True, "", (), False, approved_arguments=dict(arguments))


class ContractRegistry:
    def __init__(self, contracts: tuple[ActionContract, ...] | list[ActionContract] = ()) -> None:
        self.contracts: dict[str, ActionContract] = {}
        for contract in contracts:
            self.register(contract)

    def register(self, contract: ActionContract) -> None:
        self.contracts[contract.tool] = contract

    def get(self, tool: str) -> ActionContract | None:
        return self.contracts.get(tool)

    def validate(
        self,
        tool: str,
        arguments: dict[str, Any],
        evidence: list[Evidence],
        ctx: ContractContext,
    ) -> Verdict:
        contract = self.contracts.get(tool)
        if contract is None:
            return reject("action is not part of the task")
        if not evidence:
            return reject("action cites no evidence")
        if any(item.verification_status != EvidenceStatus.VERIFIED for item in evidence):
            return Verdict(False, "supporting evidence is not verified", tuple(item.ref for item in evidence), False)
        return contract.validate(arguments, evidence, ctx)

    def resource(self, tool: str, arguments: dict[str, Any]) -> str:
        contract = self.contracts.get(tool)
        if contract is None:
            if tool == "write_file":
                return f"file:{arguments.get('path', '')}"
            return _default_resource(tool)(arguments)
        return contract.resource_of(arguments)

    def semantic_key(self, tool: str, arguments: dict[str, Any]) -> str:
        contract = self.contracts.get(tool)
        return _default_semantic(tool)(arguments) if contract is None else contract.semantic_of(arguments)

    def irreversible(self, tool: str) -> bool:
        contract = self.contracts.get(tool)
        return bool(contract and contract.irreversible)


def flight_contract() -> ActionContract:
    """The original slice: book exactly the required flight that the verified plan names."""

    def validate(arguments: dict[str, Any], evidence: list[Evidence], ctx: ContractContext) -> Verdict:
        return verify_action("book_flight", arguments, evidence, ctx.required)

    return ActionContract("book_flight", validate, resource=lambda args: f"flight:{args.get('flight_id')}")


def default_registry() -> ContractRegistry:
    return ContractRegistry((flight_contract(),))


def cited(evidence: list[Evidence], claim_id: str) -> Evidence | None:
    found = [item for item in evidence if item.claim_id == claim_id]
    return max(found, key=lambda item: item.version) if found else None


def field_matches(argument: str, claim_id: str, key: str | None = None) -> Validator:
    """The argument must equal a field of a cited, verified claim."""

    def validate(arguments: dict[str, Any], evidence: list[Evidence], _ctx: ContractContext) -> Verdict:
        source = cited(evidence, claim_id)
        if source is None:
            return reject(f"action does not cite {claim_id}")
        expected = source.value.get(key or argument) if isinstance(source.value, dict) else source.value
        if arguments.get(argument) != expected:
            return reject(f"{argument} differs from {claim_id}")
        return approve(arguments)

    return validate


def typed(schema: dict[str, type | tuple[type, ...]]) -> Validator:
    def validate(arguments: dict[str, Any], _evidence: list[Evidence], _ctx: ContractContext) -> Verdict:
        if set(arguments) != set(schema):
            return reject("arguments do not match the tool schema")
        for name, kind in schema.items():
            if not isinstance(arguments[name], kind):
                return reject(f"{name} has the wrong type")
        return approve(arguments)

    return validate


def applicable_report(argument: str, report_claim: str, passed_field: str = "passed", extra=()) -> Validator:
    """A release must cite a passing report for the exact artifact named in the arguments."""

    def validate(arguments: dict[str, Any], evidence: list[Evidence], ctx: ContractContext) -> Verdict:
        reports = [item for item in evidence if item.claim_id == report_claim]
        if not reports:
            return reject(f"release cites no {report_claim}")
        target = arguments.get(argument)
        for report in reports:
            value = report.value if isinstance(report.value, dict) else {}
            if not value.get(passed_field):
                continue
            if not ctx.enforce_applicability:
                return approve(arguments)
            if any(item.digest == target for item in report.applies_to) and applies(report.applies_to, tuple(extra)):
                return approve(arguments)
        return reject("no cited passing report applies to this artifact")

    return validate


def all_of(*validators: Validator) -> Validator:
    def validate(arguments: dict[str, Any], evidence: list[Evidence], ctx: ContractContext) -> Verdict:
        for validator in validators:
            verdict = validator(arguments, evidence, ctx)
            if not verdict.ok:
                return verdict
        return approve(arguments)

    return validate


def any_dead(evidence: list[Evidence]) -> bool:
    return any(item.verification_status in DEAD_STATUSES for item in evidence)


class GatewayViolation(PermissionError):
    """An agent tried to call a mutating tool outside the dispatcher."""


Tool = Callable[..., Any]


class ToolGateway:
    """Owns the tool table. Reads are open; mutations go through `execute` only."""

    def __init__(
        self,
        tools: dict[str, Tool],
        contracts: ContractRegistry,
        read_only: set[str] | frozenset[str] = frozenset(),
    ) -> None:
        self.tools = dict(tools)
        self.contracts = contracts
        self.read_only = frozenset(read_only)
        self.reads: list[tuple[str, str]] = []
        self.violations: list[str] = []

    def is_mutating(self, tool: str) -> bool:
        return tool not in self.read_only

    def read(self, tool: str, **arguments: Any) -> Any:
        if tool not in self.tools:
            raise KeyError(tool)
        if self.is_mutating(tool):
            self.violations.append(tool)
            raise GatewayViolation(f"{tool} mutates state and must go through the dispatcher")
        self.reads.append((tool, canonical(arguments)))
        return self.tools[tool](**arguments)

    async def execute(self, action: ProposedAction, *, idempotency_key: str, preconditions: dict[str, int]) -> Any:
        tool = self.tools.get(action.tool)
        if tool is None:
            raise KeyError(action.tool)
        result = tool(dict(action.arguments), preconditions=dict(preconditions), idempotency_key=idempotency_key)
        if inspect.isawaitable(result):
            result = await result
        return result


Transport = Callable[[ProposedAction], Awaitable[Any]]
