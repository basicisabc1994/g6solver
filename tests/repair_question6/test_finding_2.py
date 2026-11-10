"""Exercise the refusal branch omitted by the repair's ordinary-renderer differential."""
import ast
import subprocess

from g6.harness import patch_review


def test_unconfirmed_validation_reply_keeps_68ca0eb_text():
    # Read the claimed reference from this checkout's object database; execute only
    # its review entry point with the same helpers. Invalid input stops at admission.
    old = subprocess.run(['git', 'show', '68ca0eb:src/g6/harness/patch_review.py'],
                         check=True, capture_output=True, timeout=20).stdout.decode('utf-8')
    function = next(n for n in ast.parse(old).body
                    if isinstance(n, ast.FunctionDef) and n.name == 'review_patch')
    scope = dict(vars(patch_review))
    exec(compile(ast.Module(body=[function], type_ignores=[]), '<68ca0eb review_patch>', 'exec'), scope)
    args = dict(repo='unused', task='ordinary task', requirements=[],
                intent={'unknown\nfield': 'ordinary caller text'})
    expected = scope['review_patch'](**args)
    actual = patch_review.review_patch(**args)
    assert expected['ok'] is False and actual['ok'] is False
    assert actual['error'] == expected['error'], (
        'an ordinary refusal with no confirmed case changed its reply text', actual['error'], expected['error'])
