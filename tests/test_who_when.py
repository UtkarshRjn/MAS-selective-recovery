import json
from pathlib import Path

from agentrec.who_when import (
    agent_match,
    attribute_all_at_once,
    attribute_binary_search,
    attribute_step_by_step,
    load_dir,
    parse_case,
    parse_prediction,
    taint_span,
)

FIXTURES = Path(__file__).parent / "fixtures"


class Scripted:
    """Returns scripted completions and records how many times it was called."""

    usage_calls = 0

    def __init__(self, replies: list[str]) -> None:
        self.replies = list(replies)
        self.calls = 0

    def complete(self, messages, **kwargs) -> str:
        self.calls += 1
        return self.replies.pop(0)


def test_algorithm_and_handcrafted_fields_differ():
    alg = json.loads((FIXTURES / "who_when_algorithm.json").read_text())
    hand = json.loads((FIXTURES / "who_when_handcrafted.json").read_text())
    a = parse_case(alg, "algorithm", "1")
    h = parse_case(hand, "handcrafted", "1")
    assert a.answer == "8" and a.speaker(0) == "Excel_Expert"
    assert h.answer.startswith("Renzo") and h.speaker(2) == "WebSurfer"
    assert a.mistake_step == 0 and h.mistake_step == 2


def test_coarse_lineage_taints_the_suffix():
    raw = json.loads((FIXTURES / "who_when_handcrafted.json").read_text())
    span = taint_span(parse_case(raw, "handcrafted", "1"))
    assert span["lineage_matches_suffix"]
    assert span["tainted_steps"] == 1
    assert span["reused_steps"] == 2

    early = parse_case(json.loads((FIXTURES / "who_when_algorithm.json").read_text()), "algorithm", "1")
    span = taint_span(early)
    assert span["tainted_steps"] == 3 and span["reused_steps"] == 0


def test_load_dir_sorts_numerically(tmp_path: Path):
    src = json.loads((FIXTURES / "who_when_algorithm.json").read_text())
    (tmp_path / "10.json").write_text(json.dumps(src))
    (tmp_path / "2.json").write_text(json.dumps(src))
    loaded = load_dir(tmp_path, "algorithm")
    assert [c.file_id for c in loaded] == ["2", "10"]


def test_prediction_parser_and_agent_containment():
    agent, step = parse_prediction("Agent Name: Excel_Expert\nStep Number: 0\nReason for Mistake: bad parse")
    assert agent == "Excel_Expert" and step == 0
    assert agent_match("I think Excel_Expert failed", "Excel_Expert")
    assert not agent_match("DataVerification_Expert", "Excel_Expert")


def test_attribution_methods_score_against_the_annotation():
    case = parse_case(json.loads((FIXTURES / "who_when_algorithm.json").read_text()), "algorithm", "1")
    hit = attribute_all_at_once(Scripted(["Agent Name: Excel_Expert\nStep Number: 0\nReason for Mistake: x"]), case)
    assert hit["agent_correct"] and hit["step_correct"]

    miss = attribute_step_by_step(Scripted(["1. No.\n2. Reason: ok", "1. Yes.\n2. Reason: wrong"]), case)
    assert miss["predicted_step"] == 1 and not miss["step_correct"]
    assert miss["predicted_agent"] == "Computer_terminal"

    landed = attribute_binary_search(Scripted(["lower half", "lower half"]), case)
    assert landed["predicted_step"] == 2 and landed["predicted_agent"] == "DataVerification_Expert"
