# Literature and blog sweep, 21 November 2026

Search date: Saturday, 21 November 2026, Pacific Time. Every item in the verified sections was opened at its official page (Anthropic, Cognition, OpenAI, DeepMind, or an arXiv abstract). Nothing is inferred from a title alone. Items found in search but not opened in this pass are listed as unverified. This is an internal planning file. It does not name people from our team.

Prior verified review from earlier today is in `lit_review_mas.md` and `prior_art_mas.md`. This file adds official lab blogs and the newest 2026 papers on failure propagation, recovery, memory damage, compression, and handoff loss.

## Official blogs, verified by fetch

### Anthropic

**How we built our multi-agent research system.** Official page [engineering](https://www.anthropic.com/engineering/multi-agent-research-system) (canonical [research](https://www.anthropic.com/research/multi-agent-research-system)). Published 13 June 2025, page `article:modified_time` 9 October 2026. Opened in this pass.

- A lead agent plus parallel subagents beat a single Claude Opus 4 agent by 90.2% on an internal research eval (board members of IT S&P 500 companies).
- On BrowseComp, three factors explained 95% of performance variance. Token usage alone explained 80%, then tool calls and model choice. Quote: "Multi-agent systems work mainly because they help spend enough tokens to solve the problem."
- Quote: "agents typically use about 4× more tokens than chat interactions, and multi-agent systems use about 15× more tokens than chats."
- They say the architecture is a poor fit when every agent must share the same context, or when there are many dependencies, and they single out most coding tasks.
- The lead agent saves its plan to Memory because a 200,000 token context can be truncated.
- Open problem they state but do not test as a safety study: whether the extra tokens buy reliability on irreversible actions, or only breadth on research search.

**Building effective AI agents.** [Official](https://www.anthropic.com/research/building-effective-agents). Opened (main advice page). Distinguishes workflows from agents. Often cited as the baseline for "start simple."

**Effective context engineering for AI agents.** [Engineering](https://www.anthropic.com/engineering/effective-context-engineering-for-ai-agents) and [research](https://www.anthropic.com/research/effective-context-engineering-for-ai-agents). Published 29 September 2025, modified 18 October 2026. Opened.

- They treat context as a finite attention budget and cite context rot (Chroma).
- Long horizon tactics: compaction, structured note taking, and subagent architectures. A subagent may use tens of thousands of tokens and return a 1,000 to 2,000 token summary.
- Open problem: they recommend distilled summaries as the handoff, but they do not measure whether a safety constraint or a conflict flag survives that compression, or what the over-asking cost is.

**Effective harnesses for long-running agents.** [Engineering](https://www.anthropic.com/engineering/effective-harnesses-for-long-running-agents) and [research](https://www.anthropic.com/research/effective-harnesses-for-long-running-agents). Published 26 November 2025, modified 18 October 2026. Opened.

- Quote: "getting agents to make consistent progress across multiple context windows remains an open problem."
- Compaction is "not sufficient." Two failure modes: the agent runs out of context mid feature and the next session has to guess; later sessions see partial progress and declare the job done.
- Their fix is an initializer agent plus a coding agent that writes `claude-progress.txt` and git commits.
- Open problem: they treat lossy compaction as a productivity bug. They do not measure silent irreversible harm when a constraint is dropped at a session boundary.

A later related page, [Harness design for long-running apps](https://www.anthropic.com/research/harness-design-long-running-apps), appeared in search. Not fully opened in this pass (unverified beyond the URL).

### Cognition

**Don't Build Multi-Agents.** [Official](https://cognition.com/blog/dont-build-multi-agents), Walden Yan, published 12 June 2025. Opened.

- Subagents lose the main agent's context. The Flappy Bird example: one subagent builds Mario pipes, another builds a bird that does not match, and the combiner cannot fix the implicit decisions.
- Principle 1: share context, and share full agent traces, not just individual messages.
- Principle 2: actions carry implicit decisions, and conflicting decisions carry bad results.
- They say context compression "is hard to get right" and is the way to stretch a single threaded agent, not a reason to split writers.
- They criticize OpenAI Swarm and Microsoft Autogen for pushing multi-agent designs.

**Multi-Agents: What's Actually Working.** [Official](https://cognition.com/blog/multi-agents-working), published 22 April 2026. Opened.

- The earlier warning still holds for parallel writer swarms.
- A narrower class works: several agents can contribute intelligence, but writes stay single threaded.
- A code review loop found about 2 bugs per PR (about 58% called severe). They say this worked best when the coding and review agents did not share context beforehand, because of context rot. This sits in tension with "share full traces."
- Open problem nobody in these posts measures on irreversible tools: when does a clean context help a reviewer, and when does hiding the flag or the constraint cause silent harm?

### OpenAI

**A practical guide to building agents.** Official PDF [cdn.openai.com/.../a-practical-guide-to-building-agents.pdf](https://cdn.openai.com/business-guides-and-resources/a-practical-guide-to-building-agents.pdf), landing page [openai.com/business/guides-and-resources/a-practical-guide-to-building-ai-agents/](https://openai.com/business/guides-and-resources/a-practical-guide-to-building-ai-agents/). Opened the PDF landing/excerpt. Practical orchestration and safety advice for first agents. No controlled handoff-loss study.

**Agents SDK, replacing Swarm.** Official docs: [Agents SDK](https://developers.openai.com/api/docs/guides/agents/sdk), [Orchestration and handoffs](https://developers.openai.com/api/docs/guides/agents/orchestration). GitHub [openai/swarm](https://github.com/openai/swarm) states Swarm is replaced by the Agents SDK. Opened the orchestration page.

- Two patterns: handoffs (a specialist takes ownership of the conversation) and agents as tools (the manager keeps the final answer).
- Quote-level advice: "Start with one agent whenever you can. Add specialists only when they materially improve capability isolation, policy isolation, prompt clarity, or trace legibility."
- Open problem: the SDK documents how to hand off, not whether a safety constraint survives the handoff, and not the token or over-asking cost.

### Google DeepMind

**Investing in multi-agent AI safety research.** [Official](https://deepmind.google/blog/investing-in-multi-agent-ai-safety-research/), 11 June 2026. Opened.

- A funding call of up to $10M with Schmidt Sciences, the Cooperative AI Foundation, ARIA, and Google.org.
- They say most safety evaluations still analyze models in isolation, and interacting agents can produce emergent behavior.
- Priority areas: sandboxes and testbeds, the science of agent networks, infrastructure for identity and commitment, and oversight and control.
- This is a call for work, not a result. It supports the claim that controlled MAS safety testbeds are still scarce.

**Co-Scientist.** Official page [deepmind.google/blog/co-scientist-a-multi-agent-ai-partner-to-accelerate-research/](https://deepmind.google/blog/co-scientist-a-multi-agent-ai-partner-to-accelerate-research/), dated 19 May 2026 in search. Not fully opened in this pass (search snippet only). Unverified details are not quoted here.

Other DeepMind agent posts seen in search but not opened: CodeMender, AlphaProof Code, "Securing the future of AI agents." Unverified.

## Newest papers, verified by opening the arXiv abstract

These were opened at `https://arxiv.org/abs/<id>` in this pass.

| Paper | Date | What it shows | Gap it leaves for us |
| --- | --- | --- | --- |
| Zhang et al. Multi-Agent Memory Inconsistency: Where Factual Conflicts Disappear in LLM Agents. [arXiv:2611.22245](https://arxiv.org/abs/2611.22245) | 17 Nov 2026 (v2 18 Nov) | Delegation raises retrieval of both conflicting claims (50.2% to 68.7%) but conflict detection falls (16.2% to 13.2%). Detection is higher when one worker sees both claims, lower when claims are split. Worker to lead handoffs are the stated target. Comparable generation spending across three models. | Document review, not irreversible tools. No over-asking or completion cost. Very close on the "conflicts vanish at the handoff" mechanism. We must distinguish safety actions and a compute matched single agent, or this paper already owns the claim. |
| Anchan and Sen. Attention Tax, Handoff Tax: A Stylised Model of When Multi-Agent LLM Systems Help. [arXiv:2610.06069](https://arxiv.org/abs/2610.06069) | 5 Oct 2026 | Decomposition helps once the attention cost of a long context exceeds the handoff tax of compression. Crossover at depth 10 on a ledger task; predictions at 20, 50, and 100 land within 9 points. | Accuracy on ledger steps, not silent irreversible harm, flags, or C0 over-asking. The model is the right language for a handoff stress study. The revelation would be measuring the tax in a safety and cost pair. |
| Yang et al. MemoryDojo: Benchmarking Poisoning Through LLM Agent Memory. [arXiv:2611.09721](https://arxiv.org/abs/2611.09721) | 6 Nov 2026 | 155 multi-session trajectories, five memory systems, all susceptible to poisoning, with architecture-dependent downstream effects. | Attack and retrieval, not a role scoped pipeline with irreversible tools, and not flag survival under compression. |
| Wang et al. Dependable Long-Horizon LLM Agents. [arXiv:2611.13254](https://arxiv.org/abs/2611.13254) | 9 Nov 2026 | 95 failed runs from Who&When and MAD. Faults enter early (median 0.18). In 72% of failed runs the trajectory already had evidence against the error; in 88% of those, no agent acted on it. Something flagged a deviation in 31% of runs, but no response removed the bad state. | Observational attribution on existing corpora, not a controlled compression or access manipulation. Strongly supports "noticed, then lost" as a real pattern. |
| Pitre et al. Understanding Communication Failures in Multi-Agent LLM Systems. [arXiv:2610.32060](https://arxiv.org/abs/2610.32060) | 23 Oct 2026 | Taxonomy from 3,211 failed multi-round trajectories. Failures are dominated by recovery and consensus problems, often after agents see peer replies. | Debate and QA style tasks, not irreversible tools or memory damage classes. |
| Xu. Idempotency Is Not Recovery. [arXiv:2611.23353](https://arxiv.org/abs/2611.23353) | 17 Nov 2026 | Crash and replay of tool actions. Idempotency stops duplicates (0/200 vs 117/200) but is not the same as recovery. Reconciliation is required or outcomes stay ambiguous. | Single agent runtime reliability, not multi-agent recovery that re-executes side effects. Directly relevant to building on the E4 "recovered is not harmless" audit. |
| Shah and Kathrotia. Securing and Auditing Long-Horizon LLM Agents: State-Transfer Auditing. [arXiv:2611.23346](https://arxiv.org/abs/2611.23346) | 17 Nov 2026 | Failures occur at state transfers: compaction dropping user constraints, memory consolidation stripping source authority, incomplete restore. They propose hash chained audit points. 240 fault injection trajectories. | A harness audit method, not a controlled test of whether compression causes silent harm on irreversible tools. The "compaction drops constraints" claim is exactly the practitioner gap. |
| Liao et al. EMFA: Error-Propagation Modeling for Failure Attribution. [arXiv:2610.11600](https://arxiv.org/abs/2610.11600) | 8 Oct 2026 | Models cascading propagation and loops, then counterfactual checks to find the decisive agent step. SOTA step-level attribution on Who&When. Previously unverified in our earlier review; now opened. | Attribution of failures, not a new safety intervention or irreversible harm metric. |

## Papers already verified earlier today (still in force)

Do not restate at length. Closest prior items: Cemri et al. MAST [2503.13657](https://arxiv.org/abs/2503.13657); Huang et al. [2408.00989](https://arxiv.org/abs/2408.00989); Xie et al. Spark to Fire [2603.04474](https://arxiv.org/abs/2603.04474); Sun et al. [2608.24569](https://arxiv.org/abs/2608.24569); Li and Wang [2608.16055](https://arxiv.org/abs/2608.16055); Srinivasan et al. [2611.17820](https://arxiv.org/abs/2611.17820); Han [2607.13918](https://arxiv.org/abs/2607.13918); Singh and Pawar [2608.14588](https://arxiv.org/abs/2608.14588); AgentPoison; MINJA; tau-bench.

## Practitioner claims that are still under-tested

1. Cognition: subagents lose context, and compression is hard to get right. Not tested on irreversible tools with a compute matched single agent, or with a flag that must survive a summary.
2. Anthropic: multi-agent setups use about 15 times the tokens of chat, and "work mainly because they spend enough tokens." Not tested as a safety and over-asking tradeoff.
3. Anthropic long-running harness: compaction drops state and later agents declare early success. Shah and Kathrotia describe the same transfer loss. Nobody we opened measures silent harm and C0 over-asking as a pair under controlled compression depth.
4. Cognition 2026: a reviewer with a clean context catches more bugs. This can cut against "share full traces." Untested as a safety finding (does hiding the planner's flag help or hurt the actor?).
5. OpenAI SDK: start with one agent; add handoffs only when isolation helps. No published irreversible-action experiment behind that advice.
6. Anchan and Sen give a crossover depth for accuracy. The crossover for silent harm and over-asking is unknown.
7. Zhang et al. (days old) already study conflicts disappearing at worker to lead handoffs in document review. A new paper must not repeat that claim. The remaining opening is irreversible harm, over-asking, completion, and a compute matched control.

## Venue timing, verified

- **ICML 2027** official [Call for Papers](https://icml.cc/Conferences/2027/CallForPapers) and [Dates](https://icml.cc/Conferences/2027/Dates): abstract 14 January 2027, 11:59 PM AoE; full paper 22 January 2027, 11:59 PM AoE; Rio de Janeiro, 31 July to 6 August 2027. About eight weeks from today.
- **AAAI-27** official [page](https://aaai.org/conference/aaai/aaai-27/): abstracts were due 21 July 2026, papers 28 July 2026. That cycle is closed.
- **AAAI-28**: no official date was found. Third-party pages project a late July 2027 paper deadline. Treat that as unverified until AAAI posts a call.

## Unverified (do not cite as fact)

- Full text of DeepMind Co-Scientist, CodeMender, AlphaProof Code, and "Securing the future of AI agents" (search hits only).
- Anthropic "Harness design for long-running apps" (URL only).
- Other 2026 arXiv hits from the paper search that were not opened, including 2611.22342, 2611.19886, 2611.07140, 2610.27406, 2610.17723, 2610.33282, 2610.15459, 2606.02282, 2611.14980.
- Earlier unverified items still unverified: DART 2605.23311, SagaLLM 2503.11951, "Commit Before Trust," "Facts Without Rules" 2608.29028.

