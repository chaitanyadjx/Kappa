# APR Survey — Landscape of Automated Program Repair

Compiled from the two surveys in `papers/reference/`:

1. **[LLM_Survey_1.pdf]** Yang, Cai, Liu, Le, Zhang, Bissyandé, Liu, Tian. *A Survey of LLM-based Software Repair: Taxonomies, Design Paradigms, and Applications.* ACM TOSEM, 2026 (arXiv:2506.23749). 66 systems, Jan 2022 – Apr 2026. Artifact: https://github.com/GLEAM-Lab/ProgramRepair
2. **[LLM_Survey_2.pdf]** Zhang, Fang, Xie, Ma, Sun, Yang, Chen. *A Systematic Literature Review on Large Language Models for Automated Program Repair.* ACM TOSEM, 2026, DOI 10.1145/3799693. 189 papers, 2020 – Sept 2025. Artifact: https://github.com/iSEngLab/AwesomeLLM4APR

Both are published in ACM TOSEM (CORE A*). Earlier surveys they position against: Zhang et al., learning-based APR survey (TOSEM 2023, DOI 10.1145/3631974); Huang et al., *Evolving paradigms in APR* (ACM Computing Surveys 57(2), 2024 — four-way search/constraint/template/learning taxonomy); Gazzola et al. (IEEE TSE 2019, traditional techniques up to 2017); Monperrus's living review (program-repair.org).

---

## 1. What APR Is

**Definition** (Survey 2, §2.1.1): given a buggy program P and a specification S that P fails to satisfy, APR finds a minimal transformation P' of P that satisfies S. In practice the specification is a test suite.

