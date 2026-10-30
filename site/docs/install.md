# Get started — no programming needed

!!! info "Where the downloads are"
    Each worked example's downloads are on [Worked examples](bundles/index.md) and on the
    [releases page](https://github.com/basicisabc1994/g6solver/releases), where they are called bundles. A developer
    can also run one from a copy of the code: `pip install .`, then for example `g6 bundle check-claims`.

## 1. Choose a worked example

Go to [Worked examples](bundles/index.md) and download the one for your problem. Pick the file for your computer:
Windows (`…-windows-….exe`), macOS (`…-macos-arm64.zip`, for Apple-silicon Macs; there is no Intel Mac build yet)
or Linux (`…-linux-x86_64.zip`). On macOS and Linux, double-click the `.zip` to unpack it.

## 2. Open it

Double-click the file. A small window opens (keep it open while you work) and your web browser opens a
private G6 page. It runs only on your computer. Close the small window to stop it.

Where no operating-system boundary can confine it (macOS, Linux without bubblewrap, and Windows before the one-time
access grant), G6 refuses to run untrusted code for you (a patch, a generated script, a repository's tests) unless you
allow it once with g6 setup. Code you allow then has your own rights: it can read your key file ~/.g6/.env and your
other files, and its network guard works inside Python only. You are responsible for what you run.

An app that runs code you give it (agent-change-check runs your repository's tests; ml-check runs your
script) asks on its page, the first time, whether it may run that code without a boundary where your
computer has none. Your answer is kept for this computer; `g6 setup` changes it.

??? question "The browser did not open"
    The small window shows an address like `http://127.0.0.1:52817/`. Copy it into your browser.

??? question "Windows says “Windows protected your PC”"
    G6's worked examples are not code-signed (signing costs money every year, and G6 is free). Click **More info**,
    then **Run anyway**. You can check the download first: each release includes `SHA256SUMS.txt`.

??? question "macOS says the app “cannot be opened”"
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

Follow the steps on the page. Every result is labelled:

| Label | Meaning |
|---|---|
| `verified` | Every required check passed on independent evidence. |
| `qualified-draft` | Useful, but something is unproven — the page says exactly what. |
| `blocked-escalated` | G6 stopped and tells you what it needs from you. |

## Your data

Everything G6 stores lives in a folder called `.g6` in your home folder. Delete it to remove everything.
