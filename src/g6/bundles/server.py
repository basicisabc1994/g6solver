# SPDX-License-Identifier: Apache-2.0
"""The local bundle runner: a private page on this computer, nothing on the internet.

``serve(bundle)`` binds 127.0.0.1 on a free port, prints a link and opens the browser at it. Protections:

* every launch gets a random token, required on every API call. It travels only in the fragment of the link the
  command prints and opens (``/#token=...``), which a browser never sends to a server; the page reads it from
  ``location.hash`` and never carries it, since any program on this computer can fetch the page (BUNDLES_RUNNER-01);
* the ``Host`` header must be this server's own address (blocks DNS-rebinding pages);
* ``Origin``, when sent, must be this server too (blocks other sites posting to it);
* model settings (provider and model, never a key) live in ``$G6_HOME/bundles/settings.json`` with owner-only
  permissions; keys come only from the person's key file (:mod:`g6.core.keys`), which the page never takes, writes
  or shows.
"""
from __future__ import annotations

import html
import json
import os
import secrets
import threading
import uuid
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

from g6.bundles import AWAITING_NOTE, Bundle, BundleResult, awaiting_certificate

PROVIDERS = ("none", "ollama", "anthropic", "openai", "openrouter")
_MAX_BODY = 1_000_000


def settings_path():
    from g6.core.storage import state_path

    return state_path("bundles", "settings.json")


def load_settings() -> dict[str, str]:
    path = settings_path()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {"provider": "none", "model": ""}
    if not isinstance(data, dict):
        data = {}
    settings = {k: str(data.get(k) or "") for k in ("provider", "model")}   # a key an older file holds is ignored
    if settings["provider"] not in PROVIDERS:
        settings["provider"] = "none"
    return settings


def save_settings(provider: str, model: str = "") -> dict[str, str]:
    """Store the model choice; the file is rewritten whole, so a key an older version stored there is dropped."""
    if provider not in PROVIDERS:
        raise ValueError(f"provider must be one of {', '.join(PROVIDERS)}")
    data = {"provider": provider, "model": model.strip()}
    path = settings_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        json.dump(data, fh)
    os.chmod(path, 0o600)
    return data


def public_settings(s: dict[str, str]) -> dict[str, Any]:
    """What the page may see: the choice, the key's name, whether the key file holds it and where that file is."""
    from g6.core import keys
    from g6.core.llm import PROVIDER_KEYS

    key_name = PROVIDER_KEYS.get(s["provider"], "")
    return {"provider": s["provider"], "model": s["model"], "key_name": key_name,
            "has_key": bool(key_name) and keys.present(key_name), "key_file": str(keys.path())}


def caller_from_settings(s: dict[str, str]):
    from g6.core.llm import provider_from_env

    env = {"G6_LLM_PROVIDER": s["provider"], "G6_LLM_MODEL": s["model"]}   # the key comes from the key file
    if s["provider"] == "ollama" and os.environ.get("OLLAMA_BASE_URL"):
        env["OLLAMA_BASE_URL"] = os.environ["OLLAMA_BASE_URL"]
    return provider_from_env(env)


def run_bundle(bundle: Bundle, inputs: dict[str, str], llm: Any = None) -> dict[str, Any]:
    """Run once and always return a labelled result; failures are ``blocked-escalated``, never raised."""
    if llm is None and bundle.needs_model:
        llm = caller_from_settings(load_settings())
    try:
        result = bundle.run({k: str(v) for k, v in inputs.items()}, llm)
    except Exception as exc:  # noqa: BLE001 - the page must get an honest label, not a stack trace
        result = BundleResult("blocked-escalated", f"The bundle could not finish: {type(exc).__name__}: {exc}",
                              code="G6_E_BUNDLE_FAILED")
    return result.to_dict(request_id=uuid.uuid4().hex[:12])


