from pathlib import Path
from uuid import uuid4

import pytest


@pytest.fixture
def tmp_path():
    # pytest's chmod-created numbered directory is unreadable in this sandbox.
    path = Path(__file__).resolve().parents[2] / '.review' / 'tmp' / uuid4().hex
    path.mkdir(parents=True)
    return path
