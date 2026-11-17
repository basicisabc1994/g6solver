# SPDX-License-Identifier: Apache-2.0
"""Command-scope policy for Git's documented file and program indirections.

Keep the reason beside each decision. The independent pinned documentation inventory
in tests/sources/data/git_path_command_keys.txt must be covered by this table.
Subsection keys are discovered with a bounded names-only query, never by opening
their targets. Repository includes are admitted first; native content-reading
settings and repository info rules retain their scopes; configured external
ignore and attribute files cannot control review visibility.
"""
import os
import re
from dataclasses import dataclass


@dataclass(frozen=True)
class Rule:
    key: str
    action: str
    value: str
    reason: str


RULES = (
    Rule("include.path", "classified", "", "Repository includes are read and bounded; personal includes stay native."),
    Rule("includeIf.<condition>.path", "classified", "", "Admission checks even inactive conditional includes."),
    Rule("bundle.<id>.uri", "exempt", "", "Bundle discovery is disabled; ctx_git never supplies --bundle-uri."),
    Rule("attr.tree", "exempt", "", "A tree reference reads only the admitted object store; attribute programs are disabled."),
    Rule("core.worktree", "classified", "", "GIT_WORK_TREE pins the admitted worktree before every query."),
    Rule("core.hooksPath", "override", "@hooks", "Use a new empty scratch hooks directory."),
    Rule("core.fsmonitor", "override", "false", "Disable both the monitor hook and built-in daemon."),
    Rule("core.sshCommand", "override", "", "Only the PATH-selected SSH program may be used by network sessions."),
    Rule("core.gitProxy", "override", "none", "No repository-selected proxy program; git transport is disabled."),
    Rule("core.alternateRefsCommand", "override", "", "Never run an alternate object-store shell command."),
    Rule("core.excludesFile", "override", "@null", "Repository ignore rules remain native; configured files and home defaults are disabled."),
    Rule("core.attributesFile", "override", "@null", "Repository attributes remain native; configured files and home defaults are disabled."),
    Rule("core.askPass", "override", "", "Disable password prompt programs."),
    Rule("core.editor", "override", ":", "Use Git's no-editor command."),
    Rule("core.pager", "override", "", "Disable shell pagers; --no-pager also covers early queries."),
    Rule("credential.helper", "override", "", "Empty value resets the multi-valued helper list."),
    Rule("credential.<url>.helper", "override", "", "Reset URL-scoped helpers as well as the generic helper list."),
    Rule("diff.external", "override", "", "Disable a repository-selected external diff program."),
    Rule("diff.orderFile", "override", "@null", "An empty order file preserves default ordering without an external read."),
    Rule("diff.<driver>.command", "override", "", "Disable each named external diff driver."),
    Rule("diff.<driver>.textconv", "override", "", "Disable each named text conversion program."),
    Rule("filter.<driver>.clean", "override", "", "Disable named check-in filters."),
    Rule("filter.<driver>.smudge", "override", "", "Disable named checkout filters."),
    Rule("filter.<driver>.process", "override", "", "Disable long-running named filters."),
    Rule("filter.<driver>.required", "override", "false", "A disabled filter must not prevent normal tree reads."),
    Rule("merge.<driver>.driver", "override", "", "Disable custom merge programs."),
    Rule("commit.template", "override", "@null", "No external commit message template."),
    Rule("commit.gpgSign", "override", "false", "Do not sign commits."),
    Rule("tag.gpgSign", "override", "false", "Do not sign tags."),
    Rule("tag.forceSignAnnotated", "override", "false", "Do not turn annotated tags into signing operations."),
    Rule("push.gpgSign", "override", "false", "Do not sign pushes."),
    Rule("log.showSignature", "override", "false", "Do not start signature verification while reading history."),
    Rule("merge.verifySignatures", "override", "false", "Do not start a verifier during pull."),
    Rule("gpg.program", "override", "", "No OpenPGP signing or verification program."),
    Rule("gpg.<format>.program", "override", "", "No format-specific signing or verification program."),
    Rule("gpg.ssh.defaultKeyCommand", "override", "", "No shell command for choosing a signing key."),
    Rule("gpg.ssh.allowedSignersFile", "override", "@null", "Never read an external allowed-signers file."),
    Rule("gpg.ssh.revocationFile", "override", "@null", "Never read an external key revocation file."),
    Rule("user.signingKey", "override", "", "Do not read a path-valued SSH signing key."),
    Rule("mailmap.file", "override", "@null", "Do not read an augmenting mailmap outside the admitted content."),
    Rule("mailmap.blob", "override", "", "Do not resolve a repository-selected augmenting mailmap blob."),
    Rule("blame.ignoreRevsFile", "override", "", "Blame negates the file-list option before clearing accumulated revision IDs."),
    Rule("http.sslCAInfo", "override", "", "Disable configured CA file reads, including URL subsections; TLS verification stays enabled."),
    Rule("http.sslCAPath", "override", "", "Disable configured CA directory reads, including URL subsections."),
    Rule("http.sslCert", "override", "", "No repository-selected client certificate file."),
    Rule("http.sslKey", "override", "", "No repository-selected client private key file."),
    Rule("http.cookieFile", "override", "", "Empty string explicitly disables reading a cookie file."),
    Rule("http.saveCookies", "override", "false", "Never write response cookies to a configured file."),
    Rule("http.proxySSLCert", "override", "", "No repository-selected proxy client certificate."),
    Rule("http.proxySSLKey", "override", "", "No repository-selected proxy private key."),
    Rule("http.proxySSLCAInfo", "override", "", "No repository-selected proxy CA file."),
    Rule("http.pinnedPubkey", "override", "", "Do not read a repository-selected public-key file."),
    Rule("http.proxy", "override", "", "No repository-selected proxy endpoint or Unix socket."),
    Rule("http.sslVerify", "override", "true", "Keep TLS verification on, including URL-scoped settings."),
    Rule("http.followRedirects", "override", "false", "Do not follow another network path, including URL-scoped settings."),
    Rule("http.<url>.*", "classified", "", "Specific keys inherit policy; all repository HTTP keys block outward ops."),
    Rule("init.templateDir", "override", "@template", "Clone uses a new empty scratch template directory."),
    Rule("fsck.skipList", "override", "@null", "No repository-selected fsck object list."),
    Rule("fetch.fsck.skipList", "override", "@null", "No external object list during fetch checks."),
    Rule("receive.fsck.skipList", "override", "@null", "No external object list during receive checks."),
    Rule("gc.recentObjectsHook", "override", "", "Disable object-retention programs; automatic GC is also off."),
    Rule("gc.auto", "override", "0", "Never start automatic garbage collection and its helpers."),
    Rule("gc.repackFilterTo", "exempt", "", "gc.auto=0 and maintenance.auto=false disable automatic repacking."),
    Rule("maintenance.auto", "override", "false", "Never start automatic maintenance and its helpers."),
    Rule("pager.<cmd>", "override", "", "Disable per-command shell pagers."),
    Rule("sequence.editor", "override", ":", "No interactive sequence editor."),
    Rule("interactive.diffFilter", "override", "", "No shell filter for interactive diffs."),
    Rule("trailer.<keyAlias>.command", "override", "", "No deprecated trailer shell command."),
    Rule("trailer.<keyAlias>.cmd", "override", "", "No trailer shell command."),
    Rule("remote.<name>.uploadpack", "override", "git-upload-pack", "Use the standard remote service, never a configured command."),
    Rule("remote.<name>.receivepack", "override", "git-receive-pack", "Use the standard remote receive service."),
    Rule("remote.<name>.proxy", "override", "", "No per-remote proxy endpoint."),
    Rule("remote.<name>.serverOption", "classified", "", "Repository server options refuse network operations before the gate."),
    Rule("remote.<name>.tagOpt", "classified", "", "Repository tag options refuse network operations before the gate."),
    Rule("push.pushOption", "classified", "", "Repository push options refuse pushes before the gate."),
    Rule("remote.<name>.vcs", "classified", "refuse", "Foreign-VCS helpers are refused after the names-only query."),
    Rule("remote.<name>.url", "classified", "", "The expanded addresses are checked before any outward operation."),
    Rule("remote.<name>.pushurl", "classified", "", "All expanded push addresses are checked before sending."),
    Rule("url.<base>.insteadOf", "classified", "network-refuse", "Additive rewrites cannot be reset with -c; refuse outward commands."),
    Rule("url.<base>.pushInsteadOf", "classified", "network-refuse", "Additive push rewrites cannot be reset with -c; refuse outward commands."),
    Rule("protocol.allow", "override", "never", "No transport unless the session's GIT_ALLOW_PROTOCOL explicitly permits it."),
    Rule("protocol.<name>.allow", "override", "never", "Disable protocol helpers; GIT_ALLOW_PROTOCOL bounds network sessions."),
    Rule("fetch.bundleURI", "override", "", "Do not download or read a configured bundle list."),
    Rule("transfer.bundleURI", "override", "false", "Clone must not request additional bundle sources."),
    Rule("trace2.normalTarget", "override", "0", "Disable trace destinations, including files and sockets."),
    Rule("trace2.perfTarget", "override", "0", "Disable performance trace files and sockets."),
    Rule("trace2.eventTarget", "override", "0", "Disable event trace files and sockets."),
    Rule("alias.*", "exempt", "", "ctx_git uses built-in command names; aliases cannot replace Git built-ins."),
    Rule("browser.<tool>.cmd", "exempt", "", "ctx_git never invokes browser or help operations."),
    Rule("browser.<tool>.path", "exempt", "", "ctx_git never opens browser help."),
    Rule("difftool.<tool>.cmd", "exempt", "", "ctx_git never invokes difftool."),
    Rule("difftool.<tool>.path", "exempt", "", "ctx_git never invokes difftool."),
    Rule("diff.tool", "exempt", "", "ctx_git never invokes difftool."),
    Rule("diff.guitool", "exempt", "", "ctx_git never invokes graphical difftool."),
    Rule("format.signatureFile", "exempt", "", "ctx_git never invokes format-patch."),
    Rule("format.outputDirectory", "exempt", "", "ctx_git never invokes format-patch."),
    Rule("fsmonitor.socketDir", "exempt", "", "core.fsmonitor=false disables the monitor daemon and its socket."),
    Rule("gitcvs.logFile", "exempt", "", "ctx_git never starts the CVS server."),
    Rule("gitcvs.dbName", "exempt", "", "ctx_git never starts the CVS server or opens its database."),
    Rule("guitool.<name>.cmd", "exempt", "", "ctx_git never starts git-gui."),
    Rule("help.htmlPath", "exempt", "", "ctx_git never invokes help."),
    Rule("help.browser", "exempt", "", "ctx_git never opens browser help."),
    Rule("imap.tunnel", "exempt", "", "ctx_git never invokes imap-send."),
    Rule("instaweb.browser", "exempt", "", "ctx_git never invokes instaweb."),
    Rule("instaweb.httpd", "exempt", "", "ctx_git never invokes instaweb."),
    Rule("instaweb.modulePath", "exempt", "", "ctx_git never invokes instaweb."),
    Rule("man.viewer", "exempt", "", "ctx_git never invokes help or a man viewer."),
    Rule("man.<tool>.cmd", "exempt", "", "ctx_git never invokes a man viewer."),
    Rule("man.<tool>.path", "exempt", "", "ctx_git never invokes a man viewer."),
    Rule("merge.tool", "exempt", "", "Pull is fast-forward only; ctx_git never invokes mergetool."),
    Rule("merge.guitool", "exempt", "", "ctx_git never invokes graphical mergetool."),
    Rule("mergetool.<tool>.cmd", "exempt", "", "ctx_git never invokes mergetool."),
    Rule("mergetool.<tool>.path", "exempt", "", "ctx_git never invokes mergetool."),
    Rule("safe.directory", "exempt", "", "Ownership declaration only; this path is not a file-read target."),
    Rule("sendemail.smtpSSLCertPath", "exempt", "", "ctx_git never invokes send-email."),
    Rule("sendemail.mailmap.file", "exempt", "", "ctx_git never invokes send-email."),
    Rule("sendemail.mailmap.blob", "exempt", "", "ctx_git never invokes send-email."),
    Rule("sendemail.aliasesFile", "exempt", "", "ctx_git never invokes send-email."),
    Rule("sendemail.ccCmd", "exempt", "", "ctx_git never invokes send-email."),
    Rule("sendemail.toCmd", "exempt", "", "ctx_git never invokes send-email."),
    Rule("sendemail.headerCmd", "exempt", "", "ctx_git never invokes send-email."),
    Rule("sendemail.smtpServer", "exempt", "", "ctx_git never invokes send-email or a sendmail binary."),
    Rule("sendemail.<identity>.*", "exempt", "", "ctx_git never invokes identity-specific send-email operations."),
    Rule("submodule.<name>.update", "exempt", "", "Gitlinks are refused; recursive submodule operations are disabled."),
    Rule("submodule.<name>.url", "exempt", "", "Gitlinks are refused; clone never recurses into submodules."),
    Rule("tar.<format>.command", "exempt", "", "ctx_git never invokes archive."),
    Rule("uploadpack.packObjectsHook", "exempt", "", "ctx_git never starts a local upload-pack server."),
    Rule("web.browser", "exempt", "", "ctx_git never invokes help or instaweb."),
)


