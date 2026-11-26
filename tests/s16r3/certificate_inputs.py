"""The source files a component certificate binds (paths under ``src/g6``): what an exemption list may name as bound.

A certificate binds its imported code, its harness files and every Python file of its input bases (the component's
own package, and a shared sibling's private ``_`` folders), as ``component_certificate.input_bases`` reads them.
"""
import json
from functools import lru_cache
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SOURCE = ROOT / 'src' / 'g6'


@lru_cache(maxsize=1)
def certificate_inputs() -> frozenset[str]:
    from g6.core import component_certificate as cert, vetting

    named = set()
    for path in SOURCE.glob('*/*/certificate/certificate.json'):
        body = json.loads(path.read_text(encoding='utf-8'))
        named.update(name.removeprefix('g6/') for name in body['imported_code'])
        named.update(name.removeprefix('g6/') for name in body['harness_sha256'])
        declaration = vetting.declared(body['component'])
        for base, shared in cert.input_bases(ROOT / 'src', path.parent.parent, declaration):
            for source in base.rglob('*.py'):
                rel = source.relative_to(base)
                if (not shared or rel.parts[0].startswith('_')) and '__pycache__' not in rel.parts:
                    named.add(source.relative_to(SOURCE).as_posix())
    assert named, 'no certificate found'
    return frozenset(named)