def render_page(bundle: Bundle) -> str:
    """The form page. It never carries the token: its script reads the token from the link's fragment."""
    e = html.escape
    fields = "\n".join(
        f'<label for="f-{e(f.name)}">{e(f.label)}</label>'
        + (f'<textarea id="f-{e(f.name)}" name="{e(f.name)}" rows="8" placeholder="{e(f.placeholder)}"></textarea>'
           if f.kind == "textarea" else
           f'<input id="f-{e(f.name)}" name="{e(f.name)}" placeholder="{e(f.placeholder)}">')
        + (f'<small>{e(f.help)}</small>' if f.help else "")
        for f in bundle.fields)
    options = "".join(f'<option value="{p}">{p}</option>' for p in PROVIDERS)
    settings = "" if not bundle.needs_model else f"""
<details id="settings"><summary>AI model</summary>
<p>Choose a free local model (Ollama) or a paid one. G6 takes a key only from your key file, never from this
page: add a line such as <code>OPENROUTER_API_KEY=your-key</code> to it yourself.</p>
<label for="provider">Provider</label><select id="provider">{options}</select>
<label for="model">Model (needed for openai and openrouter; optional otherwise)</label><input id="model">
<p id="keynote"></p>
<button type="button" id="save">Save</button> <span id="saved"></span></details>"""
    disclaimer = f'<p class="disclaimer">{e(bundle.disclaimer)}</p>' if bundle.disclaimer else ""
    held = f'<p class="disclaimer"><strong>{e(AWAITING_NOTE)}</strong></p>' if awaiting_certificate(bundle) else ""
    return f"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>{e(bundle.title)} · G6</title>
