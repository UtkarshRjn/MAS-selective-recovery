import json
from pathlib import Path

from agentrec.a2a import A2ACase, _assign, taint_span as a2a_span
from agentrec.tamas import parse_file, taint_span

FIXTURES = Path(__file__).parent / "fixtures"


def test_tamas_byzantine_carrier_taints_every_agent(tmp_path: Path):
    path = tmp_path / "education_byzantine.json"
    path.write_text((FIXTURES / "tamas_byzantine.json").read_text())
    case = parse_file(path, "Byzantine")[0]
    assert case.carriers == ("Assessment Agent",)
    span = taint_span(case)
    assert span["tainted_agents"] == span["n_agents"] == 2


def test_tamas_ipi_has_no_embedded_payload(tmp_path: Path):
    path = tmp_path / "education_IPI.json"
    raw = json.loads((FIXTURES / "tamas_byzantine.json").read_text())
    raw[0]["agents"][0]["agent_description"] = "Generates quizzes and tests."
    path.write_text(json.dumps(raw))
    case = parse_file(path, "IPI")[0]
    assert case.carriers == ()
    assert taint_span(case)["tainted_fraction"] == 0.0


def test_a2a_attack_taints_the_coordinator_and_a_control_does_not():
    row = _assign(FIXTURES / "a2a_meta.py", "CASE_METADATA")[0]
    attack = A2ACase("ASRF", "executable", row["case_id"], row["public"]["task"], False, row["oracle"], row["environment"]["peer_message"])
    control = A2ACase("ASRF", "executable", "c", "task", True, {}, "harmless")
    assert a2a_span(attack)["tainted"]
    assert not a2a_span(control)["tainted"]


def test_as_regex_on_fixture():
    text = (FIXTURES / "a2a_as.py").read_text()
    from agentrec.a2a import AS_CASE

    match = AS_CASE.search(text)
    assert match is not None
    assert match.group("id") == "as_001"
    assert int(match.group("choice")) == 1
