# SPDX-License-Identifier: Apache-2.0
"""Owned startup drops dependency deprecations before imports, preserving G6 warnings."""
import os
from pathlib import Path
import subprocess
import sys
import textwrap
import warnings

import pytest


class DependencyDeprecation(DeprecationWarning):
    pass


class DependencyPendingDeprecation(PendingDeprecationWarning):
    pass


@pytest.mark.parametrize('category', [DeprecationWarning, PendingDeprecationWarning,
                                     DependencyDeprecation, DependencyPendingDeprecation])
def test_owned_warning_policy_keeps_g6_origins_and_other_categories(category):
    from g6._warnings import install_process_warning_policy

    with warnings.catch_warnings(record=True) as shown:
        warnings.simplefilter('always')
        install_process_warning_policy()
        install_process_warning_policy()
        # Dependencies such as Authlib prepend their own "always" filter on import.
        warnings.simplefilter('always', category)
        for module in ('dependency', 'dependency.bootstrap', 'g6_vendor', 'g6', 'g6.bootstrap'):
            filename = (Path(__file__).resolve().parents[2] / 'src/g6/bootstrap.py'
                        if module in ('g6', 'g6.bootstrap') else Path('dependency/bootstrap.py'))
            warnings.warn_explicit(module, category, str(filename), 1, module=module)
        warnings.warn_explicit('runtime', RuntimeWarning, 'bootstrap.py', 1, module='dependency')
    assert [str(item.message) for item in shown] == ['g6', 'g6.bootstrap', 'runtime']


def test_owned_warning_policy_preserves_filters_and_hidden_g6_warnings():
    from g6._warnings import install_process_warning_policy

    with warnings.catch_warnings(record=True) as shown:
        warnings.simplefilter('ignore', DeprecationWarning)
        filters = warnings.filters[:]
        install_process_warning_policy()
        origin = str(Path(__file__).resolve().parents[2] / 'src/g6/bootstrap.py')
        warnings.warn_explicit('g6 hidden', DeprecationWarning, origin, 1, module='g6.bootstrap')
        assert warnings.filters == filters
    assert shown == []


STARTUP = textwrap.dedent('''
    import importlib.abc
    import sys
    import warnings

    warnings.simplefilter('always')
    import g6
    from pathlib import Path
    g6_origin = str(Path(g6.__file__).parent / 'bootstrap.py')
    target = {'cli': 'g6.cli.setup', 'mcp': 'fastmcp', 'mcp-core': 'pydantic'}[sys.argv[1]]

    class DependencyDeprecation(DeprecationWarning):
        pass
    class DependencyPendingDeprecation(PendingDeprecationWarning):
        pass

    class Probe(importlib.abc.MetaPathFinder):
        def find_spec(self, fullname, path, target=None):
            if fullname == self.target:
                sys.meta_path.remove(self)
                for category in (DeprecationWarning, PendingDeprecationWarning,
                                 DependencyDeprecation, DependencyPendingDeprecation):
                    warnings.simplefilter('always', category)
                    warnings.warn_explicit('DEPENDENCY_PRIVATE', category,
                                           'dependency.py', 1, module='dependency.bootstrap')
                warnings.warn_explicit('G6_DEPRECATION', DeprecationWarning,
                                       g6_origin, 1, module='g6.bootstrap')
                warnings.warn_explicit('G6_PENDING', PendingDeprecationWarning,
                                       g6_origin, 1, module='g6')
                warnings.warn_explicit('DEPENDENCY_RUNTIME', RuntimeWarning,
                                       'dependency.py', 1, module='dependency.bootstrap')
            return None
    probe = Probe()
    probe.target = target
    sys.meta_path.insert(0, probe)
    if sys.argv[1] == 'cli':
        from g6.cli import entrypoint
        try:
            entrypoint(['--help'])
        except SystemExit as error:
            assert error.code == 0
    else:
        from g6.mcp import main
        main()
    assert probe not in sys.meta_path, 'startup did not import the probed module'
''')


def _env(tmp_path):
    return {**os.environ, 'G6_HOME': str(tmp_path / 'home'),
            'FASTMCP_HOME': str(tmp_path / 'fastmcp'), 'FASTMCP_ENV_FILE': os.devnull,
            'FASTMCP_CHECK_FOR_UPDATES': 'off',
            'G6_CLAUDE': r'C:\nonexistent\g6-no-host.exe', 'G6_CODEX': r'C:\nonexistent\g6-no-host.exe'}


@pytest.mark.parametrize('surface', ['cli', 'mcp', 'mcp-core'])
def test_owned_startup_filters_before_dependency_import(tmp_path, surface):
    result = subprocess.run([sys.executable, '-X', 'utf8', '-c', STARTUP, surface], input='',
                            capture_output=True, text=True, timeout=60, env=_env(tmp_path))
    assert result.returncode == 0, result.stderr
    assert 'DEPENDENCY_PRIVATE' not in result.stdout + result.stderr
    assert 'G6_DEPRECATION' in result.stderr
    assert 'G6_PENDING' in result.stderr
    assert 'DEPENDENCY_RUNTIME' in result.stderr


def test_importing_entries_and_embedding_main_preserve_warning_policy(tmp_path):
    script = textwrap.dedent('''
        import warnings
        warnings.simplefilter('always')
        filters = warnings.filters[:]
        showwarning = warnings.showwarning
        from g6.cli import main
        from g6.mcp import main as server_main
        try:
            main(['--help'])
        except SystemExit as error:
            assert error.code == 0
        assert warnings.filters == filters
        assert warnings.showwarning is showwarning
        warnings.warn_explicit('EMBEDDING_DEPRECATION', DeprecationWarning,
                               'dependency.py', 1, module='dependency.bootstrap')
    ''')
    result = subprocess.run([sys.executable, '-X', 'utf8', '-c', script],
                            capture_output=True, text=True, timeout=30, env=_env(tmp_path))
    assert result.returncode == 0, result.stderr
    assert 'EMBEDDING_DEPRECATION' in result.stderr
