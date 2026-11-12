# SPDX-License-Identifier: Apache-2.0
"""Test-only accepting client dialog around the unchanged stdio smoke exercise."""
import importlib.util
from pathlib import Path
import sys

from mcp.types import Implementation

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from conftest import accepting_cloud_elicitation  # noqa: E402

spec = importlib.util.spec_from_file_location('mcp_smoke', sys.argv[1])
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
original = module.run_over_stdio
module.run_over_stdio = lambda command: original(command,
    client_info=Implementation(name='codex', version='test'), elicitation_handler=accepting_cloud_elicitation,
    sharing_sources=('component_catalogue', 'component_data'))
sys.argv = sys.argv[1:]
raise SystemExit(module.main())
