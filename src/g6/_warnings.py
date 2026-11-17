# SPDX-License-Identifier: Apache-2.0
"""Warning policy for processes G6 owns, never installed by a library import."""
from pathlib import Path
import os
import warnings


def install_process_warning_policy() -> None:
    """Drop dependency deprecations before imports; keep G6's existing treatment."""
    previous = warnings.showwarning
    if getattr(previous, '_g6_process_warning_policy', False):
        return
    package = Path(os.path.abspath(__file__)).parent

    def showwarning(message, category, filename, lineno, file=None, line=None):
        # A dependency may prepend its own "always" filter while importing (as
        # Authlib does). Drop at display time, without changing Python's filters.
        if issubclass(category, (DeprecationWarning, PendingDeprecationWarning)) \
                and not Path(os.path.abspath(filename)).is_relative_to(package):
            return
        previous(message, category, filename, lineno, file=file, line=line)

    showwarning._g6_process_warning_policy = True
    warnings.showwarning = showwarning
