<!-- SPDX-License-Identifier: Apache-2.0 -->
# V7 (design D): how to run each step

`PROTOCOL.md` is the design; this file is the order of commands. Run everything from `oss/`, in the foreground, with
`python -I`. Two Pythons are used:

- `$g6py`: a Python 3.12 with G6's dependencies (the repository's development virtual environment). The scripts put
  `oss/src` on their own path, so `-I` still imports the G6 tree of this checkout.
- `$studypy`: the study's own virtual environment, outside `oss/` and never committed. It holds the dataset reader
  (`pyarrow`) and, for reference only, Harbor 0.22, the official runner.

Every command that runs anything writes into the **archive**: `V7_ARCHIVE`, default `~/g6-validation/V7`.
One write-once folder per run (`<phase>/<task id>/<arm>/<UTC time>/`) holds everything the run produced, indexed by its
`run.json`; `runs.jsonl` at the root lists every run started and ended. Pass `--archive <folder>` to use another.

## 1. Downloads (once; each into its own new folder)

| What | Where it comes from | Check |
|---|---|---|
| The task list | `huggingface.co/datasets/ScaleAI/SWE-bench_Pro/resolve/<revision>/data/default/test-00000-of-00001.parquet` | `v7common.DATASET` |
| The harness and task folders | `codeload.github.com/scaleapi/SWE-bench_Pro-os/tar.gz/<commit>` | `v7common.HARNESS`; then `sha256sum -c SHA256SUMS` in `v2/` |
| Claude Code | `downloads.claude.ai/claude-code-releases/2.1.309/linux-x64/claude` and its `manifest.json` | `v7common.CLAUDE_CODE` |
| Standalone Python | `python-build-standalone` release `20261021`, `cpython-3.12.15+20261021-x86_64-unknown-linux-gnu-install_only.tar.gz` | `v7common.PYTHON` |
| The wheelhouse | the 80 wheels in `g6_mcp_wheels.txt`: `pip download --no-deps --require-hashes --only-binary=:all: --platform manylinux_2_17_x86_64 --platform manylinux2014_x86_64 --platform manylinux_2_28_x86_64 --platform manylinux_2_5_x86_64 --platform manylinux1_x86_64 --platform linux_x86_64 --python-version 3.12 --implementation cp -r g6_mcp_wheels.txt -d <wheelhouse>` | the hashes in the file |

Then write `assets.json` outside the repository:

    {"harness": "<...>/SWE-bench_Pro-os-<commit>/v2", "claude": "<...>/claude-2.1.309-linux-x64",
     "python": "<...>/cpython-3.12.15+20261021-x86_64-unknown-linux-gnu-install_only.tar.gz",
     "wheelhouse": "<...>/wheelhouse"}

## 2. The task order (ids only; already done)

    $studypy -I validation/v7_swebench/sample.py --data <folder with the parquet> --out docs/validation/results/V7

## 3. G6's wheel, from a named ref

    $g6py -I validation/v7_swebench/run_arm.py build-wheel --ref <the release branch>

The ref is resolved to its commit, and the wheel is built from `git archive` of that commit, into the archive
(`build/<commit>/wheel/`). Every run records the ref, the commit and the SHA-256. If that commit changes G6's
dependencies, regenerate `g6_mcp_wheels.txt` and the wheelhouse first.

## 4. The dry run and the probe (no model)

Docker Desktop must be running (`docker version` answers).

    $g6py -I validation/v7_swebench/run_arm.py probe  --assets assets.json --task <first tuning id>
    $g6py -I validation/v7_swebench/run_arm.py replay --assets assets.json --task <tuning id> --patch gold
    $g6py -I validation/v7_swebench/run_arm.py replay --assets assets.json --task <tuning id> --patch empty

Each replay is graded at once: the reference patch must come out resolved and the empty one unresolved.

## 5. The pilot (model calls start here)

The owner puts `ANTHROPIC_API_KEY=<key>` in `~/.g6/.env`. The runner reads it only from there, and only for a task the
spend rules let start. `G6_BENCH_MAX_USD` may lower the $180 cap, never raise it.

    $g6py -I validation/v7_swebench/run_arm.py pilot --assets assets.json --jobs 2

The pilot runs design D on the first 4 tuning ids (phase `pilot`, never scored). Its spend counts against the cap.

## 6. The freeze

The lead records the hashes, G6's commit and the pilot's measured costs in `docs/validation/REGISTRY.md`, and removes
the entry's "draft: not frozen" mark.

## 7. Scored runs

    $g6py -I validation/v7_swebench/run_arm.py batch --assets assets.json --jobs 2

- Tasks run in the fixed scored order, two at a time. Each task runs stage 1, then A2 and G from its snapshot, then
  its shadow lesson when G6 found something (`shadow.py`: a lesson archived and never given to Claude Code); its
  three patches are graded only after both stage-2 passes.
- A task starts only when its runs fit under the cap ($8.60 with the lesson's $0.10, beside those in flight). The
  output says how many tasks completed.
- The batch can be stopped and started again. A task whose stage 1 started is never run again, and a crashed run is
  ended as `interrupted` at its stream's cost.
- If a task's grading failed after its stage 2 finished, grade it again:

      $g6py -I validation/v7_swebench/run_arm.py grade --assets assets.json --task <id>

## 8. Controls, the report and the manifest

    $studypy -I validation/v7_swebench/evaluate.py selftest --harness <v2 folder>
    $g6py -I validation/v7_swebench/analyse.py gaming
    # a person inspects gaming.json in the archive root and writes gaming_review.json there
    $g6py -I validation/v7_swebench/analyse.py report
    $g6py -I validation/v7_swebench/analyse.py manifest

`report` writes `docs/validation/results/V7/report.json` and `rows.csv`; `manifest` writes `archive_manifest.json`
(every archived file's path, size and SHA-256). The raw transcripts stay in the archive: they hold the upstream
repositories' code and issue text, so publishing them is the owner's decision after a licence check.

## Tests (no network, no Docker, no model)

    $g6py -m pytest tests/validation/test_v7_swebench.py -q -p no:cacheprovider --import-mode=importlib
