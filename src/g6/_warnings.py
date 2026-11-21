# SPDX-License-Identifier: Apache-2.0
"""Warning policy for processes G6 owns, never installed by a library import."""
from pathlib import Path
import os
import warnings

import g6


def install_process_warning_policy() -> None:
    """Drop dependency deprecations before imports; keep G6's existing treatment."""
    previous = warnings.showwarning
    if getattr(previous, '_g6_process_warning_policy', False):
        return
    def showwarning(message, category, filename, lineno, file=None, line=None):
        # A dependency may prepend its own "always" filter while importing (as
        # Authlib does). Drop at display time, without changing Python's filters.
        if issubclass(category, (DeprecationWarning, PendingDeprecationWarning)):
            origin = Path(os.path.normcase(Path(filename).resolve()))
            # Namespace/package roots may change after policy installation.
            if not any(origin.is_relative_to(Path(os.path.normcase(Path(root).resolve())))
                       for root in g6.__path__):
                return
        previous(message, category, filename, lineno, file=file, line=line)

    showwarning._g6_process_warning_policy = True
    warnings.showwarning = showwarning
