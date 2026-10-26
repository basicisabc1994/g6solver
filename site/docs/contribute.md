# Contribute

G6 welcomes contributions: bug reports, new components, bundles, evaluations, and reproductions of our results.

- **Report a problem:** open an issue on GitHub with the steps to reproduce it.
- **Reproduce a result:** the [validation plan](https://github.com/basicisabc1994/g6solver/blob/main/docs/validation/VALIDATION_PLAN.md)
  and each study's frozen scripts and per-case rows are published. Independent re-runs, confirming or not, are the
  most valuable contribution of all.
- **Add a component:** every component works on its own, declares its dependencies (`__g6_requires__`), carries a
  capability label, and ships with tests, including at least one test that would fail if its main claim were false.
  Run `python tools/gen_catalog.py` so the [catalogue](components.md) matches the code.
- **Build a bundle:** a bundle is a small configuration over existing components plus a test that runs it end to end
  offline. A bundle is listed only once that test exists.

## Security {#security}

Please report security issues privately, not in a public issue: the [Security](https://g6solver.com/security/) page
says how.
