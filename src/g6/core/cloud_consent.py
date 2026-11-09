# SPDX-License-Identifier: Apache-2.0
"""Source/provider admission shared by model callers and MCP egress (design 16).

The host is trusted. The ledger is not an authentication service against code
running as the same OS user. An OS lock serialises reading, disclosure and sending
with revocation; an admitted in-flight send finishes before revocation returns.
"""
from __future__ import annotations

import contextvars
import hashlib
import os
import re
import threading
import uuid
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

from g6.core.ledger import ChainLedger, digest
from g6.core.llm import LLMError
from g6.core.storage import contained, g6_home, plain_file

REQUIRED = 'G6_E_CLOUD_CONSENT_REQUIRED'
STATE_UNAVAILABLE = 'G6_E_CLOUD_CONSENT_STATE'
WITHHELD = ('Results were withheld from a source not shared with this provider. '
            'Use g6_cloud_grant with the source or repository and provider; '
            'only acceptance in the client dialog grants sharing.')
_ID = re.compile(r'[A-Za-z0-9_.:-]{1,200}\Z')
_HASH = re.compile(r'[0-9a-f]{64}\Z')
_LOCK = threading.RLock()
_SOURCES: contextvars.ContextVar[tuple[Source, ...]] = contextvars.ContextVar('g6_request_sources', default=())
_CAPTURE: contextvars.ContextVar[Capture | None] = contextvars.ContextVar('g6_source_capture', default=None)
_POLICY: contextvars.ContextVar[bool] = contextvars.ContextVar('g6_consent_io', default=False)
_REFUSAL_TELL: contextvars.ContextVar[tuple] = contextvars.ContextVar('g6_consent_refusal_observers', default=())


@contextmanager
def telling_refusals(tell):
    """Let a supervising loop latch a withheld send before a wrapper handles its error.

    This observes a refusal; it cannot authorise sending or alter its cause.
    A loop can end its tick here, as it does for a provider classifier refusal.
    """
    token = _REFUSAL_TELL.set((*_REFUSAL_TELL.get(), tell))
    try:
        yield
    finally:
        _REFUSAL_TELL.reset(token)


def _refusal(error):
    for tell in _REFUSAL_TELL.get():
        tell(error)
    return error


def sha256(data: str | bytes) -> str:
    return hashlib.sha256(data.encode('utf-8') if isinstance(data, str) else data).hexdigest()


def repository_source(repo: str | Path) -> str:
    given = Path(repo)
    root = given.anchor if given.is_absolute() else Path.cwd()
    resolved = contained(root, repo, refuse_links=True)
    return 'repository:' + sha256(os.path.normcase(str(resolved)))


def input_source(text: str) -> str:
    """An exact caller-supplied input, never silently attributed to a repository."""
    return 'input:' + sha256(text)


def source_id(source: str) -> str:
    """Opaque source ids stay ids; a path becomes its canonical repository/file scope."""
    if not isinstance(source, str) or not source.strip():
        raise ValueError('a source or repository must be named')
    if _ID.fullmatch(source) and source not in ('.', '..'):
        return source
    return repository_source(source)


@dataclass(frozen=True)
class Source:
    source_id: str
    item_id: str
    content_sha256: str
    recorded: bool = True

    def __post_init__(self) -> None:
        if not isinstance(self.recorded, bool):
            raise ValueError('source provenance must state whether it was recorded')
        if not _ID.fullmatch(self.source_id) or not _HASH.fullmatch(self.item_id):
            raise ValueError('sources need an opaque scope id and a hashed file/passage id')
        if not _HASH.fullmatch(self.content_sha256):
            raise ValueError('sources need a content digest')

    @classmethod
    def text(cls, source: str, item: str, text: str | bytes) -> Source:
        return cls(source_id(source), sha256(item), sha256(text))


UNKNOWN = Source('unrecorded', sha256('unrecorded'), sha256('unrecorded'), False)


def withheld_message(provider: str, sources: Sequence[Source] = ()) -> str:
    """Only scope identifiers and recipient identity; never file names or source bytes."""
    recipient = provider if _ID.fullmatch(provider) else 'cloud:unknown'
    scopes = ', '.join(dict.fromkeys(s.source_id for s in sources)) or 'unrecorded'
    return f'{WITHHELD} Withheld source identifiers: {scopes}. Provider: {recipient}.'


@dataclass
class Capture:
    sources: list[Source] = field(default_factory=list)
    client: tuple[str, object] | None = None
    supplied: set[str] = field(default_factory=set)
    outputs: dict[int, tuple[object, str, tuple[Source, ...], dict]] = field(default_factory=dict)
    job: dict[str, str] | None = None
    expected_tool: object | None = None
    returned: tuple[object, str, tuple[Source, ...] | None, bool, dict] | None = None
    review_unsent: bool = False
    refused_provider: str = ''


def record_sources(sources: Sequence[Source]) -> None:
    capture = _CAPTURE.get()
    if capture is not None:
        capture.sources.extend(s for s in sources if s not in capture.sources)


