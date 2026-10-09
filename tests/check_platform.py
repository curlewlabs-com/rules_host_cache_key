#!/usr/bin/env python3
"""Show host changes reaching action cache keys through real Bazel.

Builds a consumer of this module whose key covers a scratch tool directory and
a scratch Homebrew prefix, changes each, and reads Bazel's process summary to
tell whether the action ran, was reused in place, or came from the disk cache.
This runs outside a Bazel test: it starts its own Bazel servers and changes
files the repository rule watches.
"""

from __future__ import annotations

import argparse
import json
import platform
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SUMMARY = re.compile(r"INFO: \d+ process(?:es)?: (.*)\.$", re.MULTILINE)
BOTTLE = "a" * 64

CONSUMER_BUILD = """\
genrule(
    name = "action",
    outs = ["action.txt"],
    cmd = "echo built > $@",
)
"""


def consumer_module(module_root: Path, tools: Path, prefix: Path | None) -> str:
    homebrew = f'homebrew_prefixes = ["{prefix}"],' if prefix is not None else ""
    search_path = (
        f'search_path = ["/bin", "/usr/bin", "/usr/local/bin", "{tools}"],'
        if prefix is not None
        else ""
    )
    return f"""\
module(name = "consumer")

bazel_dep(name = "rules_host_cache_key")
local_path_override(module_name = "rules_host_cache_key", path = "{module_root}")

host_cache_key = use_repo_rule("@rules_host_cache_key//:defs.bzl", "host_cache_key")

host_cache_key(
    name = "host_cache_key",
    {homebrew}
    {search_path}
)

register_execution_platforms("@host_cache_key//:platform")
"""


def write_sbom(keg: Path, bottle: str) -> None:
    sbom = {
        "packages": [
            {
                "name": "tool",
                "downloadLocation": "https://ghcr.io/v2/homebrew/core/tool/"
                f"blobs/sha256:{bottle}",
                "checksums": [{"algorithm": "SHA256", "checksumValue": bottle}],
            }
        ]
    }
    (keg / "sbom.spdx.json").write_text(json.dumps(sbom))


class Consumer:
    def __init__(self, bazel: str, workspace: Path, disk_cache: Path) -> None:
        self.bazel = bazel
        self.workspace = workspace
        self.disk_cache = disk_cache
        self.output_bases: list[Path] = []

    def build(self, output_base: Path) -> str:
        if output_base not in self.output_bases:
            self.output_bases.append(output_base)
        completed = subprocess.run(
            [
                self.bazel,
                "--nohome_rc",
                "--nosystem_rc",
                f"--output_base={output_base}",
                "build",
                "//:action",
                f"--disk_cache={self.disk_cache}",
            ],
            cwd=self.workspace,
            capture_output=True,
            text=True,
            check=False,
        )
        if completed.returncode != 0:
            sys.stderr.write(completed.stderr)
            raise RuntimeError(f"bazel build failed in {output_base}")
        return completed.stderr

    def key_report(self, output_base: Path) -> dict[str, object]:
        reports = list(output_base.glob("external/*host_cache_key/key.json"))
        if len(reports) != 1:
            raise RuntimeError(f"expected one key.json under {output_base}")
        report: dict[str, object] = json.loads(reports[0].read_text())
        return report

    def shutdown(self) -> None:
        for output_base in self.output_bases:
            subprocess.run(
                [
                    self.bazel,
                    "--nohome_rc",
                    "--nosystem_rc",
                    f"--output_base={output_base}",
                    "shutdown",
                ],
                cwd=self.workspace,
                capture_output=True,
                check=False,
            )


def outcome(stderr: str) -> str:
    summaries = SUMMARY.findall(stderr)
    if not summaries:
        raise RuntimeError("bazel printed no process summary")
    kinds = {part.split(" ", 1)[1] for part in summaries[-1].split(", ")}
    if kinds == {"internal"}:
        return "reused"
    if kinds <= {"internal", "disk cache hit"}:
        return "disk cache hit"
    return "executed"


