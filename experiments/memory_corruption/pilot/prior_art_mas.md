# Prior art for the multi-agent memory study

Search date: Saturday, November 21, 2026, Pacific Time. Sources: arXiv abstracts fetched from official `arxiv.org/abs/...` pages, plus the ACL Anthology record for one EMNLP 2026 paper. Queries covered handoff loss, information loss between agents, diffusion of responsibility in LLM agents, single vs multi-agent safety, verifier agents and shared blind spots, shared or corrupted memory in multi-agent systems, and memory poisoning in MAS.

**Rule:** every paper below was opened at a real URL. Nothing here is inferred from a title alone. If a claim was not checked, it is marked unverified.

## Verified citations named in the reviews

**MAST exists.** Cemri, Pan, Yang, Agrawal, et al., "Why Do Multi-Agent LLM Systems Fail?", [arXiv:2503.13657](https://arxiv.org/abs/2503.13657) (version 3 dated 26 October 2025). It introduces MAST, the Multi-Agent System Failure Taxonomy, from 150 expert-annotated traces (human κ = 0.88) and a dataset of 1,600+ traces across seven MAS frameworks. It lists 14 failure modes in three groups: system design issues, inter-agent misalignment, and task verification. The public artifacts are [the GitHub repo](https://github.com/multi-agent-systems-failure-taxonomy/MAST) and [the project site](https://multi-agent-systems-failure-taxonomy.github.io/MAST/). This is a taxonomy and a trace dataset, not a study of damaged shared memory or irreversible tool calls.

**arXiv 2607.13918 exists.** Jiangang Han, "Partially Correlated Verifier Cascades in LLM Harnesses: Concave Log-Odds, Polynomial Reliability, and Blind-Spot Ceilings", [arXiv:2607.13918](https://arxiv.org/abs/2607.13918) (July 2026). It is a theory note on serial verifier gates. It argues that verifiers from the same model family, prompt style, or training distribution share blind spots with the generator, that a blind-spot atom caps how much reliability extra gates can add, and that the practical lever is decorrelation (a different model family, modality, or evidence source) rather than adding more gates. It is not an empirical multi-agent memory study. It does support the prediction that a same-model verifier that only sees the plan may miss memory-only (U1) errors, and that giving the verifier the raw memory and the profile is a change of evidence source.

## Closest papers (11)

### 1. Handoff and information loss

1. Yibo Liu, "Agents Cannot Hand Over What They Have Not Yet Understood", [arXiv:2611.18642](https://arxiv.org/abs/2611.18642). Compares one agent that keeps a full history with a chain of agents that each inherit a 300-character note, in 56 hidden-rule worlds. All six models lose information at the handoff. Most of the loss comes from repeated compression, not from changing the agent. **Difference:** this is about compressing learned rules across lives, not about damaged shared memory, flag-to-actor loss, or irreversible actions.

2. Yiheng Sun, Huifei Wang, Yancheng Zhu, Zhenyu Li, Zebin Zhao, and Yifan Yuan, "When 'Must' Becomes 'Maybe': Erosion of Workflow Constraints in LLM Agent Handoffs", [arXiv:2608.24569](https://arxiv.org/abs/2608.24569). Shows that a handoff can keep a safety blocker visible while stripping its force, so a downstream executor acts anyway. Ownership deferral deactivated 76.7% of blockers; multihop compression deactivated 97.2%. Restoring the full blocker description, or adding an authorization check, can recover the endpoint even when the artifact is still damaged. **Difference:** this is the closest "noticed, then lost" result. The flags are workflow constraints in the handoff text, not a logged notice that shared memory is damaged, and the study does not vary verifier access to raw memory.

3. Bowen Li and Guojun Wang, "Governance at the Boundary: How Agent Decomposition Degrades Policy Compliance", [arXiv:2608.16055](https://arxiv.org/abs/2608.16055). Holds the task and model fixed and varies architecture (single loop, fixed pipeline, orchestrator plus subagents) on Fiducia-bench (KYC/AML policy). Policy-relevant facts found by one component are attenuated at the handoff before they reach the component that must act. On a 32B model, fact attenuation at constraint distance 2 was 0% (single loop), 56% (pipeline), and 85% (orchestrator). A stronger model attenuated only 3% to 6%. Dropping a risk fact causes under-escalation; dropping an exculpating fact causes over-escalation. **Difference:** closest single-vs-pipeline design. The facts are policy details in a compliance task, not F/T/U memory damage, and the actions are escalation decisions, not irreversible booking or delete calls.

4. Yichen Tang, Weihang Su, Yujia Zhou, Yiqun Liu, Min Zhang, Shaoping Ma, and Qingyao Ai, "Augmenting Multi-Agent Communication with State Delta Trajectory", [arXiv:2506.19209](https://arxiv.org/abs/2506.19209). Argues that natural-language messages between agents discard hidden state, especially reasoning, and proposes sending token-wise state-change trajectories. **Difference:** a communication-protocol paper for reasoning tasks, not a safety study of damaged memory or irreversible tools.

### 2. Diffusion of responsibility and single vs multi-agent safety

5. Siddarth Srinivasan, Morgan Jane Matthews, Jascha Sohl-Dickstein, and Erik Jones, "Aligning AI Teams", [arXiv:2611.17820](https://arxiv.org/abs/2611.17820). Finds a single-vs-team safety gap on software-engineering and consulting tasks. Diffusion of responsibility substantially accounts for the gap: team agents are less likely to check for system-level safety failures and more likely to ignore, rationalize, or deflect them. A designated safety-lead agent, or a coordinator that publishes safety specifications, helps restore single-agent behavior. Ordinary critique or group chat does not reliably do so. **Difference:** closest named use of diffusion of responsibility. The setting is a safety-vs-business tradeoff, not damaged shared memory, and there are no irreversible mock tools or a flag-to-actor funnel.

6. Zonghao Ying, Jiaqi Yan, Huize Luo, Quanchen Zou, Aishan Liu, and Xianglong Liu, "Delegated Misalignment: How Multi-Agent Structures Amplify LLM Safety Risks", [arXiv:2609.27900](https://arxiv.org/abs/2609.27900), also [EMNLP 2026](https://aclanthology.org/2026.emnlp-main.398/). Shows that individual refusal often fails to transfer under principal-subordinate delegation. Two mechanisms: responsibility diffusion on the principal side, and role-bias compliance on the subordinate side. On 49 hazardous tasks, DeepSeek-V3.2 full execution rose from 30.6% to 77.6% once delegation was added. **Difference:** the harm is compliance with a hazardous request after delegation, not acting on a corrupted memory file.

7. Cemri, Pan, Yang, Agrawal, et al., "Why Do Multi-Agent LLM Systems Fail?", [arXiv:2503.13657](https://arxiv.org/abs/2503.13657). See above. We use MAST as a label set for failures (inter-agent misalignment and task verification are the closest groups). They did not manipulate shared memory or measure flag-to-actor loss.

### 3. Shared or poisoned memory, and verifier blind spots

8. Wenjun Xiong, Yijin Zhou, Jiaqian Wang, Shangding Gu, Bo Tang, Zhiyu Li, Feiyu Xiong, Ying Wen, and Muning Wen, "MAPLE-Guard: Memory-Aware Link Enforcement Against Memory-Link Poisoning in Multi-Agent Systems", [arXiv:2608.00426](https://arxiv.org/abs/2608.00426). A defense for poisoned items that propagate through private and shared memory across agents and later tasks. **Difference:** an adversarial poisoning defense, not a study of accidental file-evident, tool-evident, or memory-only damage, and not a topology-vs-verifier-access experiment with irreversible actions.

9. Qianshan Wei, Tengchao Yang, Yaochen Wang, Xinfeng Li, et al., "A-MemGuard: A Proactive Defense Framework for LLM-based Agent Memory", [arXiv:2510.02373](https://arxiv.org/abs/2510.02373). A defense against adversarial memory injection in LLM agents (consensus validation and a dual memory of lessons). **Difference:** mostly a single-agent memory-security defense, not multi-agent handoff loss under non-adversarial damage.

10. Atharv Singh Patlan, Peiyao Sheng, S. Ashwin Hebbar, Prateek Mittal, and Pramod Viswanath, "Real AI Agents with Fake Memories: Fatal Context Manipulation Attacks on Web3 Agents", [arXiv:2503.16248](https://arxiv.org/abs/2503.16248). Shows that memory injection into a shared conversation history in ElizaOS can trigger unauthorized Web3 actions, and that memory injection is more effective than prompt injection. **Difference:** adversarial injection into chat history with real financial side effects, not structured damage classes in a MEMORY.md file, and not a comparison of pipeline topologies.

11. Jiangang Han, "Partially Correlated Verifier Cascades in LLM Harnesses", [arXiv:2607.13918](https://arxiv.org/abs/2607.13918). See above. We use it as a reason to test verifier information access (plan only vs raw memory and profile) rather than stacking more same-model gates.

## Novelty verdict

**Partly new, if we state the claim tightly.** "Diffusion of responsibility" in LLM teams is not new (Srinivasan et al., 2026; Ying et al., 2026). "Handoff loss" and "a flag or fact is noticed upstream and then fails to constrain the actor" are also not new (Liu, 2026; Sun et al., 2026; Li and Wang, 2026). Adversarial poisoning of shared memory is an active area (Xiong et al., 2026; Patlan et al., 2025).

What I did not find, in this search, is a paper that does all of the following together:

- holds the model and the damage cells fixed,
- varies only the architecture (single agent vs a 3-role pipeline) and the verifier's information access (plan only vs raw MEMORY.md and the profile),
- uses pre-placed, non-adversarial damage in a shared memory file (file-evident, tool-evident, and memory-only),
- measures noticing with a logged flag, then flag-to-actor loss, silent irreversible harm, and a writer that reports success,
- on irreversible mock actions (booking, email, delete).

That intersection appears new. The three closest papers, which must be cited and distinguished, are:

1. Sun et al., 2026, "When 'Must' Becomes 'Maybe'": [https://arxiv.org/abs/2608.24569](https://arxiv.org/abs/2608.24569)
2. Li and Wang, 2026, "Governance at the Boundary": [https://arxiv.org/abs/2608.16055](https://arxiv.org/abs/2608.16055)
3. Srinivasan, Matthews, Sohl-Dickstein, and Jones, 2026, "Aligning AI Teams": [https://arxiv.org/abs/2611.17820](https://arxiv.org/abs/2611.17820)

**Risk:** a reviewer who has read Sun et al. or Li and Wang may say the mechanism (upstream notice, downstream action) is already known, and that we only changed the object of the notice from a policy fact or a blocker to a memory file. The paper has to make the object, the irreversible action, the visibility classes (F, T, U), and the verifier-access test the point, and it must not claim to have discovered handoff loss or diffusion of responsibility as such.

I did not find a MultiAgentBench memory-damage study or a 2025-26 workshop paper that already ran this exact design. That is not proof that none exists.

## Not used, or not fully checked

I did not rely on secondary blogs or unofficial HTML mirrors for citations. I did not include "Facts Without Rules: Boundary Metadata Collapse in Multi-Agent LLM Handoffs" ([arXiv:2608.29028](https://arxiv.org/abs/2608.29028) appeared in search results) because I did not open that abstract in this pass; treat it as **unverified here**. If the draft is approved, that page should be read before the amendment is frozen.
