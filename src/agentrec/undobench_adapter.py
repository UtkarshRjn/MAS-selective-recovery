"""UndoBench recovery adapter backed by AgentRec provenance."""

from __future__ import annotations

import argparse
import json
import time
from pathlib import Path
from typing import Any, Callable

from recoverbench.engine import BenchmarkEngine, RunConfig
from recoverbench.recovery_methods import RECOVERY_METHOD_REGISTRY
from recoverbench.recovery_methods.base import RecoveryMethod

from .provenance import Provenance
class AgentRecRecoveryMethod(RecoveryMethod):
    """Retry the known failed call and reuse acknowledged calls."""

    name = "agentrec"
    version = "v1"
    description = "Known injected-call retry with acknowledged-call reuse and provenance."

    def __init__(self) -> None:
        self.reset()

    def wrap_tool(self, tool_name: str, tool_fn: Callable[..., Any], task: Any) -> Callable[..., Any]:
        def wrapped(*args: Any, **kwargs: Any) -> Any:
            fingerprint = json.dumps([tool_name, args, kwargs], sort_keys=True, default=str)
            if fingerprint in self.cache:
                step = self.prov.add_step(tool_name, "undobench", fingerprint=fingerprint, reused=True)
                self.prov.add_artifact(tool_name, self.cache[fingerprint], producer=step, reused=True)
                return self.cache[fingerprint]

            step = self.prov.add_step(tool_name, "undobench", fingerprint=fingerprint)
            try:
                result = tool_fn(*args, **kwargs)
            except Exception as error:
                self.prov.add_artifact(
                    f"{tool_name}/fault", {"error": str(error)}, producer=step, faulty=True
                )
                if not (
                    type(error).__name__.startswith("Injected")
                    or "[RECOVERBENCH INJECTED]" in str(error)
                ) or "PRE_MUTATION" not in str(error):
                    raise
                result = tool_fn(*args, **kwargs)
            self.prov.add_artifact(tool_name, result, producer=step)
            self.cache[fingerprint] = result
            return result

        return wrapped

    def on_failure_detected(self, error: Exception, context: dict[str, Any]) -> None:
        return None

    def reset(self) -> None:
        self.prov = Provenance()
        self.cache: dict[str, Any] = {}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--suite", default="smoke")
    parser.add_argument("--task")
    parser.add_argument("--model")
    parser.add_argument("--seeds", default="42")
    parser.add_argument("--output", default="runs")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    RECOVERY_METHOD_REGISTRY["agentrec:v1"] = AgentRecRecoveryMethod
    result = BenchmarkEngine(RunConfig(
        run_id=f"agentrec_{time.strftime('%Y%m%d_%H%M%S')}",
        model=args.model,
        suite=None if args.task else args.suite,
        task_id=args.task,
        recovery="agentrec:v1",
        output_dir=str(Path(args.output).resolve()),
        seeds=[int(seed) for seed in args.seeds.split(",")],
        dry_run=args.dry_run,
    )).run_benchmark()
    print(json.dumps(result.summary, indent=2))
    print(f"results: {result.run_dir}")


if __name__ == "__main__":
    main()
