"""Outside JSON uses the shared guarded decoder; bound and pinned inputs stay explicit."""
import ast
from pathlib import Path

from tests.s16r3.certificate_inputs import certificate_inputs
from tests.s16r3.test_home_json_census import PINNED_CHILD


def test_native_json_decoding_is_confined_to_the_guarded_decoder():
    root = Path(__file__).resolve().parents[2] / 'src/g6'
    exempt = certificate_inputs() | PINNED_CHILD | {'core/decoder_limits.py'}
    missing = []
    for path in sorted(root.rglob('*.py')):
        rel = path.relative_to(root).as_posix()
        if rel in exempt or 'vendor' in path.parts:
            continue
        tree = ast.parse(path.read_text(encoding='utf-8'))
        modules, functions = {'json'}, set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                modules.update(a.asname or a.name for a in node.names if a.name == 'json')
            elif isinstance(node, ast.ImportFrom) and node.module == 'json':
                functions.update(a.asname or a.name for a in node.names if a.name in {'loads', 'load'})
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and (ast.unparse(node.func) in functions or
                any(ast.unparse(node.func) == f'{m}.{f}' for m in modules
                    for f in ('load', 'loads', 'JSONDecoder'))):
                missing.append(f'{rel}:{node.lineno}')
    assert not missing, '\n'.join(missing)
