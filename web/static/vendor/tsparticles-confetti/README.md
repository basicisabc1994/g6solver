# tsParticles Confetti, vendored

The confetti burst on the site's Get started button (`static/js/site.js`) uses this copy, so the site makes no request
to another server for it. It is fetched only when someone first clicks Get started, never with the page.

- Package: `@tsparticles/confetti 3.8.1` (MIT licence, by Matteo Bruni; documented at https://confetti.js.org/)
- Source: https://registry.npmjs.org/@tsparticles/confetti/-/confetti-3.8.1.tgz
  (tarball SHA-256 `d167962461f7ac94c1dfd9c9fc74682a629e35acab8f440c7978dbae156115a8`)
- Files taken from it, unchanged: `package/tsparticles.confetti.bundle.min.js`, its
  `tsparticles.confetti.bundle.min.js.LICENSE.txt` banner and `package/LICENSE`
- `tsparticles.confetti.bundle.min.js` SHA-256: `3a17c5e722fe3e2a1d2671d2d9ce2610bc5c4932a6a31e8d7a9c984e6c159fe5`
  (143,446 bytes)

`oss/web/.gitattributes` keeps git from rewriting these files' line endings, so a checkout has the bytes the hash names.
`oss/tests/site/test_call_to_action.py` checks the version and the source named here, and that the bundle's hash is the
one above. To update: download one release's tarball, copy the same three files, change the version, the URLs and both
hashes here, and the version in that test.
