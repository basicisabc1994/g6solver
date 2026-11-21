# SPDX-License-Identifier: Apache-2.0
"""The only renderer of ctx_git refusal and audit-warning text.

Identifiers select literal G6 sentences. Steering scope/key forms and metadata
file labels are selected from closed vocabularies. Raw keys, values, paths,
arguments and upstream exception messages are never part of the output.
"""
from __future__ import annotations

MESSAGES = {
    'config_check_1': "repository metadata exceeds G6's admission time limit; git was not run",
    'config_account_1': "repository metadata exceeds G6's line limit per file or repository; git was not run",
    'alternate_folders': "objects/info/alternates exceeds G6's limit of 64 distinct object folders per repository (missing targets count)",
    'config_json': 'the ctx_git configuration is not JSON G6 can read',
    'config_object': 'the ctx_git configuration must be a JSON object with only roots and remote_hosts',
    'config_roots': 'roots must be a list of folder paths',
    'config_hosts': 'remote_hosts must be a list of host names, such as github.com',
    'config_absolute_root': 'each root is an absolute path (~ is not expanded)',
    'config_root_check': 'a root is not a local folder G6 can check',
    'config_load_1': "the ctx_git configuration file could not be read as UTF-8 text of at most 1 MiB; check ctx_git/config.json under G6's home",
    'path_check': 'the path could not be opened and checked',
    'metadata_check': '{metadata} could not be checked or read, so git was not run',
    'metadata_spelling': '{metadata} holds an entry whose spelling G6 cannot check',
    'metadata_network': '{metadata} names a network or device path outside the checked folders',
    'metadata_drive_outside': '{metadata} names a drive-relative target outside the checked folders',
    'metadata_drive_relative': '{metadata} holds a drive-relative entry whose spelling G6 cannot check',
    'metadata_rooted': '{metadata} holds a rooted entry whose target G6 cannot determine',
    'metadata_drive': '{metadata} holds a drive entry G6 cannot interpret on this system',
    'repository_no_roots': "no folder is declared for ctx_git, so git is not run anywhere: the person who runs G6 declares the folders ctx_git may use in G6_GIT_CONFIG or in ctx_git/config.json under G6's home, using the roots list",
    'config_repository_1': 'give repo: the absolute path of a repository under a declared root',
    'config_repository_2': 'repo is an absolute path (a relative one, or ~, is never resolved against the folder G6 runs in)',
    'repository_outside': "the repository is not at or under a declared root (a link out of one counts as outside): the person who runs G6 declares the folders ctx_git may use in G6_GIT_CONFIG or in ctx_git/config.json under G6's home, using the roots list",
    'repository_top': 'the repository is not the top folder of a git working copy (it has no .git)',
    'repository_gitfile': '.git is a file that names no git folder',
    'repository_gitdir_outside': 'the git folder is outside every declared root (its .git links out of the root)',
    'repository_gitdir_missing': 'the git folder is missing',
    'repository_metadata_folder': 'the repository metadata entry is not a git metadata folder',
    'config_common_1': 'commondir is not a file naming a git folder',
    'config_common_2': 'commondir names a git folder outside every declared root',
    'config_common_3': 'commondir names no existing git folder',
    'config_includes_1': "repository config includes form a loop or exceed G6's nesting limit",
    'config_includes_2': "repository config exceeds G6's included-file limit",
    'config_file_outside': 'repository config names a file outside the checked folders',
    'config_includes_3': 'repository config holds syntax or a file path G6 cannot classify; git was not run',
    'config_alternates_1': "the repository's object folder is missing",
    'config_alternates_2': 'objects/info/alternates names an existing entry that is not a folder with an identity G6 can check',
    'config_alternates_3': 'objects/info/alternates nests deeper than git follows, or is not a file',
    'config_alternates_4': 'objects/info/alternates borrows objects from a folder outside every declared root (objects/info/alternates)',
    'clone_no_roots': "no folder is declared for ctx_git, so nothing is cloned: the person who runs G6 declares the folders ctx_git may use in G6_GIT_CONFIG or in ctx_git/config.json under G6's home, using the roots list",
    'config_destination_1': 'dest is the absolute path of a new folder under a declared root',
    'destination_outside': "the destination is not inside a declared root: the person who runs G6 declares the folders ctx_git may use in G6_GIT_CONFIG or in ctx_git/config.json under G6's home, using the roots list",
    'config_destination_2': 'dest names a declared root; give a new folder inside it',
    'destination_occupied': 'the destination already exists and is not an empty folder',
    'destination_parent': 'the parent folder of the destination does not exist',
    'config_inside_1': 'give a path in the repository, without an option or control character',
    'config_inside_2': 'the path is outside the repository (a link out of it counts as outside)',
    'config_inside_3': "the path is inside the repository's .git folder, which no op touches",
    'config_remote_host_1': 'the remote address is not an https or ssh address',
    'config_remote_host_2': 'only https and ssh remotes are reached, not a local path',
    'remote_scheme': 'only https and ssh remotes are reached',
    'config_remote_host_3': 'the remote address carries a password; G6 does not send one',
    'revision_input': 'the requested revision is not valid (no spaces, and it may not start with -)',
    'ref_input': 'the requested branch or tag name is not valid (letters, digits, . _ / -; no .., no leading -)',
    'remote_input': 'the requested remote name is not valid',
    'dirty': 'the working copy has uncommitted changes; commit or stash them first (no flag goes past this)',
    'ops_blame_1': 'blame needs file: a file in the repository',
    'ops_changes_since_1': 'changes_since needs since (a commit): the audit log holds no earlier point for this repository',
    'since_commit': 'since is not a commit in this repository (history may have been rewritten elsewhere)',
    'ops_add_1': 'add needs paths, or all_files true',
    'ops_commit_1': 'commit needs message',
    'ops_merge_1': 'merge needs branch: what to merge into the current branch',
    'merge_commit': 'the requested branch is not a commit in this repository',
    'merge_conflict': 'the merge conflicts in the working copy; the merge was aborted, so the working copy is as it was',
    'ops_stash_save_1': 'there are no local changes to stash',
    'ops_stash_pop_1': 'stash pop did not apply cleanly; git keeps the stash',
    'ops_reset_staged_1': 'reset_staged needs paths, or all_files true',
    'remote_missing': 'the requested remote does not exist',
    'remote_policy': 'the remote address does not meet the declared host and public-address policy',
    'ssh_missing': 'the remote uses ssh and there is no ssh program on PATH',
    'steering': '{scope} config sets {form}; ctx_git refuses configured steering for this network operation',
    'push_branch_missing': 'the requested local branch does not exist to push',
    'push_read_back': 'git reported the push done, but the remote branch does not show the pushed commit',
    'ops_clone_1': 'clone needs url: an https or ssh address',
    'git_missing': 'git is not on PATH. G6 never installs it; install git 2.31 or later from git-scm.com and put it on PATH.',
    'runner_runner_1': 'config selects additive URL rewrites that cannot be disabled with -c; the network command was not run',
    'runner_runner_2': "G6's stop is engaged, so git was ended",
    'command_timeout': 'git did not finish within the command time limit and was ended',
    'command_protocol_limit': 'git wrote more than G6 reads from one command: narrow the request (paths, rev, max_count)',
    'command_oserror': 'git could not be run to the end; check the repository and the requested operation',
    'command_output_limit': 'git wrote more bytes than one call keeps: narrow the request (paths, rev, max_count)',
    'command_failed': 'git failed; use status to check the repository and the requested operation',
    'runner_prepared_1': 'config key spelling cannot be classified; git was not run',
    'runner_prepared_2': 'config selects a foreign-VCS helper; git was not run',
    'runner_prepared_3': 'config key spelling cannot be safely overridden; no operation was run',
    'unsafe_config': 'the repository config is unsafe or selects a submodule; git was not run',
    'config_timeout': "git did not answer within the command time limit while G6 read the repository's config",
    'block_intend_1': "the audit log under G6's home could not be written, so the op was not run: a change ctx_git made would go unrecorded",
    'refused': 'ctx_git refused the operation; check the request and the declared policy',
    'path_outside': 'the path is outside every declared root (a link out counts as outside)',
    'metadata_outside': '{metadata}: the path is outside the checked root',
    'remote_host_policy': 'the remote host is not one of the remote hosts the person declared in remote_hosts',
    'remote_address_policy': 'the remote host failed the public-address policy check',
    'gate_held': 'Nothing was sent: the action gate (g6.action_gating) holds this for a person. A person can run the command shown under their own git config.',
    'gate_unavailable': 'the action gate could not decide',
    'audit_unwritten': "the audit log under G6's home could not be written",
    'audit_outcome': " The audit log could not record this call's outcome: its intent is recorded, and changes_since will count what it changed as not through ctx_git.",
}

