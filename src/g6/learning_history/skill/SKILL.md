---
name: g6-learning-history
description: Use when you need to inspect G6's locally retained repository checks and results, find their store, or ask to erase one repository's learning history.
---

# G6 repository check history

## When to use it

Use `learning_history` to see what G6 has retained for an explicit repository: check identifiers, admitted trials,
rejected rows, stale checks and the local store path. Recording is on by default. Use its client dialog when the
person wants that repository's history erased.

## When not to use it

This does not run checks, record caller-supplied outcomes, train a model, adopt strategies or decide whether code is
correct. Use `learning` for prompt hints and `metaharness` for workflow tuning. History removal does not remove the
separate execution receipts or disable future recording.

## Ops

- `list`: inspect one repository's local history without returning retained check source.
- `forget`: request irreversible erasure of that repository's history through the MCP client's consent dialog.

`ops` and `help` provide operation discovery. `list` and `forget` require a non-empty `repo` containing the absolute
path of a git working tree's top folder. Unknown fields and caller-supplied `op` overrides are refused.

## Alone and composed

Alone over MCP, call `g6_invoke` with `component="learning_history"`, `op="list"`, and `params_json` containing
`{"repo": "<absolute repository root>"}`. Read `ok`, `completion_state` and `warning_card` before using the result.
The result includes `retained_checks`, `admitted_trials`, `rejected_trials`, `stale_checks`, `checks`, `trials`,
`repository_id` and `store_path`. `history_unavailable` means the store could not be read; empty counts then do not
establish an empty history. A repository without a committed identity has no identified store.

Composed, `harness` supplies execution-owned outcomes and `workforce` supplies repository validation and receipts.
Inspect history before reviewing a repeated check result; this component grants no execution or adoption authority.

For `forget`, the server's owner must allow `learning_history.forget` in `G6_MCP_SENSITIVE`, and the person must then
agree in the client dialog. A decline, a missing dialog or changed history authorizes no deletion. A supplied name
is recorded, not authenticated. Python, CLI and composition calls cannot substitute for that dialog; a direct
Python call refuses with `G6_E_LEARNING_HISTORY_DIALOG_REQUIRED`. Never retry around a permission refusal. After an
I/O failure during erasure, use `list` to inspect what remains; do not assume that nothing was removed.

## Failure modes it catches

- `opaque_learning`: exposes counts, check identifiers, trial metadata and the store path, and offers
  repository-scoped erasure only after the person's agreement in the MCP client.

## Blind spots

This is same-user local state. Code running as that user can change it; the component is no security boundary
against such code. Counts reflect admitted local records, not all work ever attempted or independent assessment
of correctness. Missing receipts or an unreadable chain admit nothing. The list omits retained source but can show
check paths and finding metadata; share it deliberately. Erasure cannot recall any copies already shared.

## What it needs

The normal G6 installation, git on PATH, an existing local git working tree and access to G6's local history under
`G6_HOME` (the default is the user's G6 state directory). No optional extra, network connection or model is needed.
Erasure additionally needs an MCP client supporting elicitation and the owner's sensitive-op setting. This
setting alone is insufficient: the person must accept the dialog for that request.

## Examples

For erasure over MCP, replace the path below with the repository's absolute root. This sensitive example is a
request for the client's dialog, never permission for an agent to accept it:

```g6-example op=forget
{"repo": "/absolute/local/repository"}
```

This offline example creates a temporary local git repository with one empty commit, and lists its history through
the checked reply function used by `g6_invoke` with a fresh temporary `G6_HOME`. It needs no installed source checkout
or existing git identity configuration. The temporary repository is removed and the existing home restored afterwards.
It records no outcomes and requests no deletion. To inspect your own history, supply its absolute repository root and
keep your normal `G6_HOME` instead.

```python
import json
import os
import subprocess
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from g6.core import git_env
from g6.mcp import invoke_reply as g6_invoke


def inspect_history():
    with TemporaryDirectory(prefix="g6-history-") as scratch:
        repository = Path(scratch) / "repo"
        repository.mkdir()
        with git_env.safe_git(repository) as environment:
            for args in (("init", "--quiet"),
                         ("-c", "user.name=Skill Fixture", "-c", "user.email=skill@example.invalid",
                          "-c", "commit.gpgsign=false", "commit", "--allow-empty", "--no-verify",
                          "--quiet", "-m", "offline history fixture")):
                subprocess.run([git_env.git_program(), "-C", str(repository), *args],
                               env=environment, check=True, capture_output=True, timeout=30)
        home = Path(scratch) / "home"
        with patch.dict(os.environ, {"G6_HOME": str(home)}):
            value = g6_invoke("learning_history", "list", json.dumps({"repo": str(repository)}))
            assert value["ok"], value
            history = value["result"]
            assert history["retained_checks"] == history["admitted_trials"] == 0
            assert history["rejected_trials"] == 0 and "history_unavailable" not in history
            assert history["checks"] == history["trials"] == []
            assert Path(history["store_path"]).is_relative_to(home)


inspect_history()
```

## The four open problems

- **Learning signals**: exposes execution-owned retained checks and trial counts. It accepts no caller-written
  training signal and measures no improvement itself.
- **Evaluators the model cannot influence**: reads receipt-backed outcomes; it does not select or replace the
  evaluator. Same-user state remains a trust limitation.
- **Transfer by failure mode**: addresses `opaque_learning` through inspection and erasure of one repository's
  history. It transfers no checks or strategies between repositories.
- **Enough held-out cases**: counts are operational history, not a held-out evaluation. This component supplies no
  sample-size or generalisation claim.
