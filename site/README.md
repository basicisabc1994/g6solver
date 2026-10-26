# g6solver.com — static site

Built with MkDocs Material, to be hosted free on Cloudflare Pages from the cut-over. No server, no forms, no
analytics.

Local preview:

    pip install "mkdocs-material==9.5.39"
    mkdocs serve -f oss/site/mkdocs.yml

Cloudflare Pages settings (the owner sets these once, at cut-over):

| Setting | Value |
|---|---|
| Build command | `pip install "mkdocs-material==9.5.39" && mkdocs build --strict -f oss/site/mkdocs.yml` |
| Build output directory | `oss/site/build` |
| Environment variable | `PYTHON_VERSION=3.12` |

Downloads (bundles) are attached to GitHub Releases, not hosted here (Pages caps files at 25 MB).
