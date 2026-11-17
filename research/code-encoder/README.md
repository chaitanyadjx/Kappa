# Code encoders and decoders for code patching

A curated bibliography for the Kappa design in [`../Method.md`](../Method.md), where code and graph representations are embedded and a patcher changes source code to satisfy expected graph relationships.

**Scope.** The core list prioritizes code-centric learned repair, code-to-code generation, structured edit prediction, and non-LLM patching. It is not restricted to systems that take code as their only signal: constrained compiler/static-analysis feedback and retrieval are identified where relevant. NL-guided systems are not a focus. The final section contains reusable code-representation/pretraining foundations; these papers do **not** by themselves demonstrate automated repair. Traditional pattern/template systems are included only as relevant structured-patch comparisons. No general-purpose LLM-based repair paper is treated as a core method.

## A. Direct code-centric patch generation and learned repair

1. **DeepFix: Fixing Common C Language Errors by Deep Learning** — Rahul Gupta, Soham Pal, Aditya Kanade, Shirish Shevade. **AAAI 2017 (conference).** An early neural repair system that identifies compiler errors and iteratively generates code changes; a foundational code-to-code baseline. [AAAI proceedings](https://ojs.aaai.org/index.php/AAAI/article/view/10742)

2. **On Learning Meaningful Code Changes via Neural Machine Translation** — Michele Tufano, Jevgenija Pantiuchina, Cody Watson, Gabriele Bavota, Denys Poshyvanyk. **ICSE 2019 (conference).** Learns transformations from before/after code, relevant to encoding buggy code context and decoding developer-like edits; broader code-change learning rather than a standalone APR system. [DOI](https://doi.org/10.1109/ICSE.2019.00021) · [arXiv](https://arxiv.org/abs/1901.09102)

3. **An Empirical Study on Learning Bug-Fixing Patches in the Wild via Neural Machine Translation** — Michele Tufano, Cody Watson, Gabriele Bavota, Massimiliano Di Penta, Martin White, Denys Poshyvanyk. **ACM Transactions on Software Engineering and Methodology 28(4), 2019 (journal).** Studies learning real-world bug-fix changes from code; useful for patch-dataset characteristics and limits of learned code-to-code translation. [ACM DOI](https://doi.org/10.1145/3340544)

4. **SequenceR: Sequence-to-Sequence Learning for End-to-End Program Repair** — Zimin Chen, Steve Kommrusch, Michele Tufano, Louis-Noël Pouchet, Denys Poshyvanyk, Martin Monperrus. **IEEE Transactions on Software Engineering 47(9), 2021 (journal).** Code-in/code-out sequence repair with a copy mechanism for identifiers and out-of-vocabulary tokens. [DOI](https://doi.org/10.1109/TSE.2019.2940179) · [arXiv](https://arxiv.org/abs/1901.01808)

5. **DLFix: Context-Based Code Transformation Learning for Automated Program Repair** — Yi Li, Shaohua Wang, Tien N. Nguyen. **ESEC/FSE 2020 (conference).** A two-layer tree-based model learns both fix context and code transformations, directly relevant to contextual code encoding and structured patch decoding. [ACM record](https://dl.acm.org/doi/10.1145/3377812.3390896)

6. **CoCoNuT: Combining Context-Aware Neural Translation Models Using Ensemble for Program Repair** — Thibaud Lutellier, Hung Viet Pham, Lawrence Pang, Yitong Li, Moshi Wei, Lin Tan. **ISSTA 2020 (conference).** Combines context-aware neural translation models to generate patches from buggy code and surrounding code. [ACM record](https://dl.acm.org/doi/10.1145/3395363.3397369)

7. **CURE: Code-Aware Neural Machine Translation for Automatic Program Repair** — Nan Jiang, Thibaud Lutellier, Lin Tan. **ICSE 2021 (conference).** Augments neural translation with code-aware search and project vocabulary handling to improve generated patches. [IEEE record](https://doi.org/10.1109/ICSE43902.2021.00107) · [arXiv](https://arxiv.org/abs/2103.00073)

8. **Break-It-Fix-It: Unsupervised Learning for Program Repair** — Michihiro Yasunaga, Percy Liang. **ICML 2021 (conference).** Trains bug-induction and repair models in a cycle, reducing dependence on paired human bug/fix examples; a code-centric learning strategy. [PMLR proceedings](https://proceedings.mlr.press/v139/yasunaga21a.html)

9. **TFix: Learning to Fix Coding Errors with a Text-to-Text Transformer** — Berkay Berabi, Jingxuan He, Veselin Raychev, Martin Vechev. **ICML 2021 (conference).** Generates code fixes for static-analysis warnings. The repair task is code generation, but warning information is part of the input, so it is not a strictly code-only setup. [PMLR proceedings](https://proceedings.mlr.press/v139/berabi21a.html)

10. **A Syntax-Guided Edit Decoder for Neural Program Repair** — Qihao Zhu, Zeyu Sun, Yuan-an Xiao, Wenjie Zhang, Kang Yuan, Yingfei Xiong, Lu Zhang. **ESEC/FSE 2021 (conference).** Introduces Recoder, which predicts syntax-guided edits and placeholders instead of regenerating the entire method; especially relevant to a decoder that emits graph/code edits. [ACM record](https://dl.acm.org/doi/10.1145/3468264.3468544) · [arXiv](https://arxiv.org/abs/2106.08253)

11. **GrasP: Graph-to-Sequence Learning for Automated Program Repair** — Ben Tang, Bin Li, Lili Bo, Xiaoxue Wu, Sicong Cao, Xiaobing Sun. **QRS 2021 (conference).** Encodes an AST-derived graph and decodes a repair sequence; close to graph-embedding-to-code-patch design. [IEEE record](https://ieeexplore.ieee.org/document/9724652)

12. **Applying CodeBERT for Automated Program Repair of Java Simple Bugs** — Ehsan Mashhadi, Hadi Hemmati. **MSR 2021 (conference).** Fine-tunes a pretrained code encoder for code-to-code repair on small Java bugs; a direct encoder-backbone precedent. [IEEE record](https://doi.org/10.1109/MSR52588.2021.00063) · [arXiv](https://arxiv.org/abs/2103.11626)

13. **Neural Program Repair with Execution-Based Backpropagation** — He Ye, Matias Martinez, Martin Monperrus. **ICSE 2022 (conference).** RewardRepair incorporates compiler/test execution feedback into optimization of neural patch generation, relevant to validating iterative patches. [ACM record](https://dl.acm.org/doi/10.1145/3510003.3510222) · [arXiv](https://arxiv.org/abs/2105.04123)

14. **DEAR: A Novel Deep Learning-Based Approach for Automated Program Repair** — Yi Li, Shaohua Wang, Tien N. Nguyen. **ICSE 2022 (conference).** Targets multi-statement and multi-hunk changes, useful when a repair decoder must produce coordinated edits rather than a single-token replacement. [ACM record](https://dl.acm.org/doi/10.1145/3510003.3510177)

15. **VulRepair: A T5-Based Automated Software Vulnerability Repair** — Michael Fu, Chakkrit Tantithamthavorn, Trung Le, Van Nguyen, Dinh Q. Phung. **ESEC/FSE 2022 (conference).** Applies an encoder–decoder model to vulnerability-fix generation; domain-specific patching rather than general bug repair. [ACM record](https://doi.org/10.1145/3540250.3549098)

16. **Tare: Type-Aware Neural Program Repair** — Qihao Zhu, Zeyu Sun, Wenjie Zhang, Yingfei Xiong, Lu Zhang. **ICSE 2023 (conference).** Adds type constraints to encoder–decoder repair to reduce untypable patches; relevant to enforcing semantic constraints on generated code. [IEEE record](https://ieeexplore.ieee.org/document/10172781)

17. **Template-Based Neural Program Repair** — Xiangxin Meng, Xu Wang, Hongyu Zhang, Hailong Sun, Xudong Liu, Chunming Hu. **ICSE 2023 (conference).** TENURE combines learned generation with repair templates, offering a hybrid structured-decoder alternative to unconstrained token generation. [DOI](https://doi.org/10.1109/ICSE48619.2023.00127)

18. **Embedding Context as Code Dependencies for Neural Program Repair** — Noor Nashid, Mifta Sintaha, Ali Mesbah. **ICST 2023 (conference).** GLANCE uses dependency context and graph-to-sequence learning to provide nonlocal code information to patch generation. [IEEE record](https://ieeexplore.ieee.org/document/10132257)

19. **SeqTrans: Automatic Vulnerability Fix Via Sequence to Sequence Learning** — Jianlei Chi, Yu Qu, Ting Liu, Qinghua Zheng, Heng Yin. **IEEE Transactions on Software Engineering 49(2), 2023 (journal).** Uses sequence-to-sequence learning for security patch generation; useful as a vulnerability-specific code decoder study. [DOI](https://doi.org/10.1109/TSE.2022.3156637)

20. **Neural Transfer Learning for Repairing Security Vulnerabilities in C Code** — Zimin Chen, Steve Kommrusch, Martin Monperrus. **IEEE Transactions on Software Engineering 49(1), 2023 (journal).** Studies transfer learning for neural vulnerability repair, relevant where Kappa’s repair corpus is limited. [DOI](https://doi.org/10.1109/TSE.2022.3147265) · [arXiv](https://arxiv.org/abs/2104.08308)

21. **Impact of Code Language Models on Automated Program Repair** — Nan Jiang, Kevin Liu, Thibaud Lutellier, Lin Tan. **ICSE 2023 (conference).** Evaluates code language models on repair and fine-tuning for APR; directly relevant to selecting an encoder backbone, while distinguishing model capability from general-purpose LLM repair. [IEEE record](https://doi.org/10.1109/ICSE48619.2023.00125) · [arXiv](https://arxiv.org/abs/2302.05020)

22. **RAP-Gen: Retrieval-Augmented Patch Generation with CodeT5 for Automatic Program Repair** — Weishi Wang, Yue Wang, Shafiq R. Joty, Steven C. H. Hoi. **ESEC/FSE 2023 (conference).** Retrieves prior code fixes and conditions CodeT5 patch generation on them; relevant if Kappa retrieves similar graph/code nodes before decoding. [ACM record](https://doi.org/10.1145/3611643.3616256)

## B. Structured patch representations and learned repair patterns

23. **Getafix: Learning to Fix Bugs Automatically** — Rahul Bader, Joseph Scott, Michael Pradel, Satish Chandra. **OOPSLA 2019 / PACMPL (journal-conference).** Learns hierarchical fix patterns from code changes and applies them to similar code; not a neural encoder–decoder, but a strong structured-edit comparison. [ACM DOI](https://doi.org/10.1145/3360585)

24. **FixMiner: Mining Relevant Fix Patterns for Automated Program Repair** — Anil Koyuncu, Kui Liu, Tegawendé F. Bissyandé, Dongsun Kim, Jacques Klein, Martin Monperrus, Yves Le Traon. **Empirical Software Engineering (journal, 2020).** Mines structured edit patterns and uses them for repair, useful for comparison with embedding-based nearest-neighbor matching. [Springer DOI](https://doi.org/10.1007/s10664-019-09780-z)

25. **TBar: Revisiting Template-Based Automated Program Repair** — Kui Liu, Anil Koyuncu, Dongsun Kim, Tegawendé F. Bissyandé. **ISSTA 2019 (conference).** A widely used template-based repair system; useful as a non-neural baseline for explicit patch operators and donor retrieval. [ACM record](https://doi.org/10.1145/3293882.3330577)

26. **GAMMA: Revisiting Template-Based Automated Program Repair via Mask Prediction** — Quanjun Zhang et al. **ASE 2023 (conference).** Revisits template repair and donor selection with mask prediction, a structured prediction approach relevant to selecting code fragments for a patch. [IEEE record](https://ieeexplore.ieee.org/document/10298335) · [arXiv](https://arxiv.org/abs/2309.09308)

## C. Code encoder / encoder–decoder foundations (not repair evaluations)

These papers provide candidate representations or architectures for Kappa’s embedding and patching stages. Their original evaluations are code understanding/generation tasks, not evidence that they solve Kappa’s repair problem.

27. **CodeBERT: A Pre-Trained Model for Programming and Natural Languages** — Zhangyin Feng, Daya Guo, Duyu Tang, Nan Duan, Xiaocheng Feng, Ming Gong, Linjun Shou, Bing Qin, Ting Liu, Daxin Jiang, Ming Zhou. **Findings of EMNLP 2020 (conference proceedings).** A pretrained code-aware encoder and practical initialization for code representation; the original paper is not an APR evaluation. [ACL Anthology](https://aclanthology.org/2020.findings-emnlp.139/)

28. **GraphCodeBERT: Pre-training Code Representations with Data Flow** — Daya Guo, Shuo Ren, Shuai Lu, Zhangyin Feng, Duyu Tang, Shujie Liu, Long Zhou, Nan Duan, Alexey Svyatkovskiy, Shengyu Fu, Michele Tufano, Shao Kun Deng, Colin B. Clement, Dawn Drain, Neel Sundaresan, Jian Yin, Daxin Jiang, Ming Zhou. **ICLR 2021 (conference).** Adds data-flow edges to code pretraining, making it especially relevant to embedding call/dataflow relationships in Kappa’s knowledge graph. [arXiv](https://arxiv.org/abs/2009.08366)

29. **Unified Pre-training for Program Understanding and Generation (PLBART)** — Wasi Uddin Ahmad, Saikat Chakraborty, Baishakhi Ray, Kai-Wei Chang. **NAACL-HLT 2021 (conference).** A pretrained sequence-to-sequence code model for understanding and generation; reusable as an encoder–decoder foundation, not a repair result. [ACL Anthology](https://aclanthology.org/2021.naacl-main.211/)

30. **CodeT5: Identifier-aware Unified Pre-trained Encoder-Decoder Models for Code Understanding and Generation** — Yue Wang, Weishi Wang, Shafiq Joty, Steven C. H. Hoi. **EMNLP 2021 (conference).** Encoder–decoder pretraining explicitly uses identifier information; relevant to preserving names and generating localized patches. [ACL Anthology](https://aclanthology.org/2021.emnlp-main.685/)

31. **UniXcoder: Unified Cross-Modal Pre-training for Code Representation** — Daya Guo, Shuai Lu, Nan Duan, Yanlin Wang, Ming Zhou, Jian Yin. **ACL 2022 (conference).** Supports code representation and generation with configurable attention; a candidate code encoder/decoder backbone, not itself an APR evaluation. [ACL Anthology](https://aclanthology.org/2022.acl-long.499/)

32. **code2vec: Learning Distributed Representations of Code** — Uri Alon, Meital Zilberstein, Omer Levy, Eran Yahav. **POPL 2019 (conference).** Learns method-level representations from AST paths; useful as a structured embedding baseline, though it is not a patch decoder. [ACM DOI](https://doi.org/10.1145/3290353)

33. **code2seq: Generating Sequences from Structured Representations of Code** — Uri Alon, Shaked Brody, Omer Levy, Eran Yahav. **ICLR 2019 (conference).** Encodes AST paths and decodes sequences, providing a structured encoder–decoder design relevant to code edits. [ICLR record](https://iclr.cc/virtual/2019/poster/646) · [arXiv](https://arxiv.org/abs/1808.01400)

34. **A Survey of Learning-Based Automated Program Repair** — Quanjun Zhang, Chunrong Fang, Yuxiang Ma, Weisong Sun, Zhenyu Chen. **ACM Computing Surveys (journal, 2024).** A recent map of learning-based APR methods and model families; useful for finding additional repair-specific encoder/decoder work and understanding evaluation practices. [ACM DOI](https://doi.org/10.1145/3631974)

35. **Neural Program Repair: Systems, Challenges and Solutions** — Wenkang Zhong, Chuanyi Li, Jidong Ge, Bin Luo. **Internetware 2022 (conference).** Surveys neural repair systems and their encoder–decoder formulations; useful for tracing methods and limitations, not itself a new patch model. [ACM record](https://doi.org/10.1145/3545258.3545268) · [arXiv](https://arxiv.org/abs/2202.10868)

## Newer surveys and reviews on the state of APR (2025–2026)

These reviews are more appropriate starting points for a current field overview than adding more individual repair systems. The original bibliography’s item 34, **A Survey of Learning-Based Automated Program Repair** (Zhang et al., ACM Computing Surveys, 2024), is the foundational learning-based APR survey and remains the best starting point. The 2026 entries below are peer-reviewed journal articles according to their publisher/DOI records as of November 2026.

1. **Learning-Based Automated Program Repair: A Systematic Literature Review** — Qijia Chen, Dongcheng Li, Man Zhao, W. Eric Wong, Hui Li. **Complex System Modeling and Simulation 5(4), 2025, pp. 305–322.** Broad overview of learning-based APR divided into supervised, unsupervised, transfer, ensemble, and language-model learning. [DOI](https://doi.org/10.23919/CSMS.2025.0004) · [SciOpen record](https://www.sciopen.com/article/10.23919/CSMS.2025.0004)

2. **Advancements in Automated Program Repair: A Comprehensive Review** — Sena Dikici, Turgay Tugay Bilgin. **Knowledge and Information Systems 67(6), 2025, pp. 4737–4783.** Broad APR review comparing template-based, machine-learning, and deep-learning approaches across 41 tools; useful for positioning learning-based systems among other APR families. [Springer DOI](https://doi.org/10.1007/s10115-025-02383-9)

3. **Survey of Large-Language-Model-Based Automated Program Repair** — Xu Pengyu, Kuang Boyu, Su Mang, Fu Anmin. **Journal of Computer Research and Development 62(8), 2025, pp. 2040–2057.** LLM-specific APR review that organizes techniques into cloze-style and neural-machine-translation-style approaches and surveys datasets, metrics, challenges, and directions. [Journal record](https://doi.org/10.7544/issn1000-1239.202440467)

4. **A Systematic Literature Review on Large Language Models for Automated Program Repair** — Quanjun Zhang, Chunrong Fang, Yang Xie, Yuxiang Ma, Weisong Sun, Yun Yang, Zhenyu Chen. **ACM Transactions on Software Engineering and Methodology (journal), 2026.** Systematic review of 189 papers from 2020–2025; categorizes LLMs and their deployment strategies, repair scenarios, datasets/open-science issues, challenges, and future directions. [ACM DOI](https://doi.org/10.1145/3799693) · [arXiv preprint](https://arxiv.org/abs/2405.01466)

5. **Patch Generation in APR: A Survey from the Perspectives of Utilizing LLMs and Using APR-Specific Information** — Yaopeng Yang, Chuanyi Li, Zhifeng Han, Rui Li, Kui Xu, Qingyuan Li, Wenkang Zhong, Zongwen Shen, Zhiwei Fei, Jidong Ge, Bin Luo. **ACM Transactions on Software Engineering and Methodology 35(8), 2026, Article 255.** Focuses specifically on patch generation, synthesizing 124 studies around LLM use and APR-specific information such as bug-fix pairs, context, and error messages; covers generation configuration and patch evaluation. [ACM DOI](https://doi.org/10.1145/3764584)

6. **Unveiling the Power of Code Pre-Trained Models in Automated Program Repair: A Systematic Review** — Shanggui Zhan, Xingqi Wang, Dan Wei, Xin Xiang, Yangbo Lin, Huizhe Wu, Junchao Lu. **Journal of Systems and Software 241, 2026, Article 113015.** Closest match to Kappa’s encoder/backbone question; reviews code-pretrained model architectures and their integration strategies in APR. [DOI](https://doi.org/10.1016/j.jss.2026.113015) · [ScienceDirect record](https://www.sciencedirect.com/science/article/pii/S0164121226002487)

7. **A Survey of LLM-based Automated Program Repair: Taxonomies, Design Paradigms, and Applications** — Boyang Yang, Zijian Cai, Fengling Liu, Xuan Bach D. Le, Lingming Zhang, Tegawendé F. Bissyandé, Yang Liu, Haoye Tian. **arXiv preprint, 2025; version updated August 2026 (not yet a peer-reviewed venue publication identified).** Organizes 62 LLM-based APR systems by parameter adaptation and control authority, and discusses retrieval/analysis augmentation. [arXiv](https://arxiv.org/abs/2506.23749)

8. **Patch Correctness Assessment: A Survey** — Zhiwei Fei, Jidong Ge, Chuanyi Li, Tianqi Wang, Yuning Li, Haodong Zhang, LiGuo Huang, Bin Luo. **ACM Transactions on Software Engineering and Methodology 34(2), 2025.** Focused survey of patch overfitting and automated patch-correctness assessment, including approaches, datasets, metrics, effectiveness, and open problems; important for evaluating whether Kappa’s generated patches are actually correct. [ACM DOI](https://doi.org/10.1145/3702972)

9. **Automated Bug Detection and Program Repair Using Deep Learning: A Comprehensive Review** — Rawaa Ali, Adala Chyaid. **Computing and Informatics 45 (2026).** Review of deep-learning and LLM approaches for bug detection and repair, covering benchmark datasets, algorithms, and frameworks. This is broader and less APR-specialized than the ACM/Springer reviews above. [DOI](https://doi.org/10.2478/cait-2026-0006)

10. **A Survey on Automated Program Repair Techniques** — Kai Huang, Zhengzi Xu, Su Yang, Hongyu Sun, Xuejun Li, Zheng Yan, Yuqing Zhang. **arXiv preprint, 2023.** Broad survey with a comparison framework for APR tools and discussion of the field’s state; preprint, so use as a discovery map rather than a substitute for the peer-reviewed reviews. [arXiv](https://arxiv.org/abs/2303.18184)

### Suggested survey reading order

1. Start with **A Survey of Learning-Based Automated Program Repair** for the overall learning-based pipeline and components.
2. Read **Patch Generation in APR** for recent patch-generation strategies and APR-specific context; then **Unveiling the Power of Code Pre-Trained Models** for code-encoder/backbone choices.
3. Use **A Systematic Literature Review on LLM-based APR** and the **LLM-based APR taxonomy** for the current LLM-heavy landscape.
4. Read **Patch Correctness Assessment** alongside the above to understand patch overfitting and validation, a key limitation of test-guided repair.
5. Use **Advancements in Automated Program Repair** as a cross-check for classical/template-based APR families, and **Learning-Based Automated Program Repair: A Systematic Literature Review** for a second broad learning-based taxonomy.

## Reading priorities for Kappa

For direct architectural matches, start with **Recoder** (edits rather than whole-method regeneration), **GrasP** and **GLANCE** (graph-to-sequence), **DLFix** (tree/context encoding), **GraphCodeBERT** (data-flow-aware code representation), and **Tare** (type constraints). Compare them with **SequenceR**, **CURE**, and **CoCoNuT** for token-sequence encoder–decoders, and with **Getafix/FixMiner/TBar** for explicit edit-pattern retrieval. Treat **CodeBERT, GraphCodeBERT, PLBART, CodeT5, and UniXcoder** as candidate backbones—not as proof that an embedding-similarity patch strategy will be correct.

**Bibliographic note:** The bibliography contains the original 35 entries plus 10 additional, distinct survey/review papers. The original list already includes its 2024 learning-based APR survey and 2022 neural-repair review; neither is duplicated in the new list. Preprints are labeled separately and are not counted as separate versions of peer-reviewed works.
