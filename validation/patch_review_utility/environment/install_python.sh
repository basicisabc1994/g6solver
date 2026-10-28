#!/bin/sh
# SPDX-License-Identifier: Apache-2.0
# Python 3.12 and G6 for G6's toolchain (toolchain.py build), in the pinned glibc image, offline: /sources holds the
# verified Python build, /wheels the verified wheels and G6's wheel built at the frozen commit; /opt/g6 is the toolchain
# volume. The bytecode is written with unchecked hashes, so it does not depend on the time of the build.
#   sh install_python.sh <python build tarball>
set -eu
tar -xzf "/sources/$1" -C /opt/g6
/opt/g6/python/bin/python3 -I -m pip install --no-index --no-deps --no-compile --disable-pip-version-check \
    --no-warn-script-location /wheels/*.whl >/dev/null
/opt/g6/python/bin/python3 -I -m pip check
/opt/g6/python/bin/python3 -I -m compileall -q --invalidation-mode unchecked-hash \
    /opt/g6/python/lib/python3.12/site-packages >/dev/null
/opt/g6/python/bin/python3 -I -c "import g6.harness.patch_review, g6.workforce.repo_tests"
