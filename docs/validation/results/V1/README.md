# V1 — grounding: results (2026-10-18)

Registered in `../../REGISTRY.md` (entry "V1", commit `92421e30e1`) before the evaluation split was read. The run
used the frozen scripts unchanged, on VitaminC `test` (55,197 rows), seed `20261018`.

**Verdict: `component_validated_no_transfer`.** On this population, `g6.grounding.check_claim` met the
pre-registered bar:

- false support ≤ 5%, with the 95% upper bound ≤ 10%;
- lower false support than the relevance baseline (paired sign test);
- quoted recall ≥ 90%.

"No transfer": the result covers Wikipedia sentences in VitaminC's style and these five strata, nothing wider.

## Population

| Stratum | Qualifying cases | Drawn | Label |
|---|---|---|---|
| quoted (≥ 90% of the claim's words in the evidence) | 2,649 | 100 | supported |
| paraphrased | 14,993 | 100 | supported |
| negated | 74 | 74 (all) | unsupported |
| wrong figure | 3,712 | 100 | unsupported |
| near topic (NOT ENOUGH INFO) | 6,228 | 100 | unsupported |

That is 474 pairs. The primary endpoint is computed on the 174 negated and wrong-figure pairs.

## Results

Rates are over the stratum's pairs. "False support" is the share of the 174 adversarial pairs the arm called
supported. Intervals are 95%: Wilson, and a cluster bootstrap over `case_id`. No arm had an error.

| Arm | False support | Wilson | Bootstrap | Quoted recall | Paraphrased recall | Near-topic called supported |
|---|---|---|---|---|---|---|
| `check_claim` | **0/174 = 0.0%** | 0.0–2.2% | 0.0–0.0% | **91/100** | 19/100 | 6/100 |
| relevance baseline (BM25 top-1 ≥ 1.4189) | 56/174 = 32.2% | 25.7–39.5% | 25.3–39.7% | 73/100 | 40/100 | 33/100 |
| NLI, `gpt-oss:20b` (one call per pair) | 3/174 = 1.7% | 0.6–5.0% | 0.0–4.0% | 95/100 | 82/100 | 13/100 |
| NLI, `llama3.1:8b` | 26/174 = 14.9% | 10.4–21.0% | 9.8–20.7% | 89/100 | 83/100 | 51/100 |

**Paired, `check_claim` against relevance, on the adversarial pairs:** 56 wins and 0 losses (one-sided sign test
p < 10⁻⁶). The two deterministic arms gave identical answers in both runs.

## What this does and does not show

- **It shows** that on this population `check_claim` did not call a negated or wrong-figure claim supported, while
  "the passage is relevant" did so a third of the time. That is the defect audit 014 GRD-001 named.
- **It does not show** that `check_claim` is a good support detector in general. It found support for only 19% of
  supported paraphrases, against 82% for one call to `gpt-oss:20b`. That model's false support (1.7%, upper bound
  5.0%) is not distinguishable from `check_claim`'s on 174 pairs: the model wrongly supported 3 pairs that
  `check_claim` did not, and there were none the other way, so the exact sign test gives one-sided p = 0.125. The honest reading: `check_claim` is a
  cheap, model-free check that rarely says "supported" wrongly and often says "not found" when the source does
  support the claim. A sentence it marks "not found" needs a person or a model to look again.
- The quoted stratum is defined by word overlap, the signal `check_claim` itself uses, so the 91% quoted recall is
  partly circular (plan §0.1, weakness 1).

## Everything that went wrong or is open

- **Strata unaudited.** No second person checked the stratum assignment (plan §0.1, weakness 4).
- **Negated stratum is small.** It has 74 pairs, and 57 of them come from VitaminC's synthetic (FEVER-based)
  claims.
- **Near-topic disagreements.** On inspection after unblinding, several of `check_claim`'s 6 near-topic "supported"
  answers look arguable as dataset labels; for example, "16 confirmed cases before March 15" against "as of March 15,
  … 16 cases". The dataset label is the pre-registered oracle, so these count against the check and are not
  rescored.
- **Quoted misses.** Several of the 9 quoted misses are near-quotes broken by an inserted word ("can also be used").
- **Contamination.** VitaminC has been public since 2021 and carries a BIG-bench canary, so the NLI arms may have
  seen it in training (`contaminated_evaluation` risk). That would flatter the NLI arms, not `check_claim`.
- **Relevance baseline.** It was tuned on `dev` to maximise balanced accuracy (0.696 there), so it was given its
  best setting.
- **Development history.** It is in the registry entry: the negated rule narrowed twice on `dev`, the NLI token
  budget raised, and the runner's error accounting and bootstrap added before the freeze.
- **Gaming check (rule 8).** The arms receive only the claim and the source text, never the label. No arm can
  reach the oracle.

## Claim ledger

| Public sentence | Rows behind it |
|---|---|
| "On VitaminC, `check_claim` called 0 of 174 negated or wrong-figure claims supported (95% upper bound 2.2%); a relevance baseline called 32% supported." | `v1_rows.csv`, strata `negated` and `wrong_figure`, columns `check_claim` and `relevance` |
| "It found support for 91% of near-verbatim claims and 19% of paraphrased ones." | `v1_rows.csv`, strata `quoted` and `paraphrased` |
| "A single call to a local 20B model matched its false-support rate within the interval and found far more paraphrased support." | `v1_rows.csv`, column `nli_gpt-oss_20b` |

## Files

- `v1_report_gpt-oss_20b.json`, `v1_report_llama3.1_8b.json`: the runner's reports, per pair as answered.
- `v1_rows.csv`: one row per pair with each arm's answer. Ids only, no dataset text.
- `v1_test_pairs.CC-BY-SA-3.0.csv`: the 474 drawn pairs with their claim and evidence text. They come from VitaminC
  (Schuster, Fisch and Barzilay, NAACL 2021; Wikipedia), under the terms on the applicable Wikipedia pages, else
  CC BY-SA 3.0. This one file is not Apache-2.0.
