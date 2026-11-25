"""Keep synthetic fixtures on disk without pytest's inaccessible Windows 0700 ACL."""
import uuid
from pathlib import Path

import pytest


@pytest.fixture
def tmp_path():
    path = Path(__file__).parent / "scratch" / uuid.uuid4().hex[:8]
    path.mkdir(parents=True)
    return path.resolve()
