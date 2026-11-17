Project Name: Kappa
Field: APR (Automated Program Repair)

## APR Scope

Taxonomy from Zhang et al., "A Survey of Learning-based Automated Program Repair," ACM TOSEM 33(2), 2024 (A* journal; cited by RepairAgent [56] and PReMM in papers/reference/). The survey lists APR by bug type:

1. Semantic bug repair - behavior deviates from the test oracle (Defects4J/BugsInPy-style; SequenceR, CURE, SelfAPR, PReMM, RepairAgent). Needs executable tests + fault localization as input.
2. Syntax error repair - compile/parse errors (DeepFix, DrRepair, BIFI).
3. Security vulnerability repair - CVE/CWE-driven (VRepair, VulRepair, PredicateFix's CodeQL CVE benchmark). Excluded: this project is not security-focused.
4. Adjacent repair domains (Monperrus living review / program-repair.org): concurrency, performance, memory, configuration, and static-analysis violation repair.

The general A* survey for traditional (non-learning) techniques: Gazzola et al., "Automatic Software Repair: A Survey," IEEE TSE 2019.

Newer surveys covering the LLM era: "A Systematic Literature Review on Large Language Models for Automated Program Repair," ACM TOSEM (DOI 10.1145/3799693) - the direct successor to the TOSEM survey above; and "A Survey of LLM-based Software Repair: Taxonomies, Design Paradigms and Applications" (DOI 10.1145/3845995; 66 systems, Jan 2022 - June 2025; four paradigms: fine-tuning / prompting / procedural / agentic, plus cross-cutting retrieval- and analysis-augmented layers).

Chosen scope: static-analysis violation repair (non-security rule violations) - the "detector -> patcher -> re-validate" family used by industry (Getafix at Facebook/Infer, Phoenix for Infer alerts, Sorald/SpongeBugs for SonarQube rules).

- Kappa's violation definition (Node Expectation vs Connected Nodes mismatch on the code graph) is exactly a rule-based detector, not a test oracle, so this type matches the pipeline directly.
- Violation categories = the Knowledge DB categories (calls, library usage, dataflow/resources, config/env, control flow) plus non-security code-quality rules.
- Patch acceptance: violation removed + no new violations (re-run Detector), compilation, tests when available.
- Benchmark anchors: SonarQube-style rule suites (Sorald benchmark), CodeHaluEval for generated-code hallucinations, CloudAPIBench for API-call validity; Defects4J/BugsInPy subsets only where a test oracle exists.
- Static-violation-repair benchmarks (matching Kappa's detector scope): PyTyDefects (ICSE 2024, github.com/sola-st/PyTy) - 2,248 real Python static-error -> fix pairs, 12 error types, scored by warning-disappears + matches-dev-fix; requires a Pyre/mypy check adapter in the Detector. Sorald (github.com/ASSERT-KTH/sorald) - SonarJava violation repair + mining (25+ rules, 161 repos, 65% fix rate on pre-condition-matching violations; Java, cross-language validation). TFix (ICSE 2021) - ~18k ESLint/TypeScript violations with real fixes (adjacent, JS/TS). No published benchmark covers Kappa's exact rule set (undefined names + unreachable code + resource leaks in Python) - the injected-violation corpus fills that gap, using Sorald's mining approach as the mechanism.

## Improvement Measurement (repair-delta benchmarks)

Gap: code benchmarks score a snapshot (pass/fail); repair needs before->after with regression guards. The reference papers' pattern, three parts:

1. Before->after transition corpora - BugSwarm (3,091+ fail-pass pairs from real CI, Java AND Python, reproducible containers, CLI/API: bugswarm.org) is the primary anchor: run the same test suite on before/after artifacts; the pair encodes "improved". Bears (Java build pairs), QuixBugs and ManySStuBs4J (buggy/fixed program pairs) as smaller alternatives.
2. Fix-level scoring - FixEval (ASE 2023, arXiv:2206.07796): execution-based evaluation of fixes (buggy->fixed Codeforces submissions) with validity -> plausibility -> correctness metrics plus time/memory limits; demonstrates text metrics (BLEU/EM) do not measure repair quality.
3. Regression and overfitting guards - SWE-bench's fail-to-pass / pass-to-pass split (improvement must flip F2P while P2P stays green); APCA techniques (PATCH-SIM, BATS, Panther, ODS; Petke et al. FSE'24 overfitting study) to verify the improvement is not test-overfitting.

Kappa composite metric on BugSwarm Python pairs: violations-before vs violations-after per Knowledge DB category (Detector re-run on patched code), F2P flip rate, P2P regression rate, plus FixEval-style patch scoring when a reference patch exists.

## Steps

1. Code is converted into a Knowledge DB using a parser -> Parser.py
2. Knowledge DB consists of 
   1. Calls
   2. Library Usage
   3. Dataflow and Resource handling
   4. Configuration and Environment
   5. Control flow
   6. Security
3. Graph is generated from the Knowledge DB using Constructor.py
4. A detector then identifies the violations in the graph using Detector.py
5. The violations are then patched using Patcher.py
6. Then the patched code is used to update the Knowledge DB and the process is repeated until no violations are found -> Updater.py
7. The final patched code is then outputted as the final result and forwared to Integrater which integrates the patched code into the original codebase.
8. A independent tester will verify the patch from exisiting code to new patched code and what was expected and mark the accuracy of patch.


## Graph Definations

1. A node will have
   1. Node ID
   2. Node Expectation (what it expects to get for ex. a function call expects a return value) as a embedding
   3. Node Connected Nodes (what nodes it is connected to) as a embedding
   4. Node Itself as a embedding
2. Node will be represented as Embeddings. -> Embedding.py (A Encoder.py and Decoder.py will be used to generate the embeddings and integrate the embeddings into the Knowledge DB/Codebase)

## Violations

If Node Expectationa and Connected Nodes arent matching then it is a violation. Then we find a node which is the most similar to the Node Expectation and Connected Nodes and then we patch the code to make it match.
For better matching, we will have a Node Embedding Match Table to get easy for matching (Node ID and Embedding) -> NodeEmbeddingMatchTable.py

## Model for Embedding

UnixCoder or CodeBert will be used to generate the embeddings for the nodes. The embeddings will be generated for Node Expectation, Connected Nodes, and Node Itself. The embeddings will be stored in the Knowledge DB for future reference. (Initialize Embedding Model in Embedding.py)

## UnixCoder Base*
(https://arxiv.org/pdf/2203.03850)

Jobs:

1. Code and NL Embeddings
2. Similarity between code and NL
3. Decoder-only Mode
4. Encoder-Decoder Mode

## Future Work

Can be classified as a Model and above approach to train the model

## Benchmarking

1. A benchmark suite will have a Task T1, Code C1 (Input), Verify against Code C2, Score how much better C2 was over C1 (did it reduce violations or not.)
   - Suite record fields (implemented in src/benchmark): `id` (T1), `code` (C1), `code_after` (C2), optional `label`/`category` and tests (`tests` or `entry_point`+`input_output`).
   - The runner runs the same checks on C1 and C2: per-case line `delta: X -> Y violations (reduced|same|increased); tests fail -> pass`; metrics aggregate into `repair_delta` (violations_before/after, reduced/same/increased, tests_fail_to_pass = F2P flips, tests_pass_to_fail = regressions).
   - Verdict stays based on C2's cleanliness so "fewer violations" and "fully clean" are distinguishable; improvement is reported separately.
   - Example: `src/benchmark/examples/delta.jsonl`. This is the runner-side implementation of the BugSwarm/FixEval repair-delta design above.
2.