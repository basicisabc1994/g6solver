# M1b static inventory: result (exploratory)

Run 2026-11-20/21 with `python -m validation.distill.pilot.m1b.inventory` on the mined pool (150 kept repositories,
648 merged pull requests in 64 of them). The owner's A2 check against the sealed final cohort removed 1 repository
(17 entries). Reads only: no candidate code was executed and no body is published here.

## Reads
- 631 entries read in 63 repositories; none left unread.
- 22 entries in 4 repositories were refused by the git reader because their trees hold a submodule
  (bastibe/python-soundfile, john-kurkowski/tldextract, kozea/tinycss2, nhairs/python-json-logger).

## Rule codes (entries failing each rule; an entry can fail several)

| Rule | Entries |
|---|---|
| V3 | 425 |
| M4 | 386 |
| M1 | 264 |
| V1 | 201 |
| V2 | 178 |
| M2 | 166 |
| M3 | 153 |
| M6 | 119 |
| READ | 22 |
| M5 | 2 |

31 entries in 13 repositories pass every rule as built.

## Counts (repositories) and the frozen chooser

| Configuration | Repositories |
|---|---|
| K_0 (as built) | 13 |
| K_1 (helper staging) | 13 |
| K_2 (one case per changed test module) | 13 |
| K_3 (prepared interpreter) | 26 |
| K_M (V rules off) | 29 |
| K_123 (never selected) | 29 |

Chooser: K_0, 13 repositories, tier **reduced**, D4 **draw** (roles 4 acquisition, 4 admission, 2 monitor; reuse not
measured). K_3 would reach the full tier, but the registration requires a prepared interpreter that exists and is
recounted; none exists, so the frozen chooser set K_3 aside. Whether to build one is the owner's decision; the draw
waits for it.

K_0 repositories: borntyping/python-colorlog, cunla/fakeredis-py, keleshev/schema, marshmallow-code/marshmallow,
martinblech/xmltodict, pytest-dev/pytest-env, pytest-dev/pytest-mock, pytest-dev/pytest-xdist, r1chardj0n3s/parse,
scientific-python/lazy-loader, sethmmorton/natsort, wolph/portalocker, wolph/python-utils.
