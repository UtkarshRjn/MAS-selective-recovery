# One-page summary: what happens when an agent's memory file goes bad?

*Draft for teammate critique, Nov 19, 2026 (PT). Full notes and citations: `research.md` in this folder. No model calls made yet.*

**Question.** Agents increasingly keep persistent notes (CLAUDE.md / AGENTS.md, MEMORY.md, a shared NOTES.md). If that file is *accidentally* damaged (a line deleted, a fact flipped, a stale value, a conflicting duplicate, a truncated or mis-encoded file), how often does the agent fail, how often does it do something irreversible (wrong email, wrong recipient, wrong booking), and does it ever notice?

**What's already known.**
- Deliberate memory poisoning is well studied: AgentPoison (arXiv 2407.12784), MINJA (2503.03704), PoisonedRAG (2402.07867), plus 2026 benchmarks (MemPoison, Trojan Hippo, MemoryDojo, MemSecBench). Small poison doses work. Plain false statements in 1.2% of a memory store cut QA accuracy from 0.85 to 0.30 (2608.21230).
- Instruction files are a live attack surface: Rules File Backdoor (Pillar Security), PackHallu (2610.09264), Skill-Inject (2602.20156). One study found malicious CLAUDE.md files barely moved code security (−8 to +6 pp, 2610.38521).
- Accidental staleness and conflict have a small literature (STALE 2605.06527, StaleBench 2611.19752, Obsolete Facts 2610.36308, Governance-State Corruption 2610.30698). **All of it scores question answering, not actions.**
- MemTX (2607.23929) is the closest work: it gates irreversible tool calls on committed memory and reports zero harm. It doesn't give per-type dose curves or test whether normal success metrics hide the harm.

**The gap we'd fill.**
1. Non-adversarial memory damage → **irreversible** side effects (not QA accuracy). Truncation and encoding damage are unstudied.
2. A **dose curve**: 1, 2 or 4 task-relevant lines corrupted, against a control with 4 *irrelevant* lines corrupted.
3. **Silent failure vs detection**: how often the agent finishes confidently while harm happened.
4. **Which gate catches which corruption.** Prediction: a lineage/integrity (hash) gate catches truncation, encoding damage and inserted lines, but *not* a value that was already wrong when written (stale, flipped). An evidence gate catches facts it can check against tool results (price, rating) but not user-only facts (email, nights).

**Recommended experiment (Design A).** This is the same travel-booking + email workflow as E4. The request becomes "book my usual trip per my saved preferences", so the email, budget, rating and nights come from a ~20-line MEMORY.md (4 task-relevant lines). We test 7 corruption types (6 accidental plus injected instruction as a positive control) × 3 doses, with clean and irrelevant-only controls. Measures are task failure, irreversible harm, silent wrong, detection rate, gate catch rate and false holds. Model is deepseek-4.1-flash, with muse-glimmer-30b as a cross-model check. n=25 per cell; headline curves pool across types (n=150 per dose, about ±8 pp 95% CI).

| Tier | Contents | Est. cost |
|---|---|---|
| 1 | deepseek ungated, all doses + controls + detection probe | ~$2.50 |
| 2 | + lineage and evidence gates at dose 2 | +$1.60 (total ~$4.10) |
| 3 | + muse at dose 2 | +$0.70 (total ~$4.80) |

**Budget.** About $5.46 of the $10 cap is spent. The runner's kill switch is at $9, so Tier 1 fits now. Tier 2 needs the kill switch raised to about $9.70, which only Bijin can approve. Tier 3 only fits if n is cut to 20.

**Alternatives.** (B) Recovery while the bad file is still on disk: does "recovered" stay at 100% while recovery re-sends wrong emails? ~$3.0. Strongest link to the blog's headline, but partly predictable. (C) One bad entry in a shared NOTES.md spreading across agents. ~$2.2. Lowest novelty, because it overlaps EquiMem, Spark to Fire and MemTX.

**Known weaknesses (please attack these).** It uses one workflow, as E4 did. The MEMORY.md is synthetic. The models are small and cheap. A detection probe may overstate what agents notice unprompted. The area is crowded, so the claim must stay narrow: accidental damage, irreversible harm, gate-by-type.

**Questions for reviewers.** (1) Is "accidental vs adversarial" a distinction worth a section, or should injected-instruction stay only a control? (2) Is the gate-by-type 2×2 the headline, or the dose curve? (3) Should Tier 1 swap a second task (e.g. file delete) in for the muse check, to answer the single-workflow criticism?