def current_sources() -> tuple[Source, ...]:
    # Observing a read does not establish that a request contains no other data.
    # Only an explicit request construction can supply complete provenance.
    return _SOURCES.get()


def current_capture() -> Capture | None:
    return _CAPTURE.get()


def bind_output(value, sources: Sequence[Source], *, metadata: dict | None = None):
    """Bind a reviewed producer's complete source set to this value and its exact JSON bytes.

    This is a host-code contract, never a tool argument. An empty source set is
    an explicit declaration that this value has no personal data (e.g. job status).
    Unannotated outputs remain UNKNOWN even if some reads were observed.
    """
    capture = _CAPTURE.get()
    if capture is not None:
        refs = tuple(sources)
        old = capture.outputs.get(id(value))
        if old is not None and old[0] is value:
            refs = tuple(dict.fromkeys((*old[2], *refs)))
        content_hash = digest(value)
        if metadata is None:
            metadata = old[3] if old is not None and old[0] is value and old[1] == content_hash else {}
        capture.outputs[id(value)] = (value, content_hash, refs, dict(metadata))
        record_sources(sources)
    return value


def output_sources(value) -> tuple[Source, ...] | None:
    capture = _CAPTURE.get()
    try:
        stored = capture.outputs.get(id(value)) if capture is not None else None
        return stored[2] if stored is not None and stored[0] is value and stored[1] == digest(value) else None
    except ValueError:
        return None


def output_metadata(value) -> dict:
    """Host-declared control fields for exactly this result, never a grant."""
    capture = _CAPTURE.get()
    try:
        stored = capture.outputs.get(id(value)) if capture is not None else None
        return dict(stored[3]) if stored is not None and stored[0] is value and stored[1] == digest(value) else {}
    except ValueError:
        return {}


@contextmanager
def request_sources(sources: Sequence[Source]) -> Iterator[None]:
    refs = tuple(dict.fromkeys((*_SOURCES.get(), *sources)))
    token = _SOURCES.set(refs)
    record_sources(sources)
    try:
        yield
    finally:
        _SOURCES.reset(token)


@contextmanager
def collect_sources() -> Iterator[Capture]:
    parent = _CAPTURE.get()
    capture = Capture()
    token = _CAPTURE.set(capture)
    try:
        yield capture
    finally:
        _CAPTURE.reset(token)
        if parent is not None and capture.refused_provider:
            parent.refused_provider = capture.refused_provider


def ledger_path() -> Path:
    return contained(g6_home(), 'cloud_consent/events.jsonl', refuse_links=True)


def _validate(kind, ident, body, seen):
    fields = {'grant': {'source', 'provider', 'time', 'attribution_sha256'},
              'revoke': {'source', 'provider', 'time'},
              'disclosure': {'provider', 'time', 'status', 'payload_sha256', 'sources'}}
    try:
        if kind not in fields or set(body) != fields[kind]:
            return ['unknown consent record or fields']
        if not isinstance(body['provider'], str) or not _ID.fullmatch(body['provider']):
            return ['invalid provider id']
        if datetime.fromisoformat(body['time']).tzinfo is None:
            return ['time needs a timezone']
        if kind in ('grant', 'revoke'):
            if not _ID.fullmatch(body['source']):
                return ['invalid source id']
            if kind == 'grant' and not _HASH.fullmatch(body['attribution_sha256']):
                return ['invalid attribution digest']
        else:
            if body['status'] != 'attempted' or not _HASH.fullmatch(body['payload_sha256']) or not body['sources']:
                return ['invalid disclosure']
            for row in body['sources']:
                if set(row) != {'source', 'item_id', 'content_sha256', 'basis'} or not _ID.fullmatch(row['source']):
                    return ['invalid disclosure source']
                if not _HASH.fullmatch(row['item_id']) or not _HASH.fullmatch(row['content_sha256']):
                    return ['invalid disclosure digests']
                if row['basis'] not in ('client-supplied', 'grant'):
                    return ['invalid disclosure basis']
    except (KeyError, TypeError, ValueError):
        return ['malformed consent record']
    return []


@contextmanager
def _locked() -> Iterator[ChainLedger]:
    token = _POLICY.set(True)
    descriptor = None
    locked = False
    yielded = False
    try:
        with _LOCK:
            folder = contained(g6_home(), 'cloud_consent', refuse_links=True)
            folder.mkdir(parents=True, exist_ok=True)
            mutex = contained(folder, 'mutex', refuse_links=True)
            descriptor = os.open(mutex, os.O_CREAT | os.O_RDWR, 0o600)
            opened = os.fstat(descriptor)
            checked = plain_file(mutex)
            if (opened.st_dev, opened.st_ino) != (checked.st_dev, checked.st_ino):
                raise OSError('consent mutex changed')
            if os.name == 'nt':
                import msvcrt
                # LK_LOCK retries for up to ten seconds, then refuses rather than bypassing the lock.
                msvcrt.locking(descriptor, msvcrt.LK_LOCK, 1)
            else:
                import fcntl
                fcntl.flock(descriptor, fcntl.LOCK_EX)
            locked = True
            path = contained(folder, 'events.jsonl', refuse_links=True)
            head = contained(folder, 'head', refuse_links=True)
            if head.exists():
                plain_file(head)
                expected = head.read_text(encoding='ascii')
                if expected and not _HASH.fullmatch(expected):
                    raise OSError('consent checkpoint is malformed')
            elif path.exists():
                raise OSError('consent checkpoint is missing')
            else:
                expected = ''
            if path.exists():
                plain_file(path)
            ledger = ChainLedger(path, expect_head=expected, validate=_validate)
            yielded = True
            yield ledger
    except (OSError, ValueError) as exc:
        if yielded:
            raise
        raise _refusal(LLMError(f'{STATE_UNAVAILABLE}: sharing state could not be read or written; nothing was sent')) from exc
    finally:
        if descriptor is not None:
            if locked:
                os.lseek(descriptor, 0, os.SEEK_SET)
                if os.name == 'nt':
                    import msvcrt
                    msvcrt.locking(descriptor, msvcrt.LK_UNLCK, 1)
                else:
                    import fcntl
                    fcntl.flock(descriptor, fcntl.LOCK_UN)
            os.close(descriptor)
        _POLICY.reset(token)


