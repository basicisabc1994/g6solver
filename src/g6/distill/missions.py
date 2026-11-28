# SPDX-License-Identifier: Apache-2.0
"""Disposable engineer mission plans. No launch, ambient mounts or sign-in discovery.

A trusted container port must enforce this plan and supply a clean image/home.
Offline is the only default network policy. With no owner sign-in grant, live
provider execution is unavailable and repository authority stops at draft PRs.
Evaluator, signer and runner state are host services, never mission mounts.
"""
from dataclasses import dataclass
from pathlib import PurePosixPath, PureWindowsPath
import re
from typing import Literal


@dataclass(frozen=True)
class Mount:
    source: str
    target: str
    read_only: bool = False


@dataclass(frozen=True)
class Mission:
    mission_id: str
    family: str
    image: str
    command: tuple[str, ...]
    mounts: tuple[Mount, ...]
    task: str
    mcp_servers: tuple = (('g6', ('g6-mcp',)),)
    environment: tuple[tuple[str, str], ...] = (('G6_HOME', '/mission-home'), ('HOME', '/mission-home'))
    signin_mounts: tuple[Mount, ...] = ()
    network: str = 'none'
    read_only_root: bool = True
    privileged: bool = False
    cap_drop: tuple[str, ...] = ('ALL',)
    no_new_privileges: bool = True
    autonomy_ceiling: str = 'draft_pr'
    tmpfs: tuple[str, ...] = ('/tmp',)
    memory_mb: int = 2048
    pids_limit: int = 128
    wall_seconds: int = 1800
    purpose: str = 'engineering'


def build_mission(*, family: Literal['claude', 'codex'], mission_id: str, worktree: str,
                  home: str, image: str, task: str, signin_mounts: tuple[Mount, ...] = (), config=None,
                  purpose: str = 'engineering') -> Mission:
    if family not in ('claude', 'codex') or not re.fullmatch('[a-z0-9-]{1,80}', mission_id):
        raise ValueError('known engineer family and safe mission id required')
    if not re.fullmatch(r'[a-z0-9./:_-]+@sha256:[0-9a-f]{64}', image):
        raise ValueError('mission image must be pinned by digest')
    path_type = PureWindowsPath if re.match(r'^[a-zA-Z]:[\\/]', worktree) else PurePosixPath
    roots = tuple(path_type(p) for p in (worktree, home))
    forbidden = {'.g6', '.codex', 'g6-held-sources', 'archive', 'certificates'}
    for index, root in enumerate(roots):
        parts = [part.casefold() for part in root.parts]
        for offset, part in enumerate(parts):
            if part in forbidden or (part == '.claude' and (index != 0 or parts[offset + 1:offset + 2] != ['worktrees'])):
                raise ValueError('host state or sign-in path is not a mission mount')
    if (any(not p.is_absolute() or '..' in p.parts for p in roots)
        or roots[0] == roots[1] or roots[0] in roots[1].parents or roots[1] in roots[0].parents
        or any(part in ('.', '..') for root in roots for part in root.parts)):
        raise ValueError('separate disposable worktree and clean home required; no host state or sign-ins')
    if not task.strip() or '\x00' in task:
        raise ValueError('mission task required')
    signin_grant = None
    if signin_mounts:
        from g6.self_training.autonomy_gate import Autonomy, authority
        signin_grant = authority(config if config is not None else Autonomy())
        if not signin_grant.permits('signin_mounts'):
            raise ValueError('sign-in mount authority is off')
    targets, sources = [], list(roots)
    for mount in signin_mounts:
        if type(mount) is not Mount or mount.read_only is not True:
            raise ValueError('sign-in mounts must be read-only')
        if signin_grant.signin_mount_paths.get(mount.target) != mount.source:
            raise ValueError('sign-in source and target must match an exact owner-configured path')
        # Pure path checks only. Never stat, resolve, open, copy or hash these paths.
        source_type = PureWindowsPath if re.match(r'^[a-zA-Z]:[\\/]', mount.source) else PurePosixPath
        source, target = source_type(mount.source), PurePosixPath(mount.target)
        if (not source.is_absolute() or len(source.parts) < 3 or mount.source.startswith(('//', '\\\\'))
            or '..' in source.parts or '\x00' in mount.source
            or '\\' in mount.target or '..' in target.parts or len(target.parts) < 3
            or target.parts[:2] != ('/', 'signin') or '\x00' in mount.target
            or any(part.casefold() in {'g6-held-sources', 'archive', 'certificates', '.g6', '.env'}
                   for part in source.parts)
            or any(source == old or source in old.parents or old in source.parents for old in sources)
            or any(target == old or target in old.parents or old in target.parents for old in targets)):
            raise ValueError('explicit, separate sign-in source and /signin target required')
        sources.append(source)
        targets.append(target)
    instructions = ('Use G6 through the mission MCP server. Training/dev inputs only; do not read held-out cases, '
        'owner state or keys. Propose an inspectable change with reproductions; no merge, release, tag or publication. '
        + task)
    command = ('claude', '-p', instructions) if family == 'claude' else ('codex', 'exec', instructions)
    if purpose not in ('engineering', 'issuance_precheck', 'issuance_check'):
        raise ValueError('unknown mission purpose')
    rehearsal = purpose != 'engineering'
    if rehearsal:
        if signin_mounts:
            raise ValueError('issuance rehearsal never mounts sign-in files')
        command = ('python', 'tools/issue_certificate.py', '--' + purpose.removeprefix('issuance_'))
    return Mission(mission_id, family, image, command,
                   (Mount(roots[0].as_posix(), '/workspace', rehearsal), Mount(roots[1].as_posix(), '/mission-home')),
                   task, signin_mounts=tuple(signin_mounts), purpose=purpose,
                   mcp_servers=() if rehearsal else (('g6', ('g6-mcp',)),))


