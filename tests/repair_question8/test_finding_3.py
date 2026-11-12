"""Optional exception diagnostics must not invoke an exception metaclass's hooks."""
import subprocess
import sys

from g6.harness import behaviour_runner as runner


def test_exception_recorder_never_calls_metaclass_metadata_hooks(tmp_path):
    root = tmp_path / 'copy'
    root.mkdir()
    (root / 'calc.py').write_text(
        'class Meta(type):\n'
        '    def __str__(cls):\n'
        '        print("G6_INVOKED_METACLASS_DISPLAY_HOOK", flush=True)\n'
        '        return "calc"\n'
        '    def __getattribute__(cls, name):\n'
        '        if name in ("__module__", "__qualname__"):\n'
        '            print("G6_INVOKED_METACLASS_HOOK:" + name, flush=True)\n'
        '        if name == "__module__":\n'
        '            return str(cls)\n'
        '        return type.__getattribute__(cls, name)\n'
        'class DomainError(ValueError, metaclass=Meta):\n'
        '    pass\n'
        'def add():\n'
        '    raise DomainError()\n', encoding='utf-8')
    channel = tmp_path / 'observation.bin'
    channel.write_bytes(b'')
    boot = tmp_path / 'bootstrap.py'
    boot.write_text(runner.BOOTSTRAP, encoding='utf-8')
    data = {'function': ['module', 'calc', 'add', 'calc.py'], 'cases': [
        {'args': [], 'expect': 'raises', 'error': ['builtin', '', 'ValueError']}]}
    child = subprocess.run([sys.executable, '-I', '-S', '-B', str(boot), str(root), str(channel),
        runner._encoded(runner.request(data))], capture_output=True, text=True, check=True, timeout=10)
    got = runner.finish(child.returncode, channel.read_bytes(), data)
    assert got['result'] == 'passed', got
    assert 'G6_INVOKED_METACLASS_DISPLAY_HOOK' not in child.stdout, (
        'G6 invoked a metaclass display hook through optional metadata lookup', child.stdout)