def _append(ledger: ChainLedger, kind: str, body: dict) -> None:
    try:
        ledger.append(kind, uuid.uuid4().hex, body)
        head = ledger.path.with_name('head')
        fresh = contained(head.parent, 'head.new', refuse_links=True)
        with fresh.open('w', encoding='ascii', newline='\n') as stream:
            stream.write(ledger.head())
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(fresh, head)
    except (OSError, ValueError) as exc:
        raise _refusal(LLMError(f'{STATE_UNAVAILABLE}: sharing state could not be written; nothing was sent')) from exc


def _time() -> str:
    return datetime.now(UTC).isoformat()


def _decisions(ledger: ChainLedger) -> dict:
    latest = {}
    for row in ledger.entries():
        body = row['body']
        if row['kind'] in ('grant', 'revoke'):
            latest[body['source'], body['provider']] = row
    return latest


def _decision(ledger: ChainLedger, source: str, provider: str) -> tuple[bool, str]:
    row = _decisions(ledger).get((source, provider))
    return (row['kind'] == 'grant', row['entry_sha256']) if row is not None else (False, '')


def preview(source: str, provider: str) -> tuple[bool, str]:
    with _locked() as ledger:
        return _decision(ledger, source_id(source), provider)


def is_granted(source: str, provider: str) -> bool:
    try:
        return preview(source, provider)[0]
    except LLMError:
        return False


def _grant(source: str, provider: str, revision: str, attribution: str) -> str:
    """Only the elicitation adapter calls this; there is no corresponding MCP answer parameter."""
    with _locked() as ledger:
        if _decision(ledger, source_id(source), provider)[1] != revision:
            return 'stale'
        _append(ledger, 'grant', {'source': source_id(source), 'provider': provider, 'time': _time(),
                                  'attribution_sha256': sha256(attribution)})
    return 'granted'


def revoke(source: str, provider: str) -> dict:
    with _locked() as ledger:
        _append(ledger, 'revoke', {'source': source_id(source), 'provider': provider, 'time': _time()})
    return {'status': 'revoked', 'source': source_id(source), 'provider': provider}


def grants() -> list[dict]:
    with _locked() as ledger:
        return [row['body'] for row in _decisions(ledger).values() if row['kind'] == 'grant']


def disclosures() -> list[dict]:
    with _locked() as ledger:
        return [row['body'] for row in ledger.entries() if row['kind'] == 'disclosure']


@contextmanager
def admission(provider: str, sources: Sequence[Source], payload_sha256: str, *, local: bool = False,
              supplied: Sequence[str] = (), disclose: bool = True) -> Iterator[None]:
    """One check, at send time. Holding the lock through send gives revocation a single order.

    Disclosure is an attempt, durably logged first: a crash/transport error cannot say
    whether the recipient received it. Local computation needs no sharing state.
    """
    refs = tuple(sources) or (UNKNOWN,)
    record_sources(refs)
    if local:
        yield
        return
    with _locked() as ledger:
        decisions = _decisions(ledger)
        def allowed(source):
            row = decisions.get((source.source_id, provider))
            granted, revision = (row['kind'] == 'grant', row['entry_sha256']) if row is not None else (False, '')
            # An agent naming a working tree cannot undo the person's revocation.
            return source.recorded and (granted or source.source_id in supplied and not revision)
        if provider == 'cloud:unknown' or not all(allowed(source) for source in refs):
            capture = _CAPTURE.get()
            if capture is not None:
                capture.refused_provider = provider
            raise _refusal(LLMError(f'{REQUIRED}: {withheld_message(provider, refs)}'))
        if disclose:
            _append(ledger, 'disclosure', {'provider': provider, 'time': _time(), 'status': 'attempted',
                'payload_sha256': payload_sha256,
                'sources': [{'source': s.source_id, 'item_id': s.item_id, 'content_sha256': s.content_sha256,
                             'basis': 'client-supplied' if s.source_id in supplied and
                                 (s.source_id, provider) not in decisions else 'grant'} for s in refs]})
        yield
