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


def build_mission(*, family: Literal['claude', 'codex'], mission_id: str, worktree: str,
                  home: str, image: str, task: str) -> Mission:
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
    instructions = ('Use G6 through the mission MCP server. Training/dev inputs only; do not read held-out cases, '
        'owner state or keys. Propose an inspectable change with reproductions; no merge, release, tag or publication. '
        + task)
    command = ('claude', '-p', instructions) if family == 'claude' else ('codex', 'exec', instructions)
    return Mission(mission_id, family, image, command, (Mount(roots[0].as_posix(), '/workspace'),
                                                      Mount(roots[1].as_posix(), '/mission-home')), task)


class ContainerMissions:
    """The injected container port enforces mounts/network/quotas and MCP wiring.

    It must attest to a disposable clean home, image provenance and host-side
    exclusion of held-out data and owner records before launching a CLI. No
    Docker socket, command executor or credentials are discovered here.
    """
    def __init__(self, container, *, stop):
        self.container, self.stop = container, stop

    def run(self, mission):
        if self.stop():
            raise ValueError('stopped before container mission')
        if len(mission.mounts) != 2:
            raise ValueError('mission has unexpected mounts')
        checked = build_mission(family=mission.family, mission_id=mission.mission_id,
            worktree=mission.mounts[0].source, home=mission.mounts[1].source, image=mission.image, task=mission.task)
        if mission != checked:
            raise ValueError('mission differs from the fixed confinement policy')
        return self.container(checked, stop=self.stop)