<style>
body{{font:16px/1.5 system-ui,sans-serif;max-width:60rem;margin:0 auto;padding:1rem;color:#1b1b1f;background:#fff}}
label{{display:block;font-weight:600;margin-top:1rem}} textarea,input,select{{width:100%;box-sizing:border-box;font:inherit;padding:.4rem}}
small{{color:#555}} button{{margin-top:1rem;padding:.5rem 1.2rem;font:inherit}} table{{border-collapse:collapse;width:100%;margin-top:1rem}}
td,th{{border:1px solid #ccc;padding:.3rem .5rem;text-align:left;vertical-align:top}} .wrap{{overflow-x:auto}}
.label{{display:inline-block;padding:.1rem .6rem;border-radius:1rem;font-weight:600}}
.verified{{background:#d7f5dd}} .qualified-draft{{background:#fff1c2}} .blocked-escalated{{background:#ffd6d6}}
.disclaimer{{border-left:4px solid #c90;padding-left:.6rem}}
@media (prefers-color-scheme:dark){{body{{background:#16161a;color:#eee}} small{{color:#aaa}} .label{{color:#111}}}}
</style></head><body>
<h1>{e(bundle.title)}</h1>{held}<p>{e(bundle.summary)}</p>{disclaimer}{settings}
<form id="form">{fields}<button type="submit">Run</button></form>
<section id="out" aria-live="polite"></section>
<p><small>Runs only on this computer. Open source — G6 harness engineering.</small></p>
<script>
const TOKEN=new URLSearchParams(location.hash.slice(1)).get("token")||"";
if(!TOKEN)document.getElementById("out").textContent="Open this page from the link the G6 window printed (it ends "+
 "in #token=...): the page does not work without it.";
async function api(path,body){{const r=await fetch(path,{{method:body?"POST":"GET",
 headers:{{"Content-Type":"application/json","X-G6-Token":TOKEN}},body:body?JSON.stringify(body):undefined}});return r.json();}}
function esc(s){{const d=document.createElement("div");d.textContent=String(s);return d.innerHTML;}}
document.getElementById("form").onsubmit=async ev=>{{ev.preventDefault();
 const out=document.getElementById("out");out.textContent="Working…";
 const inputs=Object.fromEntries(new FormData(ev.target));const r=await api("/api/run",{{inputs}});
 if(r.error){{out.innerHTML="<p>"+esc(r.error)+"</p>";return;}}
 let h='<h2>Result <span class="label '+esc(r.completion_state)+'">'+esc(r.completion_state)+'</span></h2><p>'+esc(r.summary)+'</p>';
 if(r.rows.length){{const cols=Object.keys(r.rows[0]);h+='<div class="wrap"><table><tr>'+cols.map(c=>"<th>"+esc(c)+"</th>").join("")+"</tr>"+
  r.rows.map(row=>"<tr>"+cols.map(c=>"<td>"+esc(row[c]??"")+"</td>").join("")+"</tr>").join("")+"</table></div>";}}
 if(r.notes.length)h+="<ul>"+r.notes.map(n=>"<li>"+esc(n)+"</li>").join("")+"</ul>";out.innerHTML=h;}};
const save=document.getElementById("save");
function keyNote(s){{keynote.textContent=!s.key_name?"":s.key_name+(s.has_key?" is in ":" is not yet in ")+
 "your key file, "+s.key_file+".";}}
if(save&&TOKEN){{api("/api/settings").then(s=>{{provider.value=s.provider;model.value=s.model;keyNote(s);}});
 save.onclick=async()=>{{const s=await api("/api/settings",{{provider:provider.value,model:model.value}});
  if(!s.error)keyNote(s);saved.textContent=s.error||"Saved.";}};}}
</script></body></html>"""


def make_handler(bundle: Bundle, token: str, llm: Any = None) -> type[BaseHTTPRequestHandler]:
    class Handler(BaseHTTPRequestHandler):
        server_version = "g6-bundle"

        def log_message(self, *args: Any) -> None:  # keep inputs out of the terminal
            pass

        def _send(self, status: int, body: bytes, ctype: str) -> None:
            self.send_response(status)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Referrer-Policy", "no-referrer")
            self.send_header("Content-Security-Policy",
                             "default-src 'none'; script-src 'unsafe-inline'; style-src 'unsafe-inline'; "
                             "connect-src 'self'; form-action 'none'; frame-ancestors 'none'")
            self.end_headers()
            self.wfile.write(body)

        def _json(self, status: int, value: Any) -> None:
            self._send(status, json.dumps(value).encode(), "application/json")

        def _allowed_hosts(self) -> set[str]:
            port = self.server.server_address[1]
            return {f"127.0.0.1:{port}", f"localhost:{port}"}

        def _trusted(self, *, api: bool) -> bool:
            if self.headers.get("Host", "") not in self._allowed_hosts():
                return False
            origin = self.headers.get("Origin")
            if origin is not None and origin.removeprefix("http://") not in self._allowed_hosts():
                return False
            return not api or secrets.compare_digest(self.headers.get("X-G6-Token", ""), token)

        def do_GET(self) -> None:  # noqa: N802
            if self.path == "/":
                if not self._trusted(api=False):
                    return self._send(403, b"forbidden", "text/plain")
                return self._send(200, render_page(bundle).encode(), "text/html; charset=utf-8")
            if self.path == "/api/settings":
                if not self._trusted(api=True):
                    return self._json(403, {"error": "forbidden"})
                return self._json(200, public_settings(load_settings()))
            self._send(404, b"not found", "text/plain")

        def _discard_body(self) -> None:
            """Read (at most ``_MAX_BODY`` bytes of) a body we refuse: closing a socket with unread data resets it on
            Windows, so the client would see a dropped connection instead of the refusal."""
            try:
                length = int(self.headers.get("Content-Length", "0"))
            except ValueError:
                return
            if 0 < length <= _MAX_BODY:
                self.rfile.read(length)

        def do_POST(self) -> None:  # noqa: N802
            if self.path not in ("/api/run", "/api/settings"):
                self._discard_body()
                return self._send(404, b"not found", "text/plain")
            if not self._trusted(api=True):
                self._discard_body()
                return self._json(403, {"error": "forbidden"})
            try:
                length = int(self.headers.get("Content-Length", "0"))
                if length > _MAX_BODY:
                    return self._json(413, {"error": "input too large"})
                body = json.loads(self.rfile.read(length) or b"{}")
                if not isinstance(body, dict):
                    raise ValueError("body must be a JSON object")
            except ValueError as exc:
                return self._json(400, {"error": f"bad request: {exc}"})
            if self.path == "/api/settings":
                if "api_key" in body:
                    from g6.core import keys

                    return self._json(400, {"error": "G6 takes a key only from your key file, never from this page: "
                                                    f"add the line NAME=<your key> to {keys.path()} yourself"})
                try:
                    saved = save_settings(str(body.get("provider", "none")), str(body.get("model", "")))
                except ValueError as exc:
                    return self._json(400, {"error": str(exc)})
                return self._json(200, public_settings(saved))
            inputs = body.get("inputs") or {}
            if not isinstance(inputs, dict):
                return self._json(400, {"error": "inputs must be an object"})
            return self._json(200, run_bundle(bundle, inputs, llm))

    return Handler


def start(bundle: Bundle, *, port: int = 0, llm: Any = None) -> tuple[ThreadingHTTPServer, str, str]:
    """Start in a background thread; returns ``(server, url, token)``. Stop with ``server.shutdown()``."""
    token = secrets.token_urlsafe(24)
    server = ThreadingHTTPServer(("127.0.0.1", port), make_handler(bundle, token, llm))
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server, f"http://127.0.0.1:{server.server_address[1]}/", token


def serve(bundle: Bundle, *, port: int = 0, open_browser: bool = True) -> None:
    """Run until Ctrl+C (or the window is closed). The printed and opened link carries the token in its fragment."""
    server, url, token = start(bundle, port=port)
    link = f"{url}#token={token}"
    try:
        print(f"{bundle.title} is running at {link}\nKeep this window open; press Ctrl+C to stop.", flush=True)
        if open_browser:
            webbrowser.open(link)
        threading.Event().wait()
    except KeyboardInterrupt:
        pass
    finally:
        server.shutdown()