def _expression(pattern: str) -> re.Pattern[str]:
    expression = re.escape(pattern.lower())
    expression = re.sub(r"<[^>]+>", ".*", expression).replace(r"\*", ".*")
    return re.compile(expression + r"\Z", re.S)


_EXACT = {rule.key.lower(): rule for rule in RULES if "<" not in rule.key and "*" not in rule.key}
_PATTERNS = tuple((rule, _expression(rule.key)) for rule in RULES if "<" in rule.key or "*" in rule.key)


def rule_for(key: str) -> Rule | None:
    """Match concrete keys without letting a subsection escape a denied key class."""
    lower = key.lower()
    if lower in _EXACT:
        return _EXACT[lower]
    # Git preserves subsection spelling (including dots, URLs and the empty
    # subsection). Match each fixed denied key with inserted subsection text,
    # including multi-part fixed keys such as fetch.fsck.skipList. Restrict this
    # fallback to overrides: exemptions must never swallow a deny decision.
    for fixed, rule in _EXACT.items():
        if rule.action != "override":
            continue
        parts = fixed.split(".")
        for index in range(1, len(parts)):
            prefix, suffix = ".".join(parts[:index]) + ".", "." + ".".join(parts[index:])
            if lower.startswith(prefix) and lower.endswith(suffix) and len(lower) >= len(prefix) + len(suffix):
                return rule
    return next((rule for rule, expression in _PATTERNS if expression.fullmatch(lower)), None)


