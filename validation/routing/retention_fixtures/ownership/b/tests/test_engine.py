# SPDX-License-Identifier: Apache-2.0
import engine

def test_ordinary():
    assert engine.clone_settings({'name': 'a'}) == {'name': 'a'}
