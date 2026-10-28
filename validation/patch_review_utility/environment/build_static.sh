#!/bin/sh
# SPDX-License-Identifier: Apache-2.0
# Static git and bubblewrap for G6's toolchain (toolchain.py build), in the pinned Alpine image, from the sources
# toolchain.py verified: /sources (read only) holds them; /opt/g6 is the new toolchain volume. Linked statically
# against musl, both run whatever C library a task image has. Alpine's build packages come from its mirror; their
# exact versions are recorded in /opt/g6/BUILD-PACKAGES.txt, which the toolchain's tree digest covers.
#   sh build_static.sh <git tarball> <bubblewrap tarball>
set -eu
git_tar="/sources/$1"
bwrap_tar="/sources/$2"
apk add --no-cache build-base linux-headers zlib-dev zlib-static libcap-dev libcap-static meson ninja pkgconf \
    xz >/dev/null
apk info -v | sort > /opt/g6/BUILD-PACKAGES.txt
work=$(mktemp -d)

cd "$work"
tar -xJf "$git_tar"
cd git-*/
# Local commands only: no transport, no translations, no Perl, Python or Tcl helpers, and none of the optional Rust
# parts (git's own NO_RUST: Rust stays optional before Git 3.0). RUNTIME_PREFIX finds git's own folders from where the
# binary is, so the toolchain can be mounted anywhere; no dashed built-ins, no hard links.
make -j"$(nproc)" prefix=/opt/g6 RUNTIME_PREFIX=YesPlease NO_CURL=YesPlease NO_EXPAT=YesPlease NO_GETTEXT=YesPlease \
    NO_TCLTK=YesPlease NO_PERL=YesPlease NO_PYTHON=YesPlease NO_OPENSSL=YesPlease NO_REGEX=YesPlease NO_RUST=YesPlease \
    NO_INSTALL_HARDLINKS=YesPlease SKIP_DASHED_BUILT_INS=YesPlease CFLAGS=-O2 LDFLAGS=-static install >/dev/null

cd "$work"
tar -xJf "$bwrap_tar"
cd bubblewrap-*/
meson setup _build --prefix=/opt/g6 --buildtype=release --prefer-static -Ddefault_library=static \
    -Dc_link_args=-static -Dman=disabled -Dselinux=disabled -Dtests=false -Dbash_completion=disabled \
    -Dzsh_completion=disabled >/dev/null
meson compile -C _build >/dev/null
meson install -C _build >/dev/null

# Static means no program interpreter and no shared library needed, read from the ELF headers (musl's ldd accepts a
# static position-independent program, so it cannot tell).
for program in /opt/g6/bin/git /opt/g6/bin/bwrap; do
    if readelf -l "$program" | grep -q "program interpreter" || readelf -d "$program" | grep -q "(NEEDED)"; then
        echo "$program is not static" >&2
        exit 1
    fi
done
rm -rf "$work"