**The generate-and-validate pipeline** (both surveys):
1. **Fault localization** — identify suspicious code elements (spectrum-based/SBFL like Ochiai+GZoltar, or perfect localization assumed in many evaluations).
2. **Patch generation** — produce candidate patches via transformation rules, search, or models.
3. **Patch validation** — run the test suite as oracle; a patch passing all tests is **plausible**; if it generalizes to hidden tests it is **correct**; if it passes public tests but breaks elsewhere it is **overfitting** (test-overfitting is the field's central correctness problem).

Traditional technique families (Survey 2 §2.1.3):
- **Heuristic-based** — genetic-programming search over patch space: GenProg, Astor, ARJA, SimFix.
- **Constraint-based** — condition synthesis via SMT solvers: Nopol, Cardumen, ACS, Dynamoth.
- **Pattern-based** — hand-crafted or mined fix templates: TBar, FixMiner, AVATAR.
- **Learning-based** — repair as neural machine translation buggy→fixed: CURE, Recoder, DEAR, SelfAPR.

The precursor learning-based survey (Zhang et al., TOSEM 2023) organizes the same space by **bug type** (semantic / syntax error / vulnerability), **granularity** (expression, statement, hunk, method), and **code representation** (sequence / AST tree / graph) with encoder-decoder NMT architectures (LSTM → Transformer).

## 2. The LLM Era: Scale and Trends (Survey 2)

- **189 papers, 2020–Sept 2025**; no LLM-based APR before 2020 (first: TFix). Growth fit by a power law (R²≈0.988); estimated 210+ papers by end of 2025.
- **Venues**: ICSE (26) > FSE/ESEC (17) > ASE (13) > TOSEM (12) > ISSTA (11); spillover into AI (NeurIPS, ICLR, ACL, AAAI), PL (OOPSLA, PLDI), and security (S&P, USENIX Security, TDSC, TIFS). 12.44% arXiv-only.
- **Languages**: 24 total — Java 35.45%, Python 28.73%, C 10.07%, C++ 10.07%; rare languages now reachable (Rust, Verilog, Solidity) because LLMs transfer general programming knowledge without per-language training corpora.
- **Models**: 78 distinct LLMs used. Top: GPT-3.5 (72 papers), GPT-4 (51), CodeT5 (31), CodeLlama (28), Codex (22), GPT-4o (15), StarCoder (15), CodeBERT (13). Architectures: encoder-only (CodeBERT/GraphCodeBERT), encoder-decoder (CodeT5/PLBART), decoder-only (GPT family, CodeLlama, DeepSeek-Coder) — decoder-only dominates.
- **Contributions**: 118 new techniques, 56 empirical studies, 15 benchmarks, 3 human studies.

## 3. How LLMs Are Used: Four Utilization Strategies (Survey 2) = Four Paradigms (Survey 1)

Survey 2 classifies by **adaptation strategy**; Survey 1 by **where repair capability and control logic reside** (parameter adaptation × runtime control authority), with auxiliary evidence as separate cross-cutting tags. The two views align:

| Strategy (Surv. 2) | Paradigm (Surv. 1) | Count | Mechanism | Representative systems |
|---|---|---|---|---|
| Fine-tuning (55) | **Fine-Tuning** (21 systems) | Full Tuning: Huang et al., Jiang et al., VulMaster, RepairCAT; Adapter/PEFT: RepairLLaMA (LoRA r8 on frozen CodeLlama-7B), MORepair (QLoRA+rationale), Li et al. ((IA)³), federated LoRA (Luo et al.); Distillation: KNOD (rule-guided), DistiLRR (rationale distillation), NARRepair (dataset distillation, 5.4–18.6× faster decoding); RL Tuning: RePair (PPO+critic), SecRepair, SWE-RL (Llama3-70B, diff-similarity reward, 41.0% SWE-bench Verified), Vul-R2, AdaPatcher (DPO) | Weights updated on bug-fix data; strong task alignment, high training cost; RL aligns with execution rewards but reward design is fragile |
| Zero-shot / few-shot prompting (54+24) | **Prompting** (17) | Zero-shot: AlphaRepair (cloze-style CodeBERT infilling), Codex-on-QuixBugs (Prenner et al.); Few-shot: Xia et al. (9 LLMs, 1,094 bugs), Gao et al. (200+ Codex prompts, 4 retrieved exemplars), CEDAR; + **retrieval-augmented**: RLCE (repo map → GPT-3.5 22.6→56.0%, GPT-4 41.1→81.5% on RepoBugs), Ehsani et al. (hierarchical bug/repo/project knowledge → 79% on BugsInPy), DSrepair (DS knowledge graph, DS-1000), PailGen (fix-pattern retrieval); + **analysis-enriched**: D4C (failing test in prompt), APPATCH (semantics-aware slices), TracePrompt (PySnooper traces), ACFix (RBAC graph) | Frozen model, single call; everything outside the model. Focused context helps; irrelevant/verbose context hurts |
| Conversational / scripted pipelines | **Procedural** (15) | Test-feedback loops: ChatRepair (162/337 D4J, $0.42/bug, ~21.9 rounds), ThinkRepair (chain-of-thought + partial-pass reuse), REx (Thompson-sampled bandit over partial patches, 2–5× fewer LLM calls), ContrastRepair (failing/passing test pairs); Human-in-the-loop: CREF (tutor hints, AVG-5 76.6% on TutorCode), HULA (planner+coder+human, SWE-bench + 663 Jira issues), DRCodePilot (design rationales, 4.7× zero-shot on Flink/Solr); Retrieval-scripted: Agentless (3-phase hierarchical localization→repair→validate, 32.0% SWE-bench Lite @ $0.70), PATCH (multi-role simulation), KGCompass (repo knowledge graph, 58.30% Lite pass@1 @ $0.20); Analysis-scripted: Repilot (JDT completion engine prunes invalid tokens, 59% compile rate), SAN2PATCH (sanitizer logs, ToT, 79.5% VulnLoc), PredicateFix (bridging predicates → key examples, re-run analyzer to accept), LLM4CVE (PoC-exploit feedback), IntDiagSolver (environment diagnosis, 86.7%) | Designer fixes stage order, tools, retry policy, stopping budget; reproducible and cost-predictable |
| Agent (15) | **Agentic** (13) | Tool-augmented: SWE-Agent (agent-computer interface, 12.47% SWE-bench), SWE-Agent M (multimodal), OpenHands (CodeAct, 26.0% Lite), RepairAgent (FSM + 14 tools, 164/835 D4J), AutoCodeRover (AST-guided, ~19% Lite, $0.43), LANTERN (translate→repair→translate for low-resource langs), VulDebugger (debugger-guided); LLM-gated review: TSAPR (MCTS + LLM judge, 201/835 D4J @pass@32), Abstain-and-Validate (industrial noise reduction), SpecRover (spec-guided review, 31.0% Lite @ $0.65); Self-controlled: MAGIS (4-role team, 25.33% Lite), SWE-Search (MCTS debate, 31% Lite), Learn-by-Interact (60.0% Lite) | LLM chooses tools/branching/regeneration/stopping; most autonomy, highest cost, weakest run-to-run stability |

**Cross-cutting auxiliary evidence** (Survey 1's key conceptual move): retrieval, program-analysis evidence, test feedback, human feedback, and domain knowledge are *tags combinable with any paradigm*, not top-level buckets. Survey 1's strongest corpus-wide association: retrieval-tagged systems target repository-level/industrial settings far more often than non-retrieval ones (odds ratio 38.57, Fisher p≈3.4e-6, significant after Benjamini–Hochberg correction) — evidence that context acquisition is the orthogonal design axis for repo-scale repair.

**Temporal trend** (Survey 1, Table 8): 2023 = fine-tuning + prompting era; 2024 = procedural + agentic appear; 2025 = largest year across all paradigms (30 of 66 systems).

## 4. Repair Scenarios / Bug Types (Survey 2 — 20 scenarios)

Distribution across 189 papers (Monperrus-derived taxonomy):

| Scenario | Papers | Share | Notes |
|---|---|---|---|
| Semantic bugs (test-triggered) | 81 | 42.93% | Dominant; driven by Defects4J. Fine-tuned (CURE, DEAR), cloze (AlphaRepair, GAMMA, Repilot), conversational (ChatRepair, ThinkRepair), agents (RepairAgent) |
| Programming problems | 24 | 12.57% | Competitive-programming/student code (QuixBugs, HumanEvalFix, Refactory, CodeFlashs) |
| Security vulnerabilities | 21 | 10.99% | CVE-style: VulMaster, VRepair-line, InferFix, PredicateFix, APPATCH, LLM4CVE (security-inclined; our project excludes this scope) |
| Repository-level issues | 18 | 9.42% | SWE-bench family: SWE-Agent, AutoCodeRover, OpenHands, KGCompass |
| Static warnings | 9 | 4.71% | Static-analysis alert repair (TFix lineage; PredicateFix) — **Kappa's chosen scope** |
| Syntax errors | 8 | 4.19% | DeepFix lineage, SYNSHINE, BIFI |
| Hardware bugs | 5 | 2.62% | Verilog |
| Test case repair | 4 | — | |
| Type errors | 4 | — | PyTy, TypeFix |
| GUI bugs | 3 | — | multimodal |
| Code review / performance / smart contract | 2+2+2 | — | |
| API misuse, crash, error-handling, formal proof/spec, motion planner, translation bug | 1 each | — | long tail: >60%-style rarity that motivates retrieval over training |

Read with the TOSEM-2023 bug-type lens (semantic / syntax / vulnerability) plus Monperrus's broader domain list (concurrency, performance, memory, configuration, static-analysis violations). Note: **concurrency bug repair has zero LLM-based papers** — an explicitly open scenario (Survey 2 §9.1.2).

## 5. Benchmarks, Metrics, and Comparability

**Dataset usage** (Survey 2, 111 datasets): Defects4J (51 papers), QuixBugs (29), HumanEval-Java (12), BFP (11), SWE-bench Lite (10), HumanEval (10), CVEfixes (8), TFix (6), MBPP (6), Big-Vul (6), SWE-bench (5), BugsInPy (4), Vul4J (4), ManyBugs (4), APPS (4)...

**Metrics**: pass@k dominates (pass@1 in 21/66 systems in Survey 1's corpus, rising in 2024–25); plus exact match, EM/BLEU/CodeBLEU, correct-repair counts, success rate, avg@5, accept@1, merged PRs. APR convention: **plausible** (passes available tests) vs **correct** (semantically equivalent to developer patch, human-verified).

**Survey 1's protocol audit (its distinctive contribution)** — same benchmark name ≠ comparable score:
- Of 2,145 unordered system pairs, only 100 share a benchmark label; 32 fall in three protocol-aligned windows (SWE-bench Lite pass@1, EvalRepair-Java pass@10/CodeLlama-13B, HumanEval-Java pass@10); only **one** pair is fully model-controlled (MORepair vs Luo et al. on EvalRepair-Java, both Fine-Tuning). **No model-controlled cross-paradigm comparison exists — benchmark-name overlap does not support a paradigm leaderboard.**
- SWE-bench Lite pass@1 window: 18.00% (SWE-Agent) → 58.30% (KGCompass), whole-stack scores under differing models/costs/search depth.
- Evaluation-reliability coding: 10/66 assume perfect fault localization; 6/66 narrow scope to single-function/single-hunk; 36/66 discuss contamination but only **28/66 operationalize a mitigation**; 39/66 add validation beyond public tests (hidden tests, sanitizer/exploit reproduction, LLM/human review).
- Vulnerability/API-misuse/crash repair protocols are fragmented across small disjoint datasets with mixed metrics (EM, F1, CodeBLEU, correct repairs, success rate) — assurance evidence accumulates poorly there.

## 6. Patch Correctness Assessment (APCA) and Self-Repair (Survey 2 §8.3–8.4)

- **LLM-based APCA**: embeddings-as-features (Tian et al. 2020; Invalidator w/ CodeBERT), QA formulation (Quatrain), fine-tuned pipeline (APPT), zero-shot next-token judging (PatchZero). Roots: PATCH-SIM execution-trace similarity; anti-pattern static checks; overfitting magnitude studied by Smith et al./Petke et al.
- **Self-repair** (repair folded into code generation): Self-Edit, Self-Debugging (DeepMind), OpenCodeInterpreter, CYCLE, LDB, AgentCoder, SelfEvolve, Self-Refine. Caution from Olausson et al.: LLMs often cannot reliably explain why their own code is wrong — self-repair is not a panacea.

## 7. Open Challenges (merged from both surveys)

Survey 1's five evidence-backed bottlenecks:
1. **Training/deployment cost** — 19/21 fine-tuning systems use open models vs 37/45 prompting/procedural/agentic systems on proprietary APIs; loop-based systems must report token/latency budgets and stopping criteria.
2. **Evaluation reliability** — perfect-FL and narrowed-scope assumptions; contamination discussed more often than controlled; extra validation is heterogeneous and must be reported as a protocol attribute, not a score.
3. **Workflow robustness** — prompt/seed sensitivity compounds across agentic steps; invalid commands and unbounded loops make cost unpredictable.
4. **Retrieval & context management** — no recall guarantee; prompt-window limits; structured analysis output gets flattened into text.
5. **Patch assurance / acceptance gates** — only 10/66 systems expose an explicit acceptance gate (test-feedback, human review, LLM-gated); the rest return generator output directly.

Survey 1's three directions: stronger multi-hunk/contamination-aware benchmarks with marked localization assumptions; cost-aware repair policies (success per unit budget, caching, fallback to simpler pipelines); separating generation from acceptance (independent validation signals).

Survey 2's three-stage agenda:
- **Stage 1 — advancing techniques**: integrate LLMs with off-the-shelf APR (SMT solvers, heuristic search spaces, domain templates); attack under-explored scenarios (concurrency=0 papers, hardware) via transfer learning, cloze-style repair with patterns, in-context learning.
- **Stage 2 — rigorous evaluation**: leakage mitigation (ChatGPT can enumerate all Defects4J projects; construct large-scale leakage-free benchmarks from closed/commercial/real-time sources); cost-aware evaluation (PEFT, pruning/quantization/distillation, LLM-based patch ranking before validation).
- **Stage 3 — deployment**: human-centered adoption studies (do LLMs actually reduce debugging cost?); APR inside autonomous programming workflows (Human-AI co-programming; unified generate-test-localize-repair loops).

## 8. Where Kappa Sits in This Landscape

- **Scope**: static-analysis violation repair (non-security) — the "static warnings" scenario (9 papers) merged with the detector→patcher→re-validate family (Getafix/Phoenix/Sorald in industry; PredicateFix procedurally). Under-served relative to semantic bugs (81 papers) → room for contribution.
- **Design slot** (Survey 1 taxonomy): no fine-tuning (no parameter adaptation), no LLM in the repair loop → Kappa is an **analysis-augmented, program-analysis-evidence-driven repair pipeline outside all four LLM paradigms** — closest to the analysis-enriched procedural bucket (Repilot, PredicateFix) but deterministic: embeddings over the code graph replace LLM calls; the Node Embedding Match Table is the retrieval layer.
- **Validation** (Survey 1 challenge 5): Kappa's Independent Tester = an explicit acceptance gate (violation removed + no new violations + compile + tests) — directly answers the survey's "separate generation from acceptance" direction.
- **Evaluation discipline** (Survey 1 RQ3): report benchmark variant, metric + candidate budget, model provenance (UnixCoder/CodeBERT versions), localization assumptions, leakage controls (post-cutoff split), and repeated-run variance — the exact reporting gaps the survey flags.
- **Benchmarks**: CodeHaluEval (execution-verified hallucinations), CloudAPIBench (stub-binding API validity), BugsInPy/SWE-bench subsets where test oracles exist, SonarQube-style rule suites (Sorald) — mapped in `research/Method.md`.

## 9. Reference List

**Surveys**
- Yang et al. 2026. A Survey of LLM-based Software Repair. TOSEM. arXiv:2506.23749 — [local PDF](../papers/reference/LLM_Survey_1.pdf)
- Zhang et al. 2026. A Systematic Literature Review on LLMs for APR. TOSEM. doi:10.1145/3799693 — [local PDF](../papers/reference/LLM_Survey_2.pdf)
- Zhang et al. 2023. A Survey of Learning-based APR. TOSEM 33(2). doi:10.1145/3631974
- Huang et al. 2024. Evolving paradigms in APR: Taxonomy, challenges, opportunities. ACM Computing Surveys 57(2)
- Gazzola et al. 2019. Automatic Software Repair: A Survey. IEEE TSE 45(1)
- Monperrus. Living review of automatic program repair. program-repair.org/bibliography.html

**Representative systems cited above** (venue in parens)
- Fine-tuning: VulMaster (TOSEM'24), RepairCAT (APR@ICSE'24), RepairLLaMA (TSE'25), MORepair (TOSEM'25), KNOD (ICSE'23), NARRepair (ASE'24), SWE-RL (2025), AdaPatcher (AAAI'25), TraceFixer (2023), InferFix (FSE'23), PyTy (ICSE'24), NTR (ICSE'25)
- Prompting: AlphaRepair (FSE'22), Xia et al. (ICSE'23), Gao et al. (ASE'23), CEDAR (ICSE'23), RLCE (ICSE-C'24), Ehsani et al. (ASE'25), DSrepair (2025), PailGen (TOSEM'26), D4C (2025), APPATCH (USENIX Sec'25), TracePrompt (KANLP'25), ACFix (TSE'25), Dr.Fix (2026)
- Procedural: ChatRepair (ISSTA'24), ThinkRepair (ISSTA'24), REx (2024), ContrastRepair (2025), CREF (2024), HULA (2025), DRCodePilot (2024), Agentless (FSE'25), PATCH (TOSEM'25), KGCompass (2025), Repilot (FSE'23), SAN2PATCH (2025), PredicateFix (ICSE'26) — [local PDF](../papers/reference/PredicateFix.pdf), LLM4CVE (DSD'25), IntDiagSolver (2026)
- Agentic: SWE-Agent (NeurIPS'24), OpenHands (FSE'25), RepairAgent (ICSE'25) — [local PDF](../papers/reference/2403.17134v2.pdf), AutoCodeRover (2024), LANTERN (ICSE'26), VulDebugger (2025), TSAPR (2025), SpecRover (ICSE'25), Abstain-and-Validate (ICSE-SEIP'26), MAGIS (2024), SWE-Search (ICLR'25), Learn-by-Interact (2025)
- APCA/self-repair: PATCH-SIM (ISSTA'18), Tian et al. (2020), Quatrain (ASE'22), Invalidator (2023), PatchZero (2023), APPT (2024); Self-Edit, Self-Debugging, OpenCodeInterpreter, CYCLE (OOPSLA'24), LDB, Olausson et al. (2023)

**Papers already analyzed in this project**: `research/Method.md` (past-work table) and `papers/recent_related_work_problem_statement.md` cover RepairAgent, PReMM, ExpeRepair, CodeHalu (AST), CloudAPIBench, package-hallucination study, CodexGraph, KG-codegen, RepoScope, CodeMEM, PredicateFix.
