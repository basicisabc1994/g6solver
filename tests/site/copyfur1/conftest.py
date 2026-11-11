import importlib.util
import os
import sys
from pathlib import Path
import pytest

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / 'web'))
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'g6site.settings')
import django  # noqa: E402 - initialize Django only after the review's environment setup
django.setup()

@pytest.fixture(scope='session')
def honesty():
    spec = importlib.util.spec_from_file_location('review_honesty', ROOT / 'tests/site/test_honesty.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module