def expect(step: str, stderr: str, wanted: str) -> None:
    observed = outcome(stderr)
    print(f"check_platform: {step}: {observed}", flush=True)
    if observed != wanted:
        sys.stderr.write(stderr)
        raise SystemExit(f"check_platform: {step}: expected {wanted}, got {observed}")


def check_watched_changes(bazel: str, module_root: Path, scratch: Path) -> None:
    tools = scratch / "tools"
    tools.mkdir()
    tool = tools / "tool"
    tool.write_bytes(b"one")
    prefix = scratch / "brew"
    keg = prefix / "Cellar" / "tool" / "1.0"
    keg.mkdir(parents=True)
    write_sbom(keg, BOTTLE)
    workspace = scratch / "consumer"
    workspace.mkdir()
    shutil.copy(module_root / ".bazelversion", workspace / ".bazelversion")
    (workspace / "BUILD.bazel").write_text(CONSUMER_BUILD)
    (workspace / "MODULE.bazel").write_text(consumer_module(module_root, tools, prefix))

    consumer = Consumer(bazel, workspace, scratch / "disk-cache")
    first, second = scratch / "first-output-base", scratch / "second-output-base"
    try:
        expect("first build", consumer.build(first), "executed")
        expect("unchanged host", consumer.build(first), "reused")
        report = consumer.key_report(first)
        fields = report["fields"]
        assert isinstance(fields, dict)
        kegs = fields["homebrew"][str(prefix)]["kegs"]
        if kegs != [f"tool 1.0 bottle:{BOTTLE}"]:
            raise SystemExit(f"check_platform: scratch keg keyed as {kegs}")

        tool.write_bytes(b"two")
        expect("changed tool, running server", consumer.build(first), "executed")
        write_sbom(keg, "b" * 64)
        expect("another bottle, running server", consumer.build(first), "executed")
        expect("same host, fresh output base", consumer.build(second), "disk cache hit")
        # A hit alone would also follow from a key Bazel ignored; a host state
        # no build has seen must miss the shared cache.
        tool.write_bytes(b"three")
        expect("unseen tools, fresh output base", consumer.build(second), "executed")

        # Restoring the original tools restores the original key, so the
        # first build's result is reused: the key follows content, not time.
        tool.write_bytes(b"one")
        write_sbom(keg, BOTTLE)
        expect("original tools restored", consumer.build(second), "disk cache hit")
    finally:
        consumer.shutdown()


def check_this_host(bazel: str, module_root: Path, scratch: Path) -> None:
    workspace = scratch / "defaults"
    workspace.mkdir()
    shutil.copy(module_root / ".bazelversion", workspace / ".bazelversion")
    (workspace / "BUILD.bazel").write_text(CONSUMER_BUILD)
    (workspace / "MODULE.bazel").write_text(consumer_module(module_root, Path(), None))
    consumer = Consumer(bazel, workspace, scratch / "defaults-disk-cache")
    output_base = scratch / "defaults-output-base"
    try:
        expect("default attributes", consumer.build(output_base), "executed")
        report = consumer.key_report(output_base)
    finally:
        consumer.shutdown()
    fields = report["fields"]
    assert isinstance(fields, dict)
    homebrew = fields["homebrew"]
    assert isinstance(homebrew, dict)
    kegs = sum(len(prefix["kegs"]) for prefix in homebrew.values())
    print(
        f"check_platform: {platform.system()} host key {report['key']}, "
        f"covering {kegs} Homebrew kegs",
        flush=True,
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bazel", default="bazel")
    parser.add_argument("--module-root", type=Path, default=ROOT)
    args = parser.parse_args()
    module_root = args.module_root.resolve()
    with tempfile.TemporaryDirectory() as directory:
        scratch = Path(directory).resolve()
        check_watched_changes(args.bazel, module_root, scratch)
        check_this_host(args.bazel, module_root, scratch)
    print("check_platform: host changes reach action keys", flush=True)


if __name__ == "__main__":
    main()