class ContainerMissions:
    """The injected container port enforces mounts/network/quotas and MCP wiring.

    It must attest to a disposable clean home, image provenance and host-side
    exclusion of held-out data and owner records before launching a CLI. No
    Docker socket, command executor or credentials are discovered here.
    """
    def __init__(self, container, *, stop, config=None):
        self.container, self.stop, self.config = container, stop, config

    def run(self, mission):
        if self.stop():
            raise ValueError('stopped before container mission')
        if len(mission.mounts) != 2:
            raise ValueError('mission has unexpected mounts')
        checked = build_mission(family=mission.family, mission_id=mission.mission_id,
            worktree=mission.mounts[0].source, home=mission.mounts[1].source, image=mission.image, task=mission.task,
            signin_mounts=mission.signin_mounts, config=self.config, purpose=mission.purpose)
        if mission != checked:
            raise ValueError('mission differs from the fixed confinement policy')
        return self.container(checked, stop=self.stop)

    def rehearse_issuance(self, mission):
        """Only fixed offline check commands in the same disposable Docker port.

        No signer, held-source mount, source-file option or issuance command exists.
        The container port must enforce Mission's confinement, as for engineer work.
        """
        # Validate the original before deriving either rehearsal plan.
        original = build_mission(family=mission.family, mission_id=mission.mission_id,
            worktree=mission.mounts[0].source, home=mission.mounts[1].source, image=mission.image,
            task=mission.task, signin_mounts=mission.signin_mounts, config=self.config, purpose=mission.purpose)
        if mission != original:
            raise ValueError('mission differs from the fixed confinement policy')
        plans = tuple(build_mission(family=mission.family, mission_id=mission.mission_id,
            worktree=mission.mounts[0].source, home=mission.mounts[1].source, image=mission.image,
            task='Owner issuance rehearsal only', purpose=purpose)
            for purpose in ('issuance_precheck', 'issuance_check'))
        return tuple(self.run(plan) for plan in plans)
