#!/bin/sh
# SPDX-License-Identifier: Apache-2.0
# Bubblewrap, G6's operating-system boundary on Linux (g6.core.confine), on a CI job: installed; given the AppArmor
# profile it needs where the kernel restricts unprivileged user namespaces (kernel.apparmor_restrict_unprivileged_userns
# is 1 on Ubuntu 23.10 and later, GitHub's runners among them); and expected, so the bwrap tests fail rather than skip
# when it cannot confine a run (G6_EXPECT_BACKEND, read by the bwrap_backend fixture in tests/conftest.py).
set -eu
sudo apt-get update -qq
sudo apt-get install -y -qq bubblewrap
if [ "$(cat /proc/sys/kernel/apparmor_restrict_unprivileged_userns 2>/dev/null || echo 0)" = "1" ]; then
  bwrap="$(command -v bwrap)"
  printf 'abi <abi/4.0>,\ninclude <tunables/global>\nprofile bwrap %s flags=(unconfined) {\n  userns,\n  include if exists <local/bwrap>\n}\n' \
    "$bwrap" | sudo tee /etc/apparmor.d/bwrap >/dev/null
  sudo apparmor_parser -r /etc/apparmor.d/bwrap
fi
bwrap --version
echo "G6_EXPECT_BACKEND=bubblewrap" >> "$GITHUB_ENV"
