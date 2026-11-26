"""Repair 3: a text proof must survive bindings, unpacking and mutation."""
import pytest

from tests.harness.test_completion_writer_allowlist import unguarded_calls


@pytest.mark.parametrize('body', [
    'def f(str):\n    str(x).replace(a, b)\n',
    'def f(fs):\n    fs.replace(a, b)\n',
    'value.decode().replace(a, b)\n',
    'value.read_text().replace(a, b)\n',
    "[*values, 'x'][0].replace(a, b)\n",
    "first, text, *rest = *values, 'x', 'y'\ntext.replace(a, b)\n",
    "text = 'x'\nme.text = value\ntext.replace(a, b)\n",
    "text = 'x'\nclass C:\n    text = value\ntext.replace(a, b)\n",
    "text = 'x'\nif flag:\n    text = 'y'\ntext.replace(a, b)\n",
    "text = 'x' if flag else 'y'\ntext.replace(a, b)\n",
    "text = 'x' + 'y'\ntext.replace(a, b)\n",
    "text = 'x' or 'y'\ntext.replace(a, b)\n",
    "parts = (['x'],)\nparts[0].clear()\nparts[0].append(value)\nparts[0][0].replace(a, b)\n",
    "parts = ({'k': 'x'},)\nparts[0].update(k=value)\nparts[0]['k'].replace(a, b)\n",
    "parts = ['x']\nparts.insert(0, value)\nparts[0].replace(a, b)\n",
    "parts = ['x']\nparts[0] = value\nparts[0].replace(a, b)\n",
    "parts = ['x']\nparts[:] = values\nparts[0].replace(a, b)\n",
    "parts = {'k': 'x'}\nalias = parts\nalias.update(k=value)\nparts['k'].replace(a, b)\n",
    "parts = {'k': 'x'}\nchange(parts)\nparts['k'].replace(a, b)\n",
    "text = text.strip()\ntext.replace(a, b)\n",
    "text = 'x'\ntext += 'y'\ntext.replace(a, b)\n",
    "text = parts[0]\ntext.replace(a, b)\n",
    "text = data.strip()\ntext.replace(a, b)\n",
    "(text := 'x') if flag else None\ntext.replace(a, b)\n",
    "flag and (text := 'x')\ntext.replace(a, b)\n",
    "[(text := 'x') for item in values]\ntext.replace(a, b)\n",
])
def test_unproven_receivers_are_refused(body):
    assert any(name.endswith('.replace') for _, name in unguarded_calls(body))


@pytest.mark.parametrize('body', [
    "text = str(value)\ntext = text.strip()\ntext = text.replace('a', 'b')\ntext.replace('c', 'd')\n",
    "text.strip().replace('a', 'b').replace('c', 'd')\n",
    "data.strip().replace('a', 'b')\n",
    "parts[0].strip().replace('a', 'b').replace('c', 'd')\n",
    "{'k': 'a'}['k'].strip().replace('a', 'b')\n",
    "parts = {'k': 'a'}\nparts['k'].strip().replace('a', 'b')\n",
    "text = '%s' % value\ntext.replace('a', 'b')\n",
    "text = '-' * 3\ntext.replace('-', '=')\n",
])
def test_proven_receivers_are_admitted(body):
    assert not unguarded_calls(body)
