import json
import subprocess
import sys
from pathlib import Path


def test_summary_separates_final_and_recovery_effects(tmp_path):
    common = {
        "seed": 0, "fault": "f", "fault_fired": True, "fault_used": True,
        "irreversible_harm": True, "unauthorized_emails": 1,
    }
    rows = [
        {
            "method": "clean", "task_recovery_success": True,
            "final_effect_integrity": True, "usage": {},
        },
        {
            **common, "method": "none", "task_recovery_success": False,
            "final_effect_integrity": False, "recovery_effect_integrity": None,
            "recovery_irreversible_harm": None, "recovery_unauthorized_emails": None,
        },
        {
            **common, "method": "restart", "task_recovery_success": True,
            "final_effect_integrity": False, "recovery_effect_integrity": True,
            "recovery_irreversible_harm": False, "recovery_unauthorized_emails": 0,
            "recovery_usage": {}, "recovery_wall_s": 1.0,
        },
        {
            **common, "method": "verify", "task_recovery_success": True,
            "final_effect_integrity": True, "recovery_effect_integrity": None,
            "recovery_irreversible_harm": None, "recovery_unauthorized_emails": None,
        },
    ]
    path = tmp_path / "rows.jsonl"
    path.write_text("".join(json.dumps(row) + "\n" for row in rows))

    script = Path(__file__).parents[1] / "analysis" / "summarize.py"
    output = subprocess.run(
        [sys.executable, str(script), str(path)], check=True, capture_output=True, text=True
    ).stdout
    assert "| f | none | 1 | 0% | 0% | - | 100% | - | 100% | - |" in output
    assert "| f | restart | 1 | 100% | 0% | 100% | 100% | 0% | 100% | 0% |" in output
    assert "| f | verify | 1 | 100% | 100% | - | 100% | - | 100% | - |" in output
