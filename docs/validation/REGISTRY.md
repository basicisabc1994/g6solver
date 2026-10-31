# Validation registry

A study is entered here before any arm reads its evaluation split (VALIDATION_PLAN.md rule 2). Entries are
append-only. A change to anything an entry freezes re-registers the study under a new entry, and the old entry
stays. Hashes are SHA-256. Code and text files are hashed with LF line endings, which is how the repository stores
them; data files are hashed as downloaded. A hash freezes a file as of its entry's commit. A later entry may change
the same file for another study (the plan file grows study by study), and the earlier version stays in git history at
the commit the entry names.

## V1 — grounding (registered 2026-10-18)

- **Plan:** `oss/docs/validation/VALIDATION_PLAN.md` v0.2, `259070e7c60f695cdecb8a019e16f2af650b53356d5ee0b85bd7dc24b0b6f805`
  (§0, §0.1 and §V1 govern).
- **G6 code:** git tree `7dc142e23706079c778a2123970b8e7564902eba` of `oss/src` (commit `2986bdbd41`).
- **Scripts:**
  - `oss/validation/v1_grounding/derive_pairs.py` `30ca0103668dcbc1588fbe22a3c1c8b132e2972ce82ce72f0e2cdac125e8cec8`
  - `oss/validation/v1_grounding/run.py` `a9ee2ac89bb0d95dca45181957c06d9e07327091efac4b6d85d6b3e3b1c39f7a`
  - `oss/validation/v1_grounding/tune_relevance.py` `7c2b64269e672c76f5d33c76aafda5f1d2f4e1a2b4901b327e3c9732bc1f0be7`
- **Data:** VitaminC (Schuster, Fisch and Barzilay, NAACL 2021) from Hugging Face `tals/vitaminc` at revision
  `be6febb761b0b2807687e61e0b5282e459df2fa0`, under Wikipedia's terms, else CC BY-SA 3.0:
  - `LICENSE` `e5727854e203fc66d725caa6d8e97e541f64a906d1d15d19e63f32bb52134dd1`
  - `dev.jsonl` (development: the stratum rule and the threshold) `544934677f5d133873e6d38f4557f8966f4efa5d3d70874ffe6913f2091b86b5`,
    32,876,983 bytes, 63,054 rows
  - `test.jsonl` (evaluation; not read before this entry) `7ad1808dbc30c62e0a1427a53022d0dfaff668a1fde3c4b612a2d266edd753ad`,
    28,717,099 bytes
- **Sampling:** seed `20261018`, up to 100 pairs per stratum, one pair per `case_id` within a stratum.
- **Relevance baseline:** threshold `1.4189`, from `tune_relevance.py` on the dev pairs (balanced accuracy 0.696 there).
- **NLI arm:** Ollama 0.40.2, `max_tokens` 2000, and the prompt in `run.py` `arm_nli`, run once per model:
  - `gpt-oss:20b` `9ba9cc2b4e02463b933096090530aef679d534dd91ecc9afadfb2dbfdc59f3c5` (20.9B, MXFP4)
  - `llama3.1:8b` `46e0c10c039e019119339687c3c1757cc81b9da49709a3b3924863ba87ca666e` (8.0B, Q4_K_M)
- **Endpoint and verdict:** as §V1. The 95% upper bound is the larger of Wilson and the cluster bootstrap over
  `case_id` (10,000 resamples, seed 20261018).
- **Development history, disclosed:**
  - On dev, the negated rule was narrowed twice: incidental negations in long passages, then tokenised
    contractions.
  - After a dev probe, the NLI token budget was raised from 200 to 2000.
  - The runner's error accounting and bootstrap were added.
  - A dev dry run without NLI gave `check_claim` 0/170 false support and 0.90 quoted recall; the relevance baseline
    gave 0.23 false support. These are development estimates, not results.
- **Not audited:** no second person has checked the stratum assignment.
- **Outcome (appended 2026-10-18, after the run):** the frozen scripts ran unchanged on `test` (474 pairs drawn). The verdict is `component_validated_no_transfer`: `check_claim` gave 0/174 false support (Wilson upper bound 2.2%) and 91/100 quoted recall, and beat the relevance baseline in 56 of 56 discordant pairs. Results, misses and the claim ledger are in `results/V1/README.md`.

## V2 — action gating (registered 2026-10-18)

- **Plan:** `oss/docs/validation/VALIDATION_PLAN.md`, `1a7d466d47198e1274661d44ba7a2bc7f20c25a685bf04895cd77f46138f572c`
  (§0, §0.1 and §V2 govern).
- **G6 code:** git tree `1edb84ed966d0c104f7241dc3764fd0eb9cae4cf` of `oss/src` (commit `13eca3e5b6`).
- **Scripts:**
  - `oss/validation/v2_action_gating/mapping.py` `8af59819bcd4dd1b7c0137675c3b5fccedfc326ad4ed339000caf217860689ef`:
    written from the 69 tool signatures (names, parameters, descriptions) only. It covers every tool, and every
    target is a real parameter.
  - `oss/validation/v2_action_gating/extract_calls.py` `e64cf8a9d2fb3d454063a4a7d826ad911b054eb400e3d55b5efb90ae321cf28a`
  - `oss/validation/v2_action_gating/run.py` `b0687f7e2857484165f3b64686153fac14d534918fba3531e2d6e7c1d5cf13e3`
- **Data:** AgentDojo 0.1.35 (Debenedetti et al., NeurIPS 2024 Datasets and Benchmarks; MIT), wheel
  `agentdojo-0.1.35-py3-none-any.whl` `364bea4219716b716bf639f504d195943f7f6a5535d312ca41d7098704a2affd`, benchmark
  `v1.2.1`. It has 4 suites (workspace, travel, banking, slack), 97 user tasks and 35 injection tasks, in a separate
  virtual environment. No task's ground truth had been run or read before this entry.
- **Context seed:** `20261018`. It picks the user task each injection task's calls are judged against.
- **Judge arm:** Ollama 0.40.2, `gpt-oss:20b` `9ba9cc2b4e02463b933096090530aef679d534dd91ecc9afadfb2dbfdc59f3c5`,
  `max_tokens` 2000, and the prompt in `run.py` `JUDGE_SYSTEM` / `arm_judge`. One call per tool call.
- **Endpoints and verdict:** as `run.py`'s docstring, which lists the verdict rules in order.
- **Development history, disclosed:**
  - All four arms ran once on the four made-up calls of `SMOKE_FIXTURE_NOT_EVALUATION_DATA.jsonl`, to check that
    they run. No error occurred.
  - The pre-registered expectation (plan §V2): `insufficient_failure_opportunities`, because the allow-list cannot
    miss a harmful call labelled this way.
