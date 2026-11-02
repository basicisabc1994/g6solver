# pyjwt-1216: scoring amendment

Issue jpadilla/pyjwt#1209, "[BUG]: Canonical Base64URL check rejects AWS ALB JWTs". The task is the issue, the one comment made before PR 1216 opened (2026-09-28T15:46:16Z), an idea for an opt-in flag, and the repository at the parent commit.

The issue reports that `_decode_base64url_segment` rejects two things it should not: trailing `=` padding, and unused trailing bits in the last character. Its asks:

1. Keep rejecting characters outside `[A-Za-z0-9_-]`.
2. Drop the encode/decode round-trip, or stop using it to reject unused bits.
3. If `=` padding stays rejected, document an ALB path that still verifies against the original signing input.

`_decode_base64url_segment`, its `(segment, name)` signature and `Invalid crypto padding` are in the parent's code, and the name and message are in the issue, so they are stated. Where padding is handled is not. The issue speaks of tokens, and a fix may strip padding before the helper is called.

## tests/test_api_jws.py, `test_decode_accepts_canonical_base64url_padding`

**Pinned location.** The rows `Zg`, `Zg==` and `Zm8=` called the private helper directly, so padding had to be accepted inside it. A fix that strips the signature's `=` in `_load`, and leaves the helper strict, decodes ALB tokens correctly and failed these rows. Each segment is now decoded as the signature of a token, `e30.e30.` plus the segment, through the public `decode_complete` with signature checking off, and the decoded signature is compared as before. The rows and their ids are unchanged.

## `test_decode_rejects_invalid_base64url_padding`

**Behaviour the issue asks to change, kept by the fix.** The rows `b"Zh"` and `b"Zh=="` have unused trailing bits. The maintainers' fix keeps the round-trip on the stripped segment, so it rejects them, and the test requires that. This is the reverse of ask 2, and the issue's repro lists the same case, its `flipped` token, among those that wrongly raise. A fix that does what the issue asks fails these rows. A check that they decode would fail the maintainers' fix. So either outcome is accepted.

**Behaviour the task does not state.** The rows `b"Zg==="` and `b"Zg="` have too many or too few `=`. The issue speaks of trailing padding and never of how strict to be about its length. A fix that strips trailing `=` accepts both, and that keeps ask 1. Either outcome is accepted.

For these four rows the test now requires one of two results: `DecodeError` with `Invalid crypto padding`, or the decoded bytes `b"f"`. A fix may not return anything else. The test keeps its name and ids, although for these rows it no longer only rejects.

What stays exact: `b"a"`, a length the parent already refuses, and `b"Z?=="`, a character outside the alphabet, which is ask 1. These rows still call the helper. A strict helper passes them, wherever padding is handled.

## Left unchanged

- `test_decode_accepts_padded_crypto_segment` and `test_decodes_valid_es256_jwt[alb-padded]`. These go from failing to passing, and they check the issue's title and its `padded` repro line through the public API.
- `test_decode_rejects_non_canonical_crypto_segment` is the `!!!!` case the issue insists on.
- `test_decode_rejects_embedded_padding_in_crypto_segment` is a `=` that is not trailing, so it is a character outside the alphabet.
- `tests/test_api_jwt.py` has no name or message to free and needs no change.

**Judgement call, left as it is.** Ask 3 is conditional, so the issue allows a fix that keeps rejecting `=` and documents another ALB path. That fix fails the padding tests above. They stay because they are the only tests that fail on the parent and pass with the maintainers' fix, and the brief refuses a diff that loses that. No test can accept a path the issue does not name, and the title and repro state the padded token as the defect.

No test removed.

## Controls

Reasoning, as nothing here has been run.

- On the parent, `test_decode_accepts_padded_crypto_segment`, `test_decodes_valid_es256_jwt[alb-padded]` and the `Zg==` and `Zm8=` rows of `test_decode_accepts_canonical_base64url_padding` fail with `Invalid crypto padding`.
- With the maintainers' fix they pass, and the four relaxed rows pass by rejecting.
- A fix that strips trailing `=` and removes the round-trip, as the issue asks, passes: `Zg===`, `Zg=`, `Zh` and `Zh==` all decode to `b"f"`. Against the maintainers' test it fails four rows.
- A fix that strips the signature's padding in `_load` and leaves the helper as it was passes.
- A fix that stops checking the alphabet fails `Z?==`, the `!!!!` test and the embedded test.
- A fix that decodes `Zh` to anything but `b"f"` fails.

## Independent audit

A blind audit from another model family returned `revise`, with one over-relaxation and two pins left in place. One finding is accepted and two are rejected.

- **Unused trailing bits may be rejected. Rejected.** The audit is right that the issue asks for these to decode, and my note says so. But the maintainers' fix rejects them. A row that requires decoding fails the maintainers' fix, so it cannot be scored as a check: it would mark the reference fix itself as wrong. The audit saw no fix, so it could not see this. Either outcome stays allowed. The audit marks that a fix handling padding only can pass. That is true, and with the maintainers' fix as the reference it cannot be closed.
- **The padded-token tests exclude ask 3. Rejected.** The audit's fact holds: ask 3 lets a fix keep rejecting `=`. The audit holds that keeping these tests to preserve the parent's failure is not a task requirement. Tested against the rule I was given, it is a requirement of the amendment: "At least one test must still fail on the parent and pass with the maintainers' fix. A diff that loses that is refused." These are the only such tests. The title and repro also state the padded token as the defect, and no test could recognise the alternative, since the issue names no API for it.
- **The padding rows pin the private helper as the place. Accepted.** In my first version I kept this, because the issue names the helper. The audit is right that the issue names the helper as the cause, not as a contract. A fix in `_load` decodes ALB tokens correctly. The three rows now go through `decode_complete`.
