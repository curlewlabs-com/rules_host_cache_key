# Release preparation

A release is a source-only archive of a commit on `main`.

## Prepare a candidate

From a clean checkout of the candidate commit:

```sh
python3 tools/prepare_release.py > /tmp/release-notes.md
```

The helper reads the module name and version from Bazel, packages committed
`HEAD` with normalized ownership, timestamps and gzip metadata so the bytes
depend on the tree alone, and runs `tests/check_platform.py` against the
archive's own copy of the module. It writes the archive, `SHA256SUMS` and
`source.json` under `dist/`, and prints release notes with an install snippet.
An optional `vMAJOR.MINOR.PATCH` argument must match the module version.

## Tag and publish

Bump `version` in `MODULE.bazel` through a pull request, then push a fixed
`vMAJOR.MINOR.PATCH` tag at that commit on `main`. Never move or replace a
fixed tag. The [release workflow](../.github/workflows/release.yaml) checks
that the commit is on `main`, runs CI, and uses the pinned
[bazel-contrib release workflow](https://github.com/bazel-contrib/.github/blob/v7.7.0/.github/workflows/release_ruleset.yaml)
to prepare the archive again, attest its provenance, and attach it to a draft
release. Check the draft's assets against `SHA256SUMS`, then publish it.
Immutable releases keep published bytes from changing; fix a defect with a new
version.
