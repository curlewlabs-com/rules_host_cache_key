# Contributing

Use the Bazel release in `.bazelversion`, Python 3.10 or later for the checks
(the key itself needs only 3.9), and Git. Keep changes on a branch and open a
pull request with the behavior change and its verification.

## Maintainership and review

Curlew Labs maintains this project. Jeffrey Wall
([jeffwall-curlewlabs](https://github.com/jeffwall-curlewlabs)) reviews changes
and approves releases. Use issues for reproducible defects and proposals, and
[private vulnerability reporting](SECURITY.md) for security concerns.

Changes land through pull requests with passing CI and maintainer review.
Fixed version tags and published release artifacts are never replaced.

## Local checks

```sh
pre-commit run --all-files
python3 -m unittest discover -s tests -p '*_test.py'
python3 tests/check_platform.py
```

`tests/check_platform.py` drives real Bazel outside a Bazel test: it starts
its own servers, changes files the repository rule watches, and reads each
build's process summary. It needs network access for Bazel's own downloads.

## Releases

See [release preparation](docs/releases.md).