SCOPE_WORDS = {
    "local": "repository", "worktree": "repository", "repository": "repository",
    "global": "global", "system": "system", "command": "command",
}

METADATA_NAMES = {
    "repo": "repo", ".git": ".git", "objects": "objects", "refs": "refs", "commondir": "commondir",
    "repository config": "repository config", "repository config include": "repository config include",
    "repository config file": "repository config file", "objects/info/alternates": "objects/info/alternates",
}


def _key_form(key: str) -> str:
    """Select only literal forms; even the variable name is never copied."""
    parts = key.lower().split(".")
    section, name = parts[0], parts[-1]
    scoped = len(parts) > 2
    if section == "url" and scoped:
        if name == "insteadof":
            return "url.<subsection>.insteadOf"
        if name == "pushinsteadof":
            return "url.<subsection>.pushInsteadOf"
        return "url.<subsection>.<name>"
    if section == "http":
        if scoped:
            return "http.<url>.<name>"
        return "http.<name>"
    if section == "credential":
        if scoped:
            return "credential.<url>.<name>"
        return "credential.<name>"
    if section == "core":
        if scoped:
            return "core.<subsection>.<name>"
        return "core.<name>"
    if section == "remote":
        return "remote.<subsection>.<name>"
    if section == "includeif":
        return "includeIf.<subsection>.<name>"
    if section == "include":
        return "include.<name>"
    if section == "push":
        return "push.<name>"
    if section == "fetch":
        return "fetch.<name>"
    return "<key>"


def render(identifier: str, *, key: str = "", scope: str = "", metadata: str = "") -> str:
    """Render from fixed vocabulary, failing closed for unknown selectors."""
    message = MESSAGES.get(identifier, MESSAGES["refused"])
    if identifier == "steering":
        word = SCOPE_WORDS.get(scope)
        if word is None:
            return MESSAGES["refused"]
        return message.format(scope=word, form=_key_form(key))
    if identifier in ("metadata_check", "metadata_outside", "metadata_spelling", "metadata_network",
                      "metadata_drive_outside", "metadata_drive_relative", "metadata_rooted", "metadata_drive"):
        label = METADATA_NAMES.get(metadata)
        if label is None:
            return MESSAGES["refused"]
        return message.format(metadata=label)
    return message
