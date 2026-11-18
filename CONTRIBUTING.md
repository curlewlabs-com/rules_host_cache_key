# Contributing

Thanks for your interest in improving `rules_host_cache_key`. The bar for a
change is whether the key still changes when the host tools it covers change,
and stays the same on machines whose tools are the same. Bug fixes, coverage of
a host the key now refuses, sharper refusals of hosts it cannot cover, and
clearer docs are all welcome.

Use the Bazel release in `.bazelversion`, Python 3.10 or later for the checks
(the key itself needs only 3.9), and Git. Keep changes on a branch and open a
pull request with the behavior change and its verification.

## Before a large change

Propose it in an issue before writing it. What the key reads, what it leaves
out on purpose, and what the rule asks Bazel to watch are set out in the
README's [What the key covers](README.md#what-the-key-covers) and
[When the key changes](README.md#when-the-key-changes), and `host_key.py`
computes them. A change to what the key covers changes every host's key once,
so a short discussion up front saves rework.

## Maintainership and review

Curlew Labs maintains this project. Jeffrey Wall
([jeffwall-curlewlabs](https://github.com/jeffwall-curlewlabs)) reviews changes
and approves the releases that
[need approval](docs/releases.md#which-releases-need-approval). Use issues for
reproducible defects and proposals, and
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

## License

By submitting a contribution for inclusion in this project, you agree that it
is licensed under the MIT and Apache-2.0 licenses, as the README's License
section describes, without any additional terms or conditions.
