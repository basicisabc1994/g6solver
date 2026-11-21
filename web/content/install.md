# Get started — no programming needed

!!! info "Where the downloads are"
    Each worked example's downloads are on [Worked examples](bundles/index.md) and on the
    [releases page](https://github.com/basicisabc1994/g6solver/releases), where they are called bundles. A developer
    can also run one from a copy of the code: `pip install .`, then for example `g6 bundle check-claims`.

## Install for a coding agent or Python

Use Python 3.12 or 3.13. Give G6 its own environment with `--copies`: on Linux, G6 cannot yet distinguish its own
Python from a repository environment linked to the same interpreter, and refuses to review then. Activate it and
install G6 and its MCP dependencies:

```sh
python -m venv --copies .venv
source .venv/bin/activate  # PowerShell: .venv\Scripts\Activate.ps1
python -m pip install "g6[mcp] @ git+https://github.com/basicisabc1994/g6solver"
g6 doctor
g6 setup --client claude --allow patch_review.review  # or --client codex; run this yourself
```

Doctor names what is missing, the capability it blocks, and the command to fix it. It never installs anything or
contacts a model. Importable components alone do not establish that patch review or an MCP client connection works.

Linux (bubblewrap) and Windows (AppContainer) review patches; macOS does not review patches (no supported OS boundary); everything else works.

| Platform | Patch review boundary | Preparation |
|---|---|---|
| Linux | bubblewrap | `sudo apt-get install bubblewrap`, then `g6 doctor`. Doctor gives the exact remedy if user namespaces or AppArmor prevent startup. |
| Windows | AppContainer | `g6 doctor` prints the exact `icacls` commands for the interpreter folders it cannot read. Run those yourself, then repeat doctor. An access-list check alone is not a successful confined run. |
| macOS | No supported OS boundary | Use Linux or Windows for patch review. Model-free tools, the MCP server and model-backed operations remain available with their dependencies. |

On Windows, doctor's access-grant template is `icacls "<python-folder>" /grant "*S-1-15-2-1:(OI)(CI)(RX)", then icacls "<python-folder>\*" /grant "*S-1-15-2-1:(RX)" /T /C /Q`.
Replace `<python-folder>` with the exact interpreter or environment folder doctor names; run the two commands
separately. This grants read/execute access to all AppContainers on that folder. G6 does not change access lists for you.

| Missing piece | What it blocks | Command (Linux, Windows and macOS) |
|---|---|---|
| `fastmcp` / `mcp` | MCP server | `python -m pip install "g6[mcp] @ git+https://github.com/basicisabc1994/g6solver"` |
| `anthropic` | Anthropic API calls; a model used through Claude Code needs no SDK | `python -m pip install "g6[anthropic] @ git+https://github.com/basicisabc1994/g6solver"` |
| `z3-solver` (module `z3`) | Z3 formal methods | `python -m pip install "g6[formal] @ git+https://github.com/basicisabc1994/g6solver"` |
| `ortools` | OR-Tools constraint solving and optimisation | `python -m pip install "g6[optim] @ git+https://github.com/basicisabc1994/g6solver"` |
| `hypothesis` / `numpy` | Abstraction property checks | `python -m pip install "g6[abstraction] @ git+https://github.com/basicisabc1994/g6solver"` |
| A configured model | Model-backed operations | Set up Claude Code or Codex with the command doctor prints, then choose that provider in `g6 setup`. Doctor does not check that the program is set up. |

If the host program is absent, doctor names its install command. A set-up program is needed only for model-backed operations;
model-free checks work without one. Register the MCP server in your coding client with `g6 setup`, choose what it may
run, then reconnect the client and accept its source-sharing dialog for data you intend to share. Doctor does not
exercise that handshake or consent. Patch review refuses when no supported boundary can start, even if general
unconfined execution was allowed.

From an uninstalled source checkout, install into your environment with `python -m pip install ".[all]"` for the
`g6` and `g6-mcp` commands. G6's own workers also receive this checkout's resolved package root explicitly; their
working directory never supplies it.

## 1. Choose a worked example

Go to [Worked examples](bundles/index.md) and download the one for your problem. Pick the file for your computer:
Windows (`…-windows-….exe`), macOS (`…-macos-arm64.zip`, for Apple-silicon Macs; there is no Intel Mac build yet)
or Linux (`…-linux-x86_64.zip`). On macOS and Linux, double-click the `.zip` to unpack it.

## 2. Open it

Double-click the file. A small window opens (keep it open while you work) and your web browser opens a
private G6 page. It runs only on your computer. Close the small window to stop it.

!!! question "The browser did not open"
    The small window shows an address like `http://127.0.0.1:52817/`. Copy it into your browser.

!!! question "Windows says “Windows protected your PC”"
    G6's worked examples are not code-signed (signing costs money every year, and G6 is free). Click **More info**,
    then **Run anyway**. You can check the download first: each release includes `SHA256SUMS.txt`.

!!! question "macOS says the app “cannot be opened”"
    Right-click (or Control-click) the app, choose **Open**, then **Open** again. You only need to do this
    the first time.

## 3. Choose your AI model (only if the worked example needs one)

Of the 10 worked examples that work today, 6 need no AI model at all, such as *Safe agent actions* and *Check claims against
your sources*. The others show an **AI model** panel at the top of the page:

- **Free and private:** install [Ollama](https://ollama.com), then choose **ollama** as the provider. Nothing leaves
  your computer. A recent laptop with 16 GB of memory is enough for the default model.
- **Your own key:** choose **anthropic**, **openai** or **openrouter**, then add its key to your key file,
  `~/.g6/.env`, as one line such as `OPENROUTER_API_KEY=your-key`. The page never takes a key. You pay the provider
  directly.

## 4. Use it

Follow the steps on the page. Each worked-example page labels its result:

| Label | Meaning |
|---|---|
| `verified` | Every required check passed on independent evidence. |
| `qualified-draft` | Useful, but something is unproven — the page says exactly what. |
| `blocked-escalated` | G6 stopped and tells you what it needs from you. |

## Your data

Everything G6 stores lives in a folder called `.g6` in your home folder. Delete it to remove everything.