- **Not audited:** no second person has checked the mapping table.
- **Outcome (appended 2026-10-18, after the run):** the frozen scripts ran unchanged (386 calls: 30 harmful, 339 benign, 17 injection read steps). The verdict is `unsafe_or_regressive`: the gate with declared facts let 1 harmful call run (`update_password`), and the allow-list let none. The miss is finding V2-F1. Results are in `results/V2/README.md`.
- **Development measurement (appended 2026-10-18, after the V2-F1 fix; not a validation):** the frozen runner, without the judge, re-ran on the same 386 calls with the fixed gate. The gate with declared facts missed 0 of 30 harmful calls; exactly two answers changed, both `update_password` (the injected change, and the user task's own, which now asks). This population has been seen, so the verdict above stands; a validated claim needs a new registration on unseen calls.

## V6 — meta-harness customisation (registered 2026-10-19)

- **Protocol:** `oss/validation/v6_metaharness/PROTOCOL.md` `e1151edd68075734fd33c1a750d802ca51ef55415779577bca0dcb63a6aeb167`.
  It governs together with `docs/transition/META_HARNESS_PLAN.md` §7.1-§7.3
  (`98cad01623553bbbfb1e48d89413dfa97caa5f6b2547c7435abec58d057130b9`) and `oss/docs/validation/VALIDATION_PLAN.md` §0
  (`1a7d466d47198e1274661d44ba7a2bc7f20c25a685bf04895cd77f46138f572c`, the version V2 registered; not edited). Its §10
  lists every departure from §7.1 and §0, with the reason.
- **G6 code:** no git command was run for this study, so no commit is named; the code is pinned by content.
  `g6.integrity.manifest_sha256` over every `oss/src/g6/**/*.py`, each file hashed with LF line endings:
  `41fcf10f53cf539a7a618970c704e8d2caf42eb9e8aa8b253931f73bf0aa4832`. The meta-gate's judging code,
  `g6.metaharness.judging_sha256()`: `104ee34c0b989aa37ea12547bf35dde9fc226036c00bddd021bc0631819c1c8b`. The runner
  records both at the start and the end of the run.
- **Scripts:**
  - `oss/validation/v6_metaharness/workflows.py` `9fbae7c43db2727c2076b1cebd9de4cfc10bb7e9ae7615701a19290c1263eb22`
  - `oss/validation/v6_metaharness/run.py` `0ef4dd6d1c864694ced411c88f661aff9728ccb27b9222861b18e23bef2ab435`
  - `oss/validation/v6_metaharness/analyze.py` `26ef4cc5f992b0acb67693d43499843d8cc02da90de33c47a18e12d9ebdd8b3a`
- **Splits:** `oss/docs/validation/results/V6/splits.json` `a5c3d542eaf27bb04eedc8fba12dc924d110b5a403cf43603bfe07127dbc90fc`.
  Seed `20261019`; per workflow 20 dev, 40 gate (the meta-gate's own held-out) and 60 eval (the study's held-out); the
  draw read ids only.
- **Data** (in `$V6_DATA`, as downloaded; SHA-256, bytes):
  - GSM8K (Cobbe et al. 2021; MIT), `openai/grade-school-math` commit `3101c7d5072418e28b9008a6636bde82a006892c`:
    `grade_school_math/data/test.jsonl` `3730d312f6e3440559ace48831e51066acaca737f6eabec99bccb9e4b3c39d14`, 749,738 (1,319
    rows); `LICENSE` `86bbb73e855821d7c401912fd4bf82e34313e6e3b6fd6f909f2b6cc9e209a53b`.
  - MASSIVE 1.1 (FitzGerald et al. 2022; CC BY 4.0), `amazon-massive-dataset-1.1.tar.gz` from
    `amazon-massive-nlu-dataset.s3.amazonaws.com` (ETag `51e0da2a3ff7a016f109e1d1b4306e93-3`)
    `4cba5faa11c71437928e17cb1b9b3d8b8e727e7ea363a3a9a8045e19c0491577`, 40,251,390; extracted `1.1/data/en-US.jsonl`
    `c70f75c6a543a26e249ec383df67733ad9b1066f6c0406c2e04a3f03356e407e`, 3,904,197 (2,974 test rows); `1.1/LICENSE`
    `c2e6ea015269147de02117ebdd91f30ef09831251f5345fa8365273b1db1d435`; `1.1/NOTICE.md` (derived from SLURP, CC BY 4.0)
    `b90534ccd20c6f0e1e5239567af0d150496339542b75a15bfbc3e1e737593ddb`.
  - SciFact (Wadden et al. 2020), `data.tar.gz` from `scifact.s3-us-west-2.amazonaws.com/release/latest` (ETag
    `cb7da4d8609e30f2c7483b61aa447f7e`) `11c621288d41ac144d29b13b0f8503b3820b7d6e8b1f6ff24dff335c196d76be`, 3,115,079;
    extracted `data/claims_train.jsonl` `f4c8fa82d8bd0653a9cc8d61a6ea48c25eacea64e90af5dbf390ebb1b74372f0`, 175,616 (809
    claims) and `data/corpus.jsonl` `b8d6c89624cb2ed74dee8938effc4f5d8bd2086887880af8110d64be4ceade62`, 8,307,875. Licence:
    `allenai/scifact` commit `68b98a56d93e0f9da0d2aab4e6c3294699a0f72e` `LICENSE.md`
    `56ee69bc639cbab8f0b2e4b67091ec5e9390aa99a1d65f0a35e4274686c9a806` (claims CC BY 4.0, abstracts ODC-By 1.0); the
    Hugging Face card says CC BY-NC 2.0. Train claims only, because V5 plans SciFact dev.
  - MBPP sanitized (Austin et al. 2021; CC BY 4.0 per the dataset card; the repository is Apache-2.0),
    `google-research/google-research` commit `f46ca8374b4cddef97ca4208ad986049d74d296a`: `mbpp/sanitized-mbpp.json`
    `ca95deaa9a01ef0a6f439f88bcf0dd3db3563d22f22aad6cae04ebb9a8d8c8e9`, 255,053 (427 rows; 257 with test task ids 11-510);
    `mbpp/README.md` `02d63a4ffdad806c3c87b5ea2aa91e946f6eec0c2869643b09831650883f41be`; `LICENSE`
    `cfc7749b96f63bd31c3c42b5c471bf756814053e847c10f3eb003417bc523d30`.
  - Development only, replaced before this entry (PROTOCOL §10 item 12): Banking77 (Casanueva et al. 2020; CC BY 4.0),
    `PolyAI-LDN/task-specific-datasets` commit `57ec275d8078af65b7731c2a98be812d844a6d6b`: `banking_data/test.csv`
    `d12d6e3bc4c3103966ae786dc435913c0c563dfa328f5a3646d0e62cfeeb474d`; `banking_data/categories.json`
    `53261da888122daf2d120d925458631d9619e15d82e56052e7a42e535ce32b63`. Only its 20 dev cases were run.
  - No item is in V1 (VitaminC), V2 (AgentDojo), V3, V4 (TimeMachine-bench) or the planned V5 populations.
- **Model:** Ollama 0.40.2, `llama3.1:8b` `46e0c10c039e019119339687c3c1757cc81b9da49709a3b3924863ba87ca666e` (8.0B,
  Q4_K_M), default sampling, `max_tokens` 2048, for every arm. No 20B arm (PROTOCOL §10 item 2).
- **Arms, endpoints and verdict:** PROTOCOL §5-§8. Every confirmation and activation is scripted, under the name
  "v6-scripted-approval (study script, not a person)"; no person approves anything in this study.
- **Development history, disclosed:**
  - A speed probe of `llama3.1:8b` and `gpt-oss:20b` on one made-up word problem (no dataset item).
  - On the library's own test rig (`tests/metaharness/_rig.py`, a stub model), a second `propose(HintStrategy(store))` on
    the same store fails with `G6_E_LEARNING_ID_EXISTS`. This is finding V6-F1, known before this entry (PROTOCOL §10
    item 10); the library is used as it is.
  - Offline, with no model: every workflow is valid for `adapt`; every one of the 11 or 17 search profiles and arm G's
    profile is accepted by `candidate`; every check passes its own known-good and fails its known-bad outputs; the oracle
    accepts all 20 dev reference answers per workflow and rejects an empty reply.
  - Plumbing runs with `g6.core.llm.FakeLLM` on pool items outside the 120 drawn ids per workflow. They led to four changes
    before this entry: the proposer-view canary matched nothing on GSM8K (its snippet carried the template's label) and
    was fixed, with a positive control (all 20 dev inputs are found in the views); `SearchStrategy(max_candidates)` went
    from 2 to 3, because with 2 it had no new candidate left by cycle 3; an interrupted adaptation now restarts from a
    fresh workspace; rule 6 was narrowed to "no held-out gain over B" (it had fired whenever rule 5 failed).
  - `run.py devcheck` on the dev split only (successes of 20; Banking77 before its replacement):

    | Workflow | A | B | B rerun | prompt 1 | prompt 2 | G |
    |---|---|---|---|---|---|---|
    | gsm8k | 7 | 3 | 4 | 15 | 1 | 10 |
    | massive | 10 | 13 | 11 | 10 | 14 | 10 |
    | scifact | 13 | 12 | 14 | 13 | 12 | 6 |
    | mbpp | 13 | 14 | 11 | 13 | 12 | 13 |
    | banking77 | 11 | 0 | 0 | 0 | 0 | 0 |

    On SciFact 2 of 20 dev cases were blocked at intake as risky in every G6 arm. On Banking77 all 20 were, with no model
    call (`G6_E_PIPELINE_FEASIBILITY_RISKY_CONFIRMATION_REQUIRED`), so it was replaced by MASSIVE and the splits were drawn
    again; the GSM8K, SciFact and MBPP ids are unchanged (checked). This is finding V6-F2: `harness.run(Standard(goal=
    "Classify the message into one of these labels: card_arrival, card_payment_fee_charged. ...", ...), executor)` is
    blocked-escalated, and is `qualified-draft` only with `decisions=("accept_risky",)`, which
    `g6.metaharness.evaluate.run_case` never passes and no workflow or profile field can carry.
- **Power (dev only, `g6.metaharness.power`):** B against its rerun disagreed on 5%, 10%, 20% and 15% of dev cases
  (GSM8K, MASSIVE, SciFact, MBPP; 12.5% pooled). The gate on 40 cases (p ≤ 0.05 / 6, at least 5 discordant) recommends a
  true gain of +0.10 with probability 0.06-0.10, +0.20 with 0.26-0.49 and +0.30 with 0.56-0.83 across the four. E1 on
  240 tasks at the strictest Holm level (0.05 / 4) detects a pooled +0.05 with 0.30, +0.10 with 0.84 and +0.15 with 0.99.
- **Pre-registered expectation:** `inconclusive`. On dev the working prompt lifted GSM8K from 3-4 of 20 to 15 of 20, so the
  gate is expected to recommend it in cycle 1, and D to beat B (and E, which is B if D keeps one call per case) on the
  held-out split. F draws from the same 11 GSM8K options, 4 of which carry that prompt, so F is expected to find it too
  (about 0.9 over four draws), and D > F is not expected. On the other three workflows no declared option moved dev
  success by more than 2 of 20, below what the gate can detect, so few or no activations are expected there.
  `mechanism_demonstrated` would need F to miss the GSM8K prompt; `component_validated_no_transfer` would need the
  gate-approved change not to hold on the held-out split.
- **Not audited:** no second person has checked the workflows, the answer rules, the search options or the splits.
- **Outcome (appended 2026-10-19, after the run):** the frozen scripts ran once, unchanged (01:51-08:09, no crash or
  restart, 9,433 model calls; every pinned hash the same at the end). The verdict is `inconclusive`. D beat B on the
  240 held-out tasks (153 against 121; 34 wins, 2 losses), E (which was B, since D kept one call per case) and F (143;
  Holm p 0.0154), with fewer false accepts (76 against 108). But the whole gain came from GSM8K, the only workflow where
  the gate recommended a change (a step-by-step prompt: 42/60 against 10/60), so the cluster-bootstrap interval of the
  gain is [0.000, 0.404] and its lower bound is not above the oracle bound 0.005 (rule 5 fails; rule 6 does not apply,
  since the gain held on held-out tasks). The expectation registered above was also `inconclusive`, though for another
  reason: F's random draws missed the GSM8K prompt. Controls: A/A p 0.69; planted +0.10 detected in 2,000/2,000; NULL
  0/240; oracle self-test 0 failures in 400; no canary hit; no gaming match. Findings V6-F1 and V6-F2 (library) and
  V6-F3 (with four workflows the registered interval cannot exclude zero unless D gains in three of them; not computed
  before the freeze). Results and the claim ledger are in `results/V6/README.md`.

## V6b — the meta-harness's recovery loop on fresh cases (registered 2026-10-19)

- **Protocol:** `oss/validation/v6b_metaharness/PROTOCOL.md` `5e73f3b06a3c12a2f05080ef4f605a4aff98eb5c94411acd8e2c891299cc9bf5`.
  It governs together with `oss/docs/validation/VALIDATION_PLAN.md` §0 and §0.1
  (`1a7d466d47198e1274661d44ba7a2bc7f20c25a685bf04895cd77f46138f572c`, not edited) and V6's protocol
  (`oss/validation/v6_metaharness/PROTOCOL.md` `e1151edd68075734fd33c1a750d802ca51ef55415779577bca0dcb63a6aeb167`, not
  edited). Its §10 lists every departure, with the reason.
- **G6 code:** no git command was run for this study, so the code is pinned by content.
  - The tree on disk includes library commit `1d3106a22e` (named by the parent session). That commit fixed V6b-F1 and
    V6b-F2, which this study's preparation found (below).
  - `g6.integrity.manifest_sha256` over every `oss/src/g6/**/*.py`, each file hashed with LF line endings:
    `6bcd92c4156f4f1ddd0221d243608962cc6c889183743bd6833de128e0fc0fdb`.
  - The meta-gate's judging code, `g6.metaharness.judging_sha256()`:
    `b9b38a90d820604df055c3cda05dd241eb02cae7ff6bc4c69e2fb409baeda51f`.
  - The runner records both at the start and the end of the run.
- **Scripts:**
  - `oss/validation/v6b_metaharness/workflows.py` `23760306b21b87f679adbfd938fac922512c1e6b10bce50d473c14d30abe040e`
  - `oss/validation/v6b_metaharness/run.py` `051367c87d5b3752f4726d106d3987f76b07e13293315dfaa4577ff9f70dd8b4`
  - `oss/validation/v6b_metaharness/analyze.py` `5c8e0a315b799a91744b1e84157f85140c509585be0fee3b8d5964d4752198ee`
  - `oss/validation/v6b_metaharness/plumb.py` `6311121f2241e59f9a229549035249e294772b7947aba9ef35ad63ff7aff5f39`
- **Splits:** `oss/docs/validation/results/V6b/splits.json`
  `01022d1d7c1d2736d30229c79c617e405e6e7ba731d5fa438e49b7ff0fa4a9ae`.
  - Seed `20261020`, ids only.
  - Excluded: every id in V6's `splits.json` (`a5c3d542eaf27bb04eedc8fba12dc924d110b5a403cf43603bfe07127dbc90fc`).
  - Dev 20 per workflow; gate 70 (GSM8K, MASSIVE, MBPP) or 163 (SciFact); eval 100.
  - Fresh pools: 1,199 / 2,854 / 689 / 380.
  - The draw was run once before development, to know the dev ids, and again at the freeze: the same hash.
- **Data** (in `$V6B_DATA`, as downloaded; SHA-256):
  - V6's files, copied, with the same hashes as V6's entry:
    - GSM8K `test.jsonl` `3730d312f6e3440559ace48831e51066acaca737f6eabec99bccb9e4b3c39d14`;
    - MASSIVE tarball `4cba5faa11c71437928e17cb1b9b3d8b8e727e7ea363a3a9a8045e19c0491577` and `1.1/data/en-US.jsonl`
      `c70f75c6a543a26e249ec383df67733ad9b1066f6c0406c2e04a3f03356e407e`;
    - SciFact `data.tar.gz` `11c621288d41ac144d29b13b0f8503b3820b7d6e8b1f6ff24dff335c196d76be`, `claims_train.jsonl`
      `f4c8fa82d8bd0653a9cc8d61a6ea48c25eacea64e90af5dbf390ebb1b74372f0` and `corpus.jsonl`
      `b8d6c89624cb2ed74dee8938effc4f5d8bd2086887880af8110d64be4ceade62`;
    - the licence files as in V6's entry.
  - New: MBPP full (Austin et al. 2021; CC BY 4.0 per the dataset card; the repository is Apache-2.0).
    - Source: `https://raw.githubusercontent.com/google-research/google-research/f46ca8374b4cddef97ca4208ad986049d74d296a/mbpp/mbpp.jsonl`
      (ETag `018efd0f45855a922e14df3f9ad6a2e2215e6621231499507fdf9b46afbabe5f`).
    - SHA-256 `ccf64ceae9c5403bf50a044cb6d505bfd2a2963ee58338ba268fd65beab92a9f`, 563,743 bytes, 974 rows, 500 with test
      task ids 11-510.
    - The same commit as V6's sanitized file, whose `README.md` (`02d63a4ffdad806c3c87b5ea2aa91e946f6eec0c2869643b09831650883f41be`)
      defines the test split.
  - MASSIVE's declared examples: 3,000 of the 11,514 en-US train rows, sampled by seed `V6b:20261020:massive-examples`.
    Rows that repeat a study utterance are dropped at run time.
- **Model:** Ollama 0.40.2, `llama3.1:8b` `46e0c10c039e019119339687c3c1757cc81b9da49709a3b3924863ba87ca666e` (8.0B,
  Q4_K_M), reached at `127.0.0.1`. Default sampling, `max_tokens` 2048, for every arm.
  - No second model. `gpt-oss:20b` took 1.96 s per short made-up call against 0.16 s for `llama3.1:8b`, because of its
    reasoning tokens. Its run would be about 24 hours or more, above the ~16-hour limit, so it is out of scope: a
    limitation.
- **Arms, endpoints and verdict:** PROTOCOL §5-§8. Every confirmation, `accept_risky` and activation is scripted, under
  the name "v6b-scripted-approval (study script, not a person)". No person approves anything in this study.
- **Development history, disclosed:**
  - **Speed probes** used made-up prompts only, no dataset item:
    - a short call took 2.2 s through "localhost" and 0.06 s through `127.0.0.1`. That is name-resolution fallback,
      about 2.1 s per request, and it also inflated V6's seconds per case (the note in PROTOCOL §9);
    - 4 concurrent streams gave about 2× throughput. The run does not use concurrency;
    - the `gpt-oss:20b` probe above.
  - **A structure probe** printed counts and field names only:
    - MBPP full: 500 test ids, 3 tests each, 1 item with setup code;
    - MASSIVE: partition sizes, and 4 to 810 train rows per intent.
  - **Library defects found by the preparation, fixed in the library before this entry (`1d3106a22e`):**
    - V6b-F1: `exemplars.Bank` matched the whole task text, so MASSIVE's 60-label list pulled the same examples to the
      top for every task. A development before/after on 300 train-partition queries (no study case): the share of the
      8 retrieved examples that carry the query's intent was 0.115 with the old match and 0.475 with the fix.
    - V6b-F2: `SearchStrategy` took the first `max_profiles` combinations in sorted-key order, so with a large space
      `exemplars` never varied. V6b sets `max_profiles` to the space size in any case.
  - **Plumbing.** `plumb.py` ran with `g6.core.llm.FakeLLM`, on 52 pool items per workflow outside every drawn id: dev 8,
    gate 32, eval 12.
    - Everything ran end to end on all four workflows: the gate recommended RecoveryStrategy's candidate on each, the
      script activated it, and cycles 2-3, F, E, the gaming scan and the analysis ran. The canary found every dev input
      in the views and no gate or eval input.
    - It led to these changes before this entry:
      - a unique tap id (two taps made in one second shared one);
      - start and end records written by one helper, which plumbing also uses;
      - the analysis treats a missing start or end record as not verified (it had passed by default);
      - crash tracebacks go to the work folder (they name local paths).
    - A synthetic check (no data) confirmed that the planted-effect control detects gains at 100 cases. It also showed
      the false-accept property below. The analysis then gained the secondary one-sided false-accept test and a power
      simulation that models false accepts.
  - **`run.py devcheck`** on the dev split only, with the fixed library. Successes of 20; "vs B" is wins and losses on
    the same cases; calls and tokens are per case:

    | Workflow | A | B | B rerun | Remedy RecoveryStrategy offered first | Other remedy |
    |---|---|---|---|---|---|
    | gsm8k | 2 | 3 | 1 | working prompt 16 (+13 −0; 1.0 calls, 240 tokens against B's 106) | 8 examples 4 (+3 −2) |
    | massive | 12 | 12 | 11 | 8 examples 15 (+5 −2; 458 tokens against 296) | — |
    | scifact | 12 | 9 | 12 | `second_look_support` 13 (+6 −2; 1.6 calls) | 8 examples 13 (+6 −2; 1,266 tokens) |
    | mbpp | 12 | 11 | 12 | `repair_with_tests` 11 (+1 −1; 2.1 calls) | `repair1` 11 (+1 −1; 1.35 calls) |

    - `mh.diagnose()` on dev named wrong answers on every workflow (number, label, label, code). On SciFact it named an
      over-used label: 6 of 9 wrong answers were SUPPORT.
    - RecoveryStrategy offered: the working prompt (gsm8k); 8 examples (massive); `second_look_support`, then 8 examples
      (scifact); `repair_with_tests`, then `repair1` (mbpp).
    - On MBPP all 9 of B's dev failures failed the shown test. After 3 repair rounds, 2 passed it but still failed a
      hidden test, and 7 still failed it (counts only).
    - The dev check changed no workflow, option, arm or size. MASSIVE's B had one dev answer held by its check.
  - **A property of the registered rule, computed before this entry** (PROTOCOL §8). The false-accept clause of rule 2
    has margin 0. A change that has no effect, once activated on a workflow, makes it fire by noise with probability
    about 0.45 there, and 0.83 if this happens on all three targets.
- **Projection:** from the dev speeds (0.06 to 0.9 s per case, sandbox included), about 2 hours in all. K = 3 and arm G
  are kept.
- **Power (`analyze.py power`):** the gate's chance to recommend (`g6.metaharness.evaluate.power` on the gate split at
  0.05 / 6), then the decision rule on 100 eval cases, 4,000 replicates, recovery origin granted:

  | Effect sizes (per-case win / loss rates against B) | Gate power MASSIVE / SciFact / MBPP | Rule 5's count met | False-accept clause fires | Rule 6's condition met |
  |---|---|---|---|---|
  | V6's pilot, on seen cases (MASSIVE 14/2, SciFact 15/6, MBPP 9/0.5 of 60) | 0.81 / 0.79 / 0.75 | 0.76 | 0.002 | 0.02 |
  | V6b's dev check (5/2, 6/2, 1/1 of 20) | 0.32 / 0.95 / 0.00 | 0.18 | 0.003 | 0.14 |
  | half the pilot's net gain | 0.27 / 0.24 / 0.20 | 0.03 | 0.016 | 0.28 |
  | no effect, activated on all three targets | forced | 0.0003 | 0.83 | 0.98 |

  GSM8K's gate power is 1.0 at both its V6 and its dev effect.
- **Pre-registered expectation:** `inconclusive`, with `mechanism_demonstrated` the main alternative. Its probability is
  about 0.2 to 0.75, depending on whether the dev or the pilot effect sizes hold.
  - The gate is expected to recommend RecoveryStrategy's working prompt on GSM8K in cycle 1, and D to beat B there.
    GSM8K is not a target.
  - SciFact's second look (cycle 1) is expected to be recommended and to transfer, at about 0.8 on the dev numbers.
  - MASSIVE's examples are uncertain: between 0.2 and 0.8.
  - MBPP's repair is not expected to be recommended (dev +1 −1), so rule 5's second recovery win depends on MASSIVE.
  - An activated change that does not transfer may end in `unsafe_or_regressive` through the false-accept clause.
- **Not audited:** no second person has checked the workflows, the answer rules, the hand-written prompts, the example
  bank or the splits.
- **Outcome (appended 2026-10-19, after the run):**
  - **The run:** the frozen scripts ran once, unchanged: 11:50-14:04, one start, no crash, 25,807 model calls. Every
    pinned hash was the same at the end.
  - **The verdict is `contaminated_evaluation`, from a canary false positive.** The proposer-view canary matched one
    MASSIVE eval utterance: a single word that is part of the 60-label list every input carries. The canary tests
    substring containment (finding V6b-F3). No leak was found: the prompt canary had 0 hits and the views hold dev
    material only.
  - **The other rules,** applied as registered with that hit set aside, give `inconclusive`, the expected outcome. There
    were no recovery wins:
    - RecoveryStrategy offered the expected remedy on every workflow.
    - The gate refused it on all three targets: MASSIVE examples 15-4, p 0.0096; SciFact second look 29-17, p 0.052;
      MBPP repair 11-5, p 0.105.
    - It approved RecoveryStrategy's working prompt on GSM8K (eval: B 20 → D 81 of 100).
    - It approved a SearchStrategy prompt on SciFact (50 → 73).
    - D = B on MASSIVE (60) and MBPP (52). False accepts never rose (pooled 214 → 130).
  - **Secondary results:**
    - H, the hand-written prompt, reached 257 of 400 against D's 266.
    - F's seeded draw on MASSIVE was RecoveryStrategy's own candidate. The gate recommended it there (15-2) and it reached
      77 on eval: the same candidate on the same 70 gate cases gave opposite decisions (finding V6b-F4).
  - **Controls:** A/A p 0.30; its zero-margin false-accept clause fired on MBPP by noise (V6b-F5, computed before the
    freeze). Planted +0.10 on each target met rule 5's count in 2,000 of 2,000 replicates. NULL 0/400. Oracle
    self-test 0 failures in 773. Gaming scan 0 matches.
  - Results and the claim ledger are in `results/V6b/README.md`.


## V7 (draft: not frozen) — G6's check-and-fix against Claude Code checking itself, on SWE-bench Pro

- **Status: draft, not frozen.** Written 2026-10-23 by the study's builder, for the owner's design D.
  - Design D replaces the earlier three-arm draft and its option 1 for arm C, whose code and tests are deleted.
  - The lead reviews it and freezes it before any scored run. At the freeze the lead enters the final hashes, G6's
    commit and the pilot's measured costs, and removes this mark.
  - No model has been called for this study, and no scored task's text, patch or tests has been read by a person.
- **Protocol:** `oss/validation/v7_swebench/PROTOCOL.md` `6b54183b670f7ec024a61d53106763e12cd35d5b8d7dd401530b8be24dc29509`
  (draft).
  - It governs together with `oss/docs/validation/VALIDATION_PLAN.md` §0 and "Reporting"
    (`054a92de4483964c9118c72e27846817e9a33e351f04b6885aecd6de454bee1d`, not edited).
  - Its §11 lists every departure, with the reason.
- **The owner's approval, choices and cap (rule 1):**
  - The request (2026-10-23): show what G6 adds to Claude Code (Sonnet) on hard repository tasks.
  - Design D ("design D pls", 2026-10-23) was chosen so that a result can be significant within $200.
  - Model calls are paid by an Anthropic API key the owner puts in `~/.g6/.env`, with a spend limit the owner sets.
    That is the written approval and the cap. The key holds $200 (lead, 2026-10-23).
  - "ensure that we store all experimental log and api output etc for transparency and later analysis" (owner,
    2026-10-23): the archive below.
  - **Lead's rulings:**
    - the task pool is the full V2 set, the lead's default (the owner did not choose);
    - the stage-1 caps (high effort, 100 turns, $4, 50 minutes) are the builder's choice, accepted by the lead;
    - the stage-2 caps ($1.50, 30 turns, 15 minutes) and A2's words are the lead's.
- **The design:**
  - **Stage 1, arm A:** Claude Code alone solves each task once.
  - **The snapshot:** the finished container is saved as an image, and both stage-2 passes resume its session with
    equal caps.
  - **A2:** Claude Code is told "Check your work: reproduce the issue, run the tests you can, and fix anything that is
    wrong.", the same words every task.
  - **G:**
    - G6's shipped checks run on the stage-1 patch, in their own throwaway container: `workforce.repo_tests` (the
      change, the visible tests it touches, run by pytest), `coding_agent_gate.scan_diff`,
      `safety_engine.detect_test_weakening`, and the parse check.
    - If G6 flags a problem, Claude Code resumes with G6's findings as its instructions. If not, G keeps the stage-1
      patch and spends nothing.
  - **Shadow lesson** (owner, 2026-10-24), after both stage-2 passes and before grading, when G6 found something: a
    model writes one lesson from G6's findings and G's outcome into a `g6.learning.TheoryStore` as a candidate (new
    failure code) or a recurrence; archived in full, never given to Claude Code, never credited or activated.
  - **Grading:** the three patches, by the official verifier, after both stage-2 passes.
  - Before each container's work the runner asserts that the grader's files and the hidden test patch's new files are
    absent.
- **Pre-registered questions:**
  - **H1 detection:** G6's flag (from its stage-2 check on the stage-1 patch) against the hidden outcome of the stage-1
    patch. One-sided Fisher exact test: the flag rate on failed stage-1 patches exceeds the rate on passed ones.
  - **H2 final resolve:** G against A2, paired by task; one-sided exact sign test on the discordant pairs (G resolves
    and A2 does not, against the reverse).
  - **Holm** across H1 and H2 at family alpha 0.05.
  - **Secondaries,** reported and not tested for the headline: G vs A, A2 vs A, breaks (a passing stage-1 patch made
    failing), cost per arm, G6 false-flag rate; the shadow lessons, counted and not tested.
  - **Wording rule for the report:** the headline names the comparison exactly ("G6's check-and-fix against Claude
    Code checking itself with the same budget"); a non-significant result is reported as such.
- **Spend:**
  - the cap is `G6_BENCH_MAX_USD`, default 180 and never above;
  - every run not finished counts at its reservation (stage 1 $4.50, each stage-2 pass $2.00, the lesson $0.10), and
    a task starts only when its $8.60 fits beside those in flight;
  - two tasks at a time, in the fixed order; the batch stops at the first task that does not fit;
  - the pilot's spend counts.
- **G6 code:** the wheel the containers install is built at run time from a named git ref (`run_arm.py build-wheel
  --ref <ref>`), resolved to its commit, into the archive. Every run records the ref, the commit and the wheel's
  SHA-256.
  - **The commit: to be entered at the freeze.**
  - The dry run used a wheel built from `70854de82509e0c847ab8fab6059012a41d548f0`
    (`3f3b801786040c41b4feea907455e59a2c1519866e5d227893c9331d86342ed8`), for the dry run only.
- **Scripts** (LF):
  - `oss/validation/v7_swebench/sample.py` `e238dab51a9d86ef3e2cc91ce80491a7218fee43a43d903fbca9723b823f0823`
  - `oss/validation/v7_swebench/v7common.py` `293dd241be39799915738959c5bb9c6ebeed88a551989b8ebd870aa4e6fae39c`
  - `oss/validation/v7_swebench/run_arm.py` `e90e9c6f2f07dbdeb7823148dbc7f7f7792f6553e1bca383e57d4a1e6082e71b`
  - `oss/validation/v7_swebench/shadow.py` `ae963a75e782b678edbc0fa1178078df0d4b7120bc707d8919034258a88aa03a`
  - `oss/validation/v7_swebench/g6check.py` `6971e5678c304dbf55cd4a0c2fcee28a1b627b8cb2b52595f792aa2f885b01f8`
  - `oss/validation/v7_swebench/evaluate.py` `d0e1b7627754d53759df2d63bf00f0e5bf7a101b650e46d3081b3a2fe0dd86cd`
  - `oss/validation/v7_swebench/analyse.py` `c4de68ecd230a1cc2b3b0743897b317377d7320b23cd22890243c4956b025dce`
  - `oss/validation/v7_swebench/egress_proxy.py` `498bcd2fed8a69ef4e62beca6d947d5cacf8223813f566c972f9af4ed0c3396f`
  - `oss/validation/v7_swebench/g6_mcp_wheels.txt` (the 80 pinned wheels G6's runtime installs, with their hashes)
    `b09075d204a6d26f4ff5564ee49db2d2cbf7bfeac2938ece8711745dc53fc97c`. If the release commit changes G6's
    dependencies, it is regenerated before the freeze.
  - `oss/validation/v7_swebench/README.md` `cee007d7f280ed7a8521e2991535c194111408c10e5b086ce2afcfe8f50cea03`
  - Tests: `oss/tests/validation/test_v7_swebench.py` `0ab80291e4954be43a07b3082265e7837d5ddecba74482be2cfd3d3fc7b800ce`.
    They are 52 tests with no network, no Docker and no model.
- **Data:**
  - SWE-bench Pro V2 (Deng et al. 2025) from Hugging Face `ScaleAI/SWE-bench_Pro` at revision
    `2d52cb3df914a3fcf80c7f66738b3a88ae37fc50`, config `default` (version 2.0.0, 642 tasks, 11 repositories):
    - `data/default/test-00000-of-00001.parquet` `1067f72c1f43bbc578276888625db3b836cebb535546c9eaf824c5d5dd104a7f`,
      6,511,689 bytes;
    - `README.md` (the dataset card) `243f51de326a4502f0e353eb3e25cb712c465e78d80e15cdaac4db70e5a9b83f`.
  - **Licence:** the harness is MIT. The card says the task content stays under the 11 upstream repositories' licences
    and names no licence for the dataset, so only task ids and results are published.
  - The official harness and task folders, `scaleapi/SWE-bench_Pro-os` at `66f92766bba642462d4bbe5479e83f91f9211862`:
    - its tarball `a459a929729c95d15cb7b4c5c599d36ea4e9a067bedc9d018cee6032b577e6cc`;
    - `v2/SHA256SUMS` `9d84f8507c89241d42d8b3ef911600a1ec75dbbb32687ce9b45b93318502c0bd`, against which every file
      under `v2/` checked.
  - Task images: `ghcr.io/scaleapi/swe-bench_pro-v2:<instance_id>`; each run records its image's digest.
- **The task order:** `oss/docs/validation/results/V7/splits.json`
  `469e6f3dfac15ce1a492779c5c474d336cab4715a9da038db905f4858d174fc2`.
  - Seed `20261023`. The rule is in `sample.py`, which reads the id, repository and language columns only.
  - The 10 tuning ids and the 20 evaluation ids are unchanged from the earlier draft. They are listed in the file.
  - The **scored order** is 60 ids: the 20 evaluation ids, then 40 more by the same round-robin, continued over all 11
    repositories, less the 10 tuning ids.
  - The **pilot** is the first 4 tuning ids. It is never scored.
- **Agent:**
  - Claude Code `2.1.309`, linux-x64 (`4d704a8aecbe038affa3000f498ae17c1b4f753add8facb8764120affc99b658`; the release
    `manifest.json` `8504633c7837099cefd2a995a433502717c360c155287b1ba92e6ec4d230093b` gives the same checksum).
  - Model `claude-sonnet-5-5`, `--effort high`.
  - The same flags in every stage; no MCP server, skill or appended system prompt.
  - Stage 1: 100 turns, 3,000 s, $4.00. Each stage-2 pass: 30 turns, 900 s, $1.50.
  - The agent's network reaches `api.anthropic.com:443` only, through `egress_proxy.py`.
  - G6's runtime: CPython 3.12.15 (`python-build-standalone` `20261021`,
    `5ff9f0602ce964a529ed1e8555d3abab6341926d724402ba9a15e9e877191124`) and the wheel, offline from
    `g6_mcp_wheels.txt`.
- **The archive:** `~/g6-validation/V7` (`V7_ARCHIVE`; the folder the lead named on the study machine), outside every worktree.
  - **Write-once:** one folder per run, `<phase>/<task id>/<arm>/<UTC time>/`, with every file it produced: the full
    stream-json, standard error, prompts and command lines, G6's check output as returned, patches, the verifier's
    logs and the egress log.
  - **`run.json`** indexes each folder with sizes and SHA-256, and says how the run ended.
  - **`runs.jsonl`** is the append-only list of every run started and ended.
  - **The key** is redacted before writing, and each finished folder is scanned; a hit is replaced by `<withheld>` and
    the run flagged.
  - **What the repository holds:** only `results/V7/archive_manifest.json`
    (`8dee90e4025b55da20e1790aef7d2eec9b78cdff22f55354ab76e154b2e56df2` now: 35 files, the dry-run records kept under
    `legacy/`) and the analysis's summaries.
  - **The raw transcripts stay in the archive;** publishing them is the owner's decision after a licence check.
- **Dry run (2026-10-23, no model, before design D), on `openlibrary-6e889f4a` and `navidrome-dfa453cc`:**
  - reference resolved 2 of 2 (reward 1.0; 17 of 17 and 1 of 1 required tests passing); empty unresolved 2 of 2;
  - the probe: `claude --version`, `g6-mcp`'s 12 tools over stdio, the flags, and the network rule, all as expected;
  - every container, network and image was removed.
  - Design D changed the pipeline around those steps (the snapshot, the resumes, G6's check, the archive). The pilot
    is its first end-to-end run.
- **Expected tasks completed under the cap** (`analyse.py project`; the pilot first, two at a time):
  - at a stage-1 mean of $2, $3 or $4: 46, 35 or 28 tasks, with stage-2 passes at $1.00 and G acting on half;
  - 31, 25 or 21 tasks, with both passes at their caps and G acting on all.
  - The lead enters the pilot's measured costs here before the freeze.
- **Power, one-sided at 0.025:**
  - H2 detects 15% G-only wins against 3% A2-only with probability 0.15 at 30 tasks and 0.28 at 40.
  - H1 detects flag rates of 60% (failed) against 20% (passed) with 0.35 at 25 tasks, 0.55 at 35 and 0.72 at 46.
- **Pre-registered expectation:** `inconclusive`, with `component_validated_no_transfer` the likeliest alternative.
- **Open before the freeze:** the ref and commit of G6's wheel; the pilot's measured costs.
- **Not audited:** no second person has checked the order rule, A2's words, G6's check, the gaming-scan patterns or the
  verifier's reproduction of Harbor's.


## V8 preparation ? draft, not frozen (2026-10-27)

Protocol: `validation/v8_composition/PROTOCOL.md`; split: `results/V8/splits.draft.json`.
The new split was drawn from the SHA-256-verified V2 parquet by reading only instance_id, repo and repo_language.
Eight Python development tasks include two reused OpenLibrary pilots; 48 Python scored IDs are newly registered
as a draft order, not a frozen scored study. No task statements, patches or hidden tests were inspected for the draw.
The model candidate is the actual V7 runner's claude-sonnet-5-5, high effort, subject to availability and pricing
calibration. Two-arm feasibility/effect estimation does not isolate composition's causal contribution.
No paid execution or improvement claim is authorized until the protocol's outstanding gates are satisfied.

## V9 (protocol registered 2026-10-28; not run) — useful patch review

The evidence page lists a study once it has run; V9 gets its tile when its first outcome is appended.

- **Protocol:** `docs/validation/patch_review_utility/PROTOCOL.md`, version 1,
  `1f2f74c6151d2de2aa693fa1a8ec12de65416ab2b095e42fde5a117cf20f692d`. It tests objective 1 only (less human
  verification effort without an unacceptable increase in accepted defects), for AI-written Python patches.
- **Plan:** `docs/validation/VALIDATION_PLAN.md` `054a92de4483964c9118c72e27846817e9a33e351f04b6885aecd6de454bee1d`
  (§0 governs, except the deviations the protocol lists).
- **Exclusions, frozen:** every ID in `docs/validation/results/V7/splits.json`
  `469e6f3dfac15ce1a492779c5c474d336cab4715a9da038db905f4858d174fc2` and in
  `docs/validation/results/V8/splits.draft.json` `7aa7f025a297798e066c8e83641d973bb3511fe504edc5181961c0e0e1271e6b`.
- **Pool:** the Python tasks of SWE-bench Pro, revision `2d52cb3df914a3fcf80c7f66738b3a88ae37fc50`, test parquet
  `1067f72c1f43bbc578276888625db3b836cebb535546c9eaf824c5d5dd104a7f`, as V8 prepared it. Not read for this entry.
- **Frozen by this entry:** the arms and their acceptance rules; the label and review contracts; the label, oracle-control
  and audit rules; the draw rule and the seed formula; the three hypotheses with their margins (+5, +5 and +15 points);
  Experiment 2's 75% target and safety constraint; the analysis; the order of verdicts. The owner may tighten a margin
  or threshold before the feasibility freeze. Nothing is loosened.
- **Left to the feasibility entry:** G6's frozen commit (the scoped assessment of Step 2 is not written yet), the agent
  and its model, the scripts, the test commands, the time limits and caps, the auditor and the reviewers.
- **State:** no task drawn, no patch generated, no arm run. The seed cannot be computed before G6's feasibility commit
  exists.
- **Review:** a fresh, read-only agent session reviewed the draft against the plan's amendments A2–A5 and §0. It
  found 2 blocking, 10 important and 5 minor problems, and this version addresses each. No person has reviewed this
  version yet.

## V10 (protocol registered 2026-10-29; not run) — verification search on agent-written patches

The evidence page lists a study once it has run; V10 gets its tile when its first outcome is appended.

- **Protocol:** `docs/validation/patch_review_search/PROTOCOL.md`, version 1,
  `2573c84e9d637716f131842717e16da6eed5aff23d0f47a1f0627531f1dd8494`. Feasibility only: it observes and tests no
  hypothesis. It runs on V9's feasibility draw, patches, labels and contracts, and changes nothing V9 registered.
- **Plan:** `docs/validation/VALIDATION_PLAN.md` `054a92de4483964c9118c72e27846817e9a33e351f04b6885aecd6de454bee1d`
  (§0 governs, except the deviations the protocol lists).
- **Depends on:** V9's protocol `1f2f74c6151d2de2aa693fa1a8ec12de65416ab2b095e42fde5a117cf20f692d`, and two conditions on
  how V9 is run: V9's feasibility freeze names a G6 commit that includes verification search (off in V9's arms), and
  V9's labels stay unread until this study's arms and reruns are recorded. If either fails, this study is not run on
  V9's patches, and this registry says so.
- **Frozen by this entry:** the arms (A, C, T and E, with V9's arms 1, 2 and 4 as comparators) and their acceptance
  rules; what counts as a finding; the rerun rules and the meanings of reproducible, actionable and compatible with the
  reference fix; the headline count and the only sentences the results permit; the primary and secondary measures; the
  feasibility criteria; the order of verdicts; the report check; and what G6's frozen commit must provide. The owner may
  tighten a limit or criterion before the freeze. Nothing is loosened.
- **Left to the freeze entry:** G6's commit, the search configuration of arms C and T, the model and the hosted
  interface, the sampling settings and per-call limits, the call cap, the number of runs per check, the seeds, the price
  list, the owner's spending cap, the scripts and the similarity measure with its threshold.
- **State:** no code for verification search written, no task drawn, no patch generated, no arm run.
- **Review:** a fresh, read-only agent session reviewed the draft against V9's protocol, the plan's §0 and the design.
  It found 1 blocking, 20 important and 20 minor problems, and this version addresses each. No person has reviewed this
  version yet.

## V9, version 2 (registered 2026-10-29; not run) — useful patch review, with broken benchmark tasks handled

This entry re-registers V9. The entry "V9" above stays as it is; for every level of the study, version 2 replaces
version 1.

- **Protocol:** `docs/validation/patch_review_utility/PROTOCOL.md`, version 2,
  `39a7859e9e44e852328ad545aa440b12849ac5b281c20d9b41eb65e6f58a0000`. Its section "Changes from version 1" lists every
  change.
- **Why:** OpenAI's audit of SWE-bench Pro's public tasks ("Separating signal from noise in coding evaluations", 8 July
  2026) estimates that about 30% of them are broken. The owner decided on 2026-10-29 that a published list of broken
  tasks is excluded, and that the label audit treats a hidden test enforcing something the task does not state as
  label-invalid: left out of the primary analysis, counted and reported.
- **Changed from the entry V9:** the section "Broken tasks" (four kinds, the published-list check at each freeze, a
  kind recorded for each label error the audit finds, and an optional task screen by a screener who is not the label
  auditor, which runs only if the owner switches it on at a freeze); label-invalid patches are left out of every rate,
  every hypothesis and verdict 3, and a sensitivity analysis keeps them.
- **Unchanged from the entry V9:** the plan, the exclusions and the pool it names (not read for this entry), the arms,
  the hypotheses and margins, the draw and the seed formula, the label and oracle-control rules, the audit's size and
  bound, the analysis, Experiment 2 and the order of verdicts. What is left to the feasibility entry is as before, plus
  the published-list check and whether the screen runs.
- **State:** no task drawn, no patch generated, no arm run, no label auditor named. No published list of broken tasks
  had been found.
- **Review:** see the entry "V10, version 2", whose review covered both protocols.

## V10, version 2 (registered 2026-10-29; not run) — verification search, with a fresh reviewing agent (arm R)

This entry re-registers V10. The entry "V10" above stays as it is; version 2 replaces version 1.

- **Protocol:** `docs/validation/patch_review_search/PROTOCOL.md`, version 2,
  `715dce8a8167d8c1efb7abb7d5b35f8831caee5317b4b8b230d9b805d77852b4`. Its section "Changes from version 1" lists every
  change and names which details are the drafter's rulings.
- **Plan:** as in the entry V10.
- **Depends on:** V9's protocol, version 2, `39a7859e9e44e852328ad545aa440b12849ac5b281c20d9b41eb65e6f58a0000`, and the
  same two conditions on how V9 is run as before.
- **Frozen by this entry:** everything the entry V10 froze, with these changes:
  - arm R (the owner's decision of 2026-10-29): a new Claude Code session that did not write the patch, with V9's
    agent's model, version and effort and V9's arm 2 limits, given the review contract, the diff and a scratch copy
    of the repository without history, under V9's arm 2 prompt with two registered changes, accepting by V9's arm 2
    rule, and writing tests that count as findings by arm E's rule;
  - the spending cap shared by four arms, a quarter each unless the freeze entry sets other shares;
  - arm A's findings including regressions of the repository's own tests;
  - the second verdict decided by feasibility criterion 6 alone, with V9's feasibility audit reported beside the
    results;
  - V9 version 2's handling of broken tasks.
- **Left to the freeze entry,** in addition to what the entry V10 left: R's prompt, product version, session set-up,
  and the script that sets up R's copy and collects its tests; the prices for R's model; and each arm's share of the
  cap.
- **State:** verification search is being built on development fixtures only and is not frozen. No task drawn, no
  patch generated, no arm run.
- **Review:** a fresh, read-only agent session reviewed the drafts of both protocols against the owner's decisions,
  version 1 of each and §0. It found no blocking problem, 13 important and 4 minor ones, and a defect in V10 version
  1's second verdict. This version addresses each, with one exception: the reviewer judged the optional task screen to
  go beyond the owner's decision, and it is kept as an option that runs only if the owner switches it on. No person
  has reviewed this version yet.

## Worked examples on well-known Python projects (plan fixed 2026-10-29, before any run)

Not a study: worked examples, mechanism demonstrations on real code; they show what happened in these runs and say
nothing about how often G6 helps. They are separate from V9 and V10 and touch none of their material, code or budget.

- **Plan:** `validation/worked_examples/PLAN.md`, `c2d9f6be6c0c90de932014b0c0f92197614c25a01180723f98feb7fa122b0c3f`.
  It was fixed before any run, before any paid call and before any candidate task was looked at. It states the
  selection rule for projects and tasks, the run procedure in the product's real flow (`g6 setup`, then headless
  Claude Code with `claude-sonnet-5-5` and the `using-g6` skill), the independent check of every outcome against the
  maintainers' later fix and tests, that every run the rule chose is reported, and a hard stop of $100 for all runs
  together, separate from the rehearsal's $15 and V9's $60.
- **Owner's decision:** 2026-10-29, to use the API credits before they expire on 4 November.
- **State:** no project or task selected, no image built, no run, nothing spent.
- **Changes:** none. A change is a new dated section at the end of the plan, written before the runs it affects.
- **Round 3 (appended 2026-10-30, before its runs):** a development round, not a validation: the same ten bugs, which have been seen, with three arms (A: Claude Code alone; B: G6 as shipped; C: G6 and a prompt addendum to check each requirement with G6's formal tools), on `claude-haiku-4-5`, then `claude-sonnet-5-5`, then `claude-fable-5-1`, Sonnet's arm A twice per bug, under round 3's own hard stop of $100 and its ledger. The design is the plan's dated section "Round 3 (2026-10-30): formal methods".
