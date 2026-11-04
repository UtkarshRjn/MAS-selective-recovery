# PR preparation

Suggested title: Expand Phase 1 evaluation with MBPP fault injection and travel authorization cases

Base: `coding-suite` (a889670), head: `expand-benchmark-examples`.
The three inherited commits on coding-suite are not new contributions in this PR.
If coding-suite is merged first, retarget and verify the diff against main.

## Proposed description

The previous coding experiment used a small task set and simple code corruption,
which did not exercise report faults reliably. This change adds pinned 5/10/50-task
MBPP manifests, isolated Docker scoring, parallel execution, and configurable
OpenAI-compatible providers. The full protocol evaluates seven fault families with
clean/no-recovery/verification and restart/suffix/coarse conditions. Explicitly marked
fixed-code scenarios exercise forged and stale test reports without claiming natural
failure or identical model continuations.

Three simulated travel policy variants measure unauthorized recipients, booking
without approval, and cancellation of protected bookings. Their audit distinguishes
attempts, committed effects, verifier blocks, and historical violations after recovery.
No real email, booking or cancellation service is called.

Stage audits and a retrospective analysis separate downstream exposure from observed
incorrect artifacts/decisions. Token and timing metrics include verification costs;
wasted-work and tracking-overhead measurements remain scoped proxies. Saved completed
results and limitations are in `evals/evidence/README.md`. The new 10-task two-provider
comparison is incomplete: GPT-OSS is running and the GLM run stopped on HTTP errors.
Those partial results are not pooled with the completed pilots.

Validation: `uv run pytest -q`; `git diff --check`; CLI help; pinned manifest integrity;
previous 50-task Docker reference/mutant qualification; 106 tests passed on both AWS
hosts before the follow-up runs. Local HTTP-status diagnostic coverage adds one test.
No extra model runs were performed for PR preparation (one small Triton diagnostic
probe succeeded after the failure).

## Reviewer focus

- Dataset/test partition and references never enter normal agent context; controlled
  fixture scenarios are separately marked and documented.
- Generated code executes only within constrained Docker containers.
- Report approval is deterministic Python, not evidence of persuading an LLM reviewer.
- Fault eligibility varies with generated outputs; skipped injections are not defenses.
- Oracle fault localization and disabling injection are assumptions of recovery methods.
- Action history survives recovery; policy auditing is not a hard enforcement guard.
- API exceptions stop new work and preserve completed rows; no automatic retry/resume.
- Verify and recovery use different total inference budgets and intervention times.
