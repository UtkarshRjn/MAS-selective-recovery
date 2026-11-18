"""ParaRecover-derived two-fault motifs: extraction rules, the frozen extract, and executed cases."""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from agentrec import policies
from agentrec.mechanism import cases
from agentrec.mechanism.harness import run_cell
from agentrec.mechanism.suite import preset
from agentrec.pararecover.cases import SLOTS, motifs
from agentrec.pararecover.extract import candidate, effect_class, named_nodes, relation

ROOT = Path(os.environ.get("AGENTREC_PARARECOVER_ROOT", "/tmp/upstream/ParaRecover"))


def test_named_nodes_reads_chinese_node_lists() -> None:
    assert named_nodes("节点2、3、4的参数错误；节点5缺失") == ["2", "3", "4", "5"]
    assert named_nodes("节点4（x）和节点2.1的结果") == ["4", "2.1"]


def test_relation_classifies_cones() -> None:
    tasks = {
        "root": {"dep": []}, "1": {"dep": ["root"]}, "2": {"dep": ["1"]}, "3": {"dep": ["root"]},
        "4": {"dep": ["2", "3"]}, "5": {"dep": ["root"]}, "response": {"dep": ["4", "5"]},
    }
    assert relation(tasks, "1", "2") == "nested"
    assert relation(tasks, "2", "3") == "overlap"
    assert relation(tasks, "4", "5") == "disjoint"


def test_effect_class_by_tool_name() -> None:
    assert effect_class("send_bulk_emails") == "irreversible"
    assert effect_class("schedule_meetings") == "reversible"
    assert effect_class("get_local_climate") is None


def test_candidate_rejects_untranslatable_errors() -> None:
    record = {"tag_1": "必要工具缺失", "tag_2": "参数类型错误", "plan": "{}", "tool_list": "[]", "query": "", "id": 1}
    assert candidate(record)[0] is None


def test_frozen_extract_is_balanced_and_labelled() -> None:
    data = motifs()
    assert data["official_result_eligible"] is False
    assert data["source"]["license"] == "MIT" and len(data["source"]["commit"]) == 40
    assert {name: len(items) for name, items in data["motifs"].items()} == {"nested": 4, "overlap": 4, "disjoint": 4}
    for name, items in data["motifs"].items():
        for motif in items:
            assert motif["relation"] == name and len(motif["sites"]) == 2 and len(motif["tags"]) >= 2


@pytest.mark.skipif(not (ROOT / "data/LEVEL-2").exists(), reason="set AGENTREC_PARARECOVER_ROOT")
def test_extract_reproduces_the_frozen_file() -> None:
    from agentrec.pararecover.extract import select

    fresh = json.loads(json.dumps(select(ROOT), ensure_ascii=False, sort_keys=True))
    assert fresh == motifs()


def test_suite_preset_covers_every_slot() -> None:
    config = preset("pararecover", seeds=1)
    assert set(config.families) == set(SLOTS)
    assert config.schedules == "all"


@pytest.mark.parametrize("family", sorted(SLOTS))
@pytest.mark.parametrize("template", ["dev", "test"])
def test_every_motif_runs_clean_and_recovers_both_faults(family: str, template: str) -> None:
    case = cases.build(family, template)
    assert case.suite == "pararecover"
    clean = run_cell(case, case.schedules[0], 0, policies.get("online"), clean=True, horizon=400, correction_budget=3).row
    assert clean["outcome"] == "safe_completion"
    for schedule in case.schedules:
        row = run_cell(case, schedule, 0, policies.get("online"), clean=False, horizon=400, correction_budget=3).row
        assert row["faults_fired"] == {"first": 1, "second": 1}
        assert row["faults_detected"]["first"]
        assert row["safe_history"] and row["forbidden_effects"] == 0


def test_dev_and_test_templates_use_different_records() -> None:
    for family in SLOTS:
        dev, test = cases.build(family, "dev"), cases.build(family, "test")
        assert dev.description != test.description
