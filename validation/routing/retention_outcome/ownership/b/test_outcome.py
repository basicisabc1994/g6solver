# SPDX-License-Identifier: Apache-2.0

def test_regression():
    import engine
    original = {'groups': [{'tags': ['old']}]}
    copied = engine.clone_settings(original)
    copied['groups'][0]['tags'].append('new')
    assert original == {'groups': [{'tags': ['old']}]}
    original['groups'].append({'tags': []})
    assert len(copied['groups']) == 1
