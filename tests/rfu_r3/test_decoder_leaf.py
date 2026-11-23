"""Pure consumers share the decoder implementation without loading another component."""
import ast
import sys

from tests.rfu_r3.test_decoder_copies import COPIES, HEADER, ROOT, SOURCE, _sync


def test_decoder_is_one_standard_library_leaf():
    assert set(_sync().COPIES) == COPIES
    source = (ROOT / SOURCE).read_bytes()
    for name in sorted(COPIES | {SOURCE}):
        body = (ROOT / name).read_bytes()
        assert body == (source if name == SOURCE else HEADER + source), name
        for node in ast.walk(ast.parse(body)):
            names = ([a.name for a in node.names] if isinstance(node, ast.Import)
                     else [node.module or ''] if isinstance(node, ast.ImportFrom) else [])
            assert not isinstance(node, ast.ImportFrom) or node.level == 0
            assert all(n.split('.')[0] in sys.stdlib_module_names or n == '__future__' for n in names), names


def test_purity_exception_does_not_admit_a_decoder_sibling(tmp_path):
    from tests.research.mission_bridge.test_mission_bridge_ir import _outside_the_standard_library

    path = tmp_path / 'sibling.py'
    path.write_text('from g6._decoder_limits_extra import decode\n', encoding='utf-8')
    assert _outside_the_standard_library([path]) == {'g6'}
