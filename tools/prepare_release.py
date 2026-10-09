#!/usr/bin/env python3
"""Prepare and exercise a source-only release archive; print release notes."""

from __future__ import annotations

import argparse
import base64
import gzip
import hashlib
import io
import json
import re
import subprocess
import sys
import tarfile
import tempfile
from pathlib import Path

MODULE = "rules_host_cache_key"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("tag", nargs="?", help="Optional vMAJOR.MINOR.PATCH to verify")
    parser.add_argument("--bazel", default="bazel")
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    # The archive packages committed HEAD, so uncommitted edits would be
    # verified here and then left out of what ships.
    subprocess.run(
        ["git", "diff", "--exit-code", "HEAD", "--"],
        cwd=root,
        check=True,
        stdout=sys.stderr,
    )
    module = json.loads(
        subprocess.check_output(
            [
                args.bazel,
                "--ignore_all_rc_files",
                "mod",
                "graph",
                "--output=json",
                "--depth=1",
                "--ignore_dev_dependency",
                "--lockfile_mode=off",
            ],
            cwd=root,
            text=True,
        )
    )
    version = module["version"]
    tag = args.tag or f"v{version}"
    if (
        module["name"] != MODULE
        or not re.fullmatch(r"v[0-9]+\.[0-9]+\.[0-9]+", tag)
        or tag != f"v{version}"
    ):
        raise ValueError(
            f"Release tag {tag!r} does not match the module: {module['name']}@{version}"
        )
    revision = subprocess.check_output(
        ["git", "rev-parse", "HEAD"], cwd=root, text=True
    ).strip()
    prefix = f"{MODULE}-{version}"
    output = root / "dist"
    output.mkdir(exist_ok=True)
    archive = output / f"{prefix}.tar.gz"
    source = subprocess.check_output(
        ["git", "archive", "--format=tar", f"--prefix={prefix}/", "HEAD"], cwd=root
    )
    # Normalize Git's commit timestamp and PAX comment so the archive's bytes
    # depend on the committed tree alone, keeping tracked file modes.
    with (
        tarfile.open(fileobj=io.BytesIO(source)) as committed,
        archive.open("wb") as raw,
        gzip.GzipFile(fileobj=raw, mode="wb", filename="", mtime=0) as compressed,
        tarfile.open(fileobj=compressed, mode="w") as packaged,
    ):
        for member in committed.getmembers():
            member.mtime = 0
            member.uid = member.gid = 0
            member.uname = member.gname = ""
            member.pax_headers = {}
            packaged.addfile(
                member,
                committed.extractfile(member) if member.isfile() else None,
            )
    digest = hashlib.sha256(archive.read_bytes()).digest()
    integrity = "sha256-" + base64.b64encode(digest).decode()
    (output / "SHA256SUMS").write_text(f"{digest.hex()}  {archive.name}\n")
    (output / "source.json").write_text(
        json.dumps(
            {
                "module": MODULE,
                "version": version,
                "commit": revision,
                "archive": archive.name,
                "integrity": integrity,
                "strip_prefix": prefix,
            },
            indent=2,
        )
        + "\n"
    )
    print(
        f"Prepared {archive.name}: {archive.stat().st_size} bytes; {integrity}",
        file=sys.stderr,
        flush=True,
    )
    # Exercise the archive's own copy of the module, not this checkout.
    with tempfile.TemporaryDirectory() as directory:
        with tarfile.open(archive) as packaged:
            packaged.extractall(directory, filter="data")
        extracted = Path(directory) / prefix
        subprocess.run(
            [
                sys.executable,
                str(extracted / "tests" / "check_platform.py"),
                "--bazel",
                args.bazel,
                "--module-root",
                str(extracted),
            ],
            check=True,
            stdout=sys.stderr,
        )
    url = (
        f"https://github.com/curlewlabs-com/{MODULE}/releases/download/"
        f"{tag}/{archive.name}"
    )
    print(f"""Source commit: `{revision}`

Install with Bzlmod:

```starlark
bazel_dep(name = "{MODULE}", version = "{version}")
archive_override(
    module_name = "{MODULE}",
    urls = ["{url}"],
    integrity = "{integrity}",
    strip_prefix = "{prefix}",
)

host_cache_key = use_repo_rule("@{MODULE}//:defs.bzl", "host_cache_key")
host_cache_key(name = "host_cache_key")
register_execution_platforms("@host_cache_key//:platform")
```

This release is not yet in the Bazel Central Registry.
""")


if __name__ == "__main__":
    main()
