"""Review 4: load the shared synthetic fixtures (see wrfu4_helpers.py)."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from wrfu4_helpers import no_real_host_program, tmp_path, world  # noqa: E402,F401