def _rendered(key: str, value: str) -> str:
    """Git cannot open the null device as an exclude file on Windows (exit 128); an empty value disables it."""
    return "" if key.lower() == "core.excludesfile" and value == os.devnull else value


def settings(keys: tuple[str, ...] = (), *, hooks: str, template: str,
             repository_files: tuple[str, ...] = ()) -> tuple[tuple[str, str], ...]:
    """Static overrides plus every concrete subsection override found by the names-only query."""
    values = {"@null": os.devnull, "@hooks": hooks, "@template": template}
    pairs = {}
    for key in (*[rule.key for rule in RULES if "<" not in rule.key and "*" not in rule.key], *keys,
                "gpg.openpgp.program", "gpg.x509.program", "gpg.ssh.program"):
        rule = rule_for(key)
        if rule is not None and rule.action == "override":
            if "=" in key or any(ord(char) < 32 or ord(char) == 127 for char in key):
                raise ValueError("config key spelling cannot be encoded as a command override")
            pairs[key] = _rendered(rule.key, values.get(rule.value, rule.value))
    for key in repository_files:
        # Only admission's two singleton tree-file keys have this decision.
        # A supplied name cannot inject a value or replace another deny key.
        if key.lower() not in {"core.excludesfile", "core.attributesfile"}:
            raise ValueError("unrecognised repository tree-file policy")
        rule = rule_for(key)
        if rule is None or rule.action != "override" or rule.value != "@null":
            raise ValueError("repository tree-file policy has no neutralisation rule")
        pairs[key] = _rendered(rule.key, values[rule.value])
    return tuple(pairs.items())
