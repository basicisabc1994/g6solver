# SPDX-License-Identifier: Apache-2.0
"""Git/gh command plans through an explicitly injected host command capability.

No subprocess, shell, network or executable discovery. Every command is argv;
multiline bodies/commit messages use stdin via --body-file/--file. The command
port owns authentication, exit checking, confinement and exact-commit custody.
Do not construct this adapter inside an untrusted engineer mission.
"""
from difflib import unified_diff
from pathlib import Path, PurePosixPath
import re

from g6.core import stop as global_stop
from g6.core.ledger import digest
from g6.self_training.merge_gate import Change, normal
from g6.self_training.autonomy_gate import authority


class GhRepository:
    def __init__(self, *, root, command, stop, session_trailers, pinned_issue=None, exact_merger=None, config=None):
        self.root, self.command, self.stop = str(root), command, stop
        self.trailers, self.pinned_issue = tuple(session_trailers), pinned_issue
        self.exact_merger = exact_merger
        self.config = config if config is not None else Path(__file__).parents[1] / 'self_training/autonomy.json'
        if (len(self.trailers) != 2 or not self.trailers[0].startswith('Co-Authored-By: ')
                or not self.trailers[1].startswith('Claude-Session: ')
                or any('\n' in t or '\r' in t for t in self.trailers)):
            raise ValueError('two explicit single-line session trailers required')

    def stopped(self):
        # The host stop callback includes loop:stop repository labels, refreshed
        # independently. Unknown/stale remote stop state must return True.
        return bool(global_stop.engaged() or self.stop())

    def _run(self, argv, *, cwd=None, stdin=None):
        if self.stopped():
            raise ValueError('stopped before repository side effect')
        grants = authority(self.config)
        if not grants.permits('push') or not grants.permits('draft_pr'):
            raise ValueError('repository push/draft authority is off')
        return self.command(tuple(argv), cwd=cwd or self.root, stdin=stdin)

    @staticmethod
    def _public(text):
        if re.search(r'(?i)([a-z]:[\\/]|/(?:users|home)/|BEGIN [A-Z ]*PRIVATE KEY)', text):
            raise ValueError('public branch content contains a private path or key marker')

    def _worktree(self, branch, parent, key):
        path = str(PurePosixPath(self.root.replace('\\', '/')) / '.claude/worktrees' / ('auto-' + key[:20]))
        self._run(('git', 'worktree', 'add', '--detach', '--', path, parent))
        if branch is not None:
            self._run(('git', 'switch', '-c', branch), cwd=path)
        return path

    def _publish(self, path, branch, message, body, labels=()):
        self._public(message + body + '\n'.join(self.trailers))
        self._run(('git', 'commit', '--file', '-'), cwd=path,
                  stdin=message + '\n\n' + '\n'.join(self.trailers) + '\n')
        self._run(('git', 'push', '--', 'origin', f'HEAD:refs/heads/{branch}'), cwd=path)
        argv = ['gh', 'pr', 'create', '--draft', '--base', 'main', '--head', branch,
                '--title', message, '--body-file', '-']
        for label in labels:
            argv.extend(('--label', label))
        return self._run(argv, cwd=path, stdin=body).strip()

    def propose(self, change, *, labels=()):
        change = Change.model_validate_json(change.model_dump_json())
        patch = []
        for file in change.files:
            normal(file.path)
            path = file.path.replace('\\', '/')
            if file.old_path or file.mode != 'regular' or any(c in path for c in ('\n', '\r', '\t', '"')):
                raise ValueError('only direct regular UTF-8 changes supported by the draft adapter')
            if path.startswith('-') or any(part in {'.git', '.env', '.g6', '.codex'} for part in path.split('/')):
                raise ValueError('repository metadata or secret path refused')
            self._public((file.before or '') + (file.after or ''))
            if any(text and not text.endswith('\n') for text in (file.before, file.after)):
                raise ValueError('draft patches require LF-terminated text')
            patch.extend(unified_diff((file.before or '').splitlines(keepends=True),
                (file.after or '').splitlines(keepends=True),
                fromfile='a/' + path if file.before is not None else '/dev/null',
                tofile='b/' + path if file.after is not None else '/dev/null'))
        self._public('\n'.join(self.trailers))
        worktree = self._worktree(change.branch, change.parent_commit, change.sha256)
        self._run(('git', 'apply', '--index', '-'), cwd=worktree, stdin=''.join(patch))
        return self._publish(worktree, change.branch, f'G6 proposal {change.sha256[:12]}',
            f'Change: {change.sha256}\nClaim: {change.claim}\nHost evaluation and certificate required before merge.\n', labels)

    def merge_exact(self, change):
        if self.stopped():
            raise ValueError('stopped before merge')
        if self.exact_merger is None:
            raise ValueError('gh squash cannot guarantee the exact resulting commit; host exact merger required')
        if not authority(self.config).permits('merge'):
            raise ValueError('exact merge authority is off')
        # This host capability must atomically compare main, PR head and final SHA
        # and obey GitHub rules. --match-head-commit alone is not that guarantee.
        return self.exact_merger(change)

    def revert_pr(self, change, *, evidence, key):
        branch = 'claude/g6-auto/revert-' + key[:20]
        path = self._worktree(branch, change.merge_commit, key)
        self._run(('git', 'revert', '--no-commit', change.merge_commit), cwd=path)
        return self._publish(path, branch, f'G6 revert generation {evidence["generation"]}',
            f'Reverted commit: {change.merge_commit}\nMeasured regression receipt: {evidence["receipt"]}\n', ('regression',))

    def publish_digest(self, day, body):
        if not re.fullmatch(r'\d{4}-\d{2}-\d{2}', day):
            raise ValueError('ISO digest day required')
        if self.pinned_issue is None:
            raise ValueError('owner pinned digest issue required')
        if not authority(self.config).permits('digest'):
            raise ValueError('digest authority is off')
        self._public(body)
        # Digest-only protected path changes are still draft PRs for the owner.
        key = digest({'day': day, 'body': body})
        # The owner bootstraps this fixed stream once. Start each later day from
        # its fresh tip, commit detached and push fast-forward, without resets.
        self._run(('git', 'fetch', '--no-tags', 'origin', 'refs/heads/claude/g6-auto/digest'))
        parent = self._run(('git', 'rev-parse', '--verify', 'FETCH_HEAD^{commit}')).strip()
        if not re.fullmatch(r'[0-9a-f]{40}([0-9a-f]{24})?', parent):
            raise ValueError('digest stream has no checked commit tip')
        path = self._worktree(None, parent, key)
        target = 'validation/distill/digest/' + day + '.md'
        patch = ''.join(unified_diff([], body.splitlines(keepends=True), fromfile='/dev/null', tofile='b/' + target))
        self._run(('git', 'apply', '--index', '-'), cwd=path, stdin=patch)
        pr = self._publish(path, 'claude/g6-auto/digest', f'G6 daily digest {day}', body, ('protected-change',))
        self._run(('gh', 'issue', 'comment', str(self.pinned_issue), '--body-file', '-'),
                  stdin=f'Daily research digest {day}: {pr}\n')
        return pr
