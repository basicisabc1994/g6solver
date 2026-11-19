# Phase 0 runbook

S0 implements record judgments and a fake family-bank dry run. It invokes no
hardware, serving program, provider or training stack. The declarations follow
v2 section 9 and v1 section 9; commands are templates for an owner's local
driver. The driver placeholder is deliberate: importing this package or using
this CLI cannot start a hardware step. `g6.recursion.spike.templates` contains
the reusable logic distilled from `run_p0.sh`, `p0_2_load.py`, `p0_3_train.py`
and both loss-bisection scripts. Those private working copies are not runtime
dependencies. Hardware driver integration is owner-run work.

From the repository root, with `src` on `PYTHONPATH`:

```text
python tools/rsi/spike/runbook.py describe P0-3
python tools/rsi/spike/runbook.py check P0-3 P0/P0-3.json
python tools/rsi/spike/runbook.py family-bank --output .build/family-bank.json
```

`describe` gives command argv, bars, procedure, fallback and a JSON template
path. Copy the relevant `records/P0-N.json`, fill every observation and set
`status` to `ok`, `error` or `oom`. Templates deliberately return `unknown`.
CLI exit codes are 0 for pass, 1 for fail and 2 for unknown. Fake results carry
`mode: fake`; they do not establish machine feasibility. Real records use
`mode: measured`. All rates are fractions; point differences are percentage
points. Memory fields use the hand scripts' GiB convention (bytes / 2**30).

Every record contains:

- `schema: 1`, `step`, `status`, `mode`, `manifest` and `metrics`.
- `manifest.model_directory`: the directory from which hashes were captured.
- `manifest.sha256`: the complete weights manifest's pinned digest;
  `sha256_after`: its independently captured digest after the step.
- `manifest.expected`: every file/hash pair in that pinned manifest.
- `manifest.before` and `after`: independently captured file/hash maps. Every
  map must equal `expected`; missing files, added entries and changed hashes
  cannot pass. The checker reads records only, never a model directory.

The owner must pin a complete manifest before each step. `manifest_guard`
hashes files relative to the model directory, checks the pinned manifest and
captures after hashes on success and exceptions. Its `read_manifest` callback
must read the local manifest file afresh each time; the guard hashes that text
again after the step into `sha256_after`.
Endpoint disk hashes do not establish in-memory tensor identity or inventory
completeness. A missing post-step capture is unknown, never pass. Wrapper
pipelines must use `set -eo pipefail`, preserve the step's nonzero exit and
capture post-step hashes from an EXIT handler even after errors. The checker
rejects malformed/nonfinite JSON and duplicate keys.

The main numerical bars are:

| Step | Bars |
|---|---|
| P0-1 | 5070 Ti, CUDA available, capability 12.0, 4096-square bf16 CPU match; memory 22GB/swap 16GB, CUDA >=12.8, torch 2.9.x cu128, no WSL driver |
| P0-2 | Loaded, idle VRAM <=13.5GB, valid Harmony |
| P0-3 | 2048 or 1024, no OOM, peak <=15.3GB, consumed tokens/s >=400, falling loss, first-three loss within 0.5 of base |
| P0-4 | One complete A/B route: >=18/20 trigger, <=1/20 other, MBPP+-100 within 3 points, tool parse >=98%, decode >=100 tokens/s, export <=22GB WSL cap and <=60 minutes; exact tested/served adapter digest |
| P0-5 | >=2000 checked attempts/16 hours; 200 problems, low/medium reasoning, slots 1/2/4, both servers; 50 representation tasks, >=98% parse and all formal/datalog/cegis calls within deadlines |
| P0-6 | <=36 machine hours, retention drop <=3 points, Harmony parse >=99.5%; 400 train tasks, k=4, 200 test tasks; gain recorded without positive-gain bar |
| P0-7 | Two cycles, GPU freed <=60 seconds, no OOM, 1441 minute readings over 24 hours, measured/projected free RAM >=6GB at reboot cadence |
| P0-8 | Opus 5.5; five proposals, all parsed, each 20–40k-token view, latency and limit fields recorded |
| P0-9 | 40 unique families; suite strength passes; fixed issue template with no actor text; each simulated episode <=1200 seconds |

For P0-3, the required record also includes all of the following:

- rank 16, batch 1, accumulation 16, 50 steps, 500 samples and 100 tool turns;
- `router_aux_loss: off`, trainer `router_aux_loss_coef: 0.0`, and all model and
  submodule configs reset after trainer construction. TRL construction enables
  `output_router_logits`, adding the gpt-oss 0.9 router load-balancing term;
- assistant-only masking that aborts on errors; a token-aligned synthetic
  system/user/assistant-analysis/tool-call/tool-result/assistant-final sample,
  with each assistant token labelled and all other tokens set to -100;
- identical collated unpacked batches, labels, reduction and router settings
  compared before updates. The first-three training loss bar is an additional
  coarse guard; it does not establish objective equivalence;
- at least two actual batches at the claimed maximum length. Short samples
  cannot establish context capacity;
- timed consumed nonpadding input tokens, separately counted supervised
  tokens, and elapsed seconds. `TokenCounter` accumulates real microbatches;
  requested packing, SEQ, a first-64 mean and accumulation-group estimates
  never supply the numerator. Packing may be silently skipped on this stack;
- explicit final adapter/tokenizer saving, adapter digest, resolved compute
  dtype, pinned torch/transformers/TRL/Unsloth/Zoo/PEFT/bitsandbytes versions,
  LF-normalised dataset hash and locally inspected installed-trainer digest.

If 2048 fails, register 1024 with per-turn samples and short-context families,
and omit the SWE probe. If 1024 fails, stop for the owner's Q3 decision. No
automatic provider, model or compute switch exists here.

`GPULock(holder_id)` is a context manager over
`$G6_HOME/recursion/gpu.lock`, resolved through G6's state path. Byte zero is
locked by the OS; the remaining UTF-8 JSON records id, PID and UTC start time.
A second acquirer gets `G6_E_GPU_BUSY`. Metadata never expires; an unlocked
file's stale metadata is replaced only after acquiring its OS lock. Release
and process exit release ownership. The file is never unlinked, avoiding
split ownership among concurrent openers. Local filesystem lock semantics are
required; unrelated programs must also honor the reservation.

P0-9 calls real `mutation_check.check` on forty distinct synthetic boundary
functions, using fake execution results and a simulated clock. No generated
source runs. As v2 specifies no score cutoff, this slice conservatively
requires at least one mutant tried, every tried mutant rejected, no survivors
and no early stop for each family. This measures dry-run plumbing only. S4
owns real family generation and suites. S0 accepts only the fixed template
`Repair the boundary in {function}.` and an empty actor-text field.
