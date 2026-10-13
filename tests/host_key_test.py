"""Behavior of the host key computation against scratch hosts."""

from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import host_key

# The host's real system volume where it has one, so the scratch tools below sit
# off it, as a host's own installs do.
SYSTEM_VOLUME = (
    os.statvfs(host_key.MACOS_VERSION).f_fsid if sys.platform == "darwin" else None
)


def pour(
    prefix: Path,
    formula: str,
    version: str,
    bottle: str | None,
    installed_at: int,
    payload: bytes = b"tool",
) -> Path:
    """Lay out a keg the way Homebrew leaves one, with its per-install records."""
    keg = prefix / "Cellar" / formula / version
    (keg / "bin").mkdir(parents=True)
    (keg / "bin" / formula).write_bytes(payload)
    (keg / "INSTALL_RECEIPT.json").write_text(json.dumps({"time": installed_at}))
    packages: list[dict[str, object]] = [
        {"name": formula, "downloadLocation": "https://example.invalid/src.tar.gz"}
    ]
    if bottle is not None:
        packages.append(
            {
                "name": formula,
                "downloadLocation": "https://ghcr.io/v2/homebrew/core/"
                f"{formula}/blobs/sha256:{bottle}",
                "checksums": [{"algorithm": "SHA256", "checksumValue": bottle}],
            }
        )
    sbom = {"creationInfo": {"created": str(installed_at)}, "packages": packages}
    (keg / "sbom.spdx.json").write_text(json.dumps(sbom))
    (keg / ".brew").mkdir()
    (keg / ".brew" / f"{formula}.rb").write_text(f"# fetched at {installed_at}\n")
    opt = prefix / "opt"
    opt.mkdir(exist_ok=True)
    link = opt / formula
    if link.is_symlink():
        link.unlink()
    link.symlink_to(f"../Cellar/{formula}/{version}")
    return keg


def homebrew(prefix: Path) -> object:
    return host_key.homebrew_identity(prefix, host_key.Watches())


class HomebrewTest(unittest.TestCase):
    def setUp(self) -> None:
        scratch = tempfile.TemporaryDirectory()
        self.addCleanup(scratch.cleanup)
        self.scratch = Path(scratch.name)

    def test_same_bottles_key_alike_whenever_they_were_poured(self) -> None:
        # Two hosts that poured the same bottles at different times must share
        # results; install records carry times and must not split them.
        first, second = self.scratch / "first", self.scratch / "second"
        pour(first, "bash", "5.3.20", "a" * 64, installed_at=1)
        pour(second, "bash", "5.3.20", "a" * 64, installed_at=2)
        self.assertEqual(homebrew(first), homebrew(second))

    def test_another_bottle_of_the_same_version_rekeys(self) -> None:
        # A rebuilt bottle keeps its version, and only its digest tells a
        # result built against the old bytes from one built against the new.
        first, second = self.scratch / "first", self.scratch / "second"
        pour(first, "bash", "5.3.20", "a" * 64, installed_at=1)
        pour(second, "bash", "5.3.20", "b" * 64, installed_at=1)
        self.assertNotEqual(homebrew(first), homebrew(second))

    def test_unbottled_keg_is_keyed_by_content_not_by_records(self) -> None:
        # A keg without a recorded bottle has nothing else that names its
        # bytes; reading them must still ignore the per-install records.
        first, second = self.scratch / "first", self.scratch / "second"
        pour(first, "tool", "1.0", None, installed_at=1)
        keg = pour(second, "tool", "1.0", None, installed_at=2)
        self.assertEqual(homebrew(first), homebrew(second))
        (keg / "bin" / "tool").write_bytes(b"rebuilt")
        self.assertNotEqual(homebrew(first), homebrew(second))

    def test_relinking_to_another_keg_rekeys(self) -> None:
        # Tools and library loads resolve through opt/, so moving a link to a
        # second installed version changes what actions run.
        prefix = self.scratch / "prefix"
        pour(prefix, "tool", "1.0", "a" * 64, installed_at=1)
        pour(prefix, "tool", "2.0", "b" * 64, installed_at=1)
        before = homebrew(prefix)
        link = prefix / "opt" / "tool"
        link.unlink()
        link.symlink_to("../Cellar/tool/1.0")
        self.assertNotEqual(before, homebrew(prefix))

    def test_malformed_sbom_keys_the_keg_by_content(self) -> None:
        # An SBOM is Homebrew's record, not ours; a shape the reader does not
        # expect must leave the keg keyed by its bytes, not fail the fetch.
        for index, malformed in enumerate(
            (
                "[]",
                "null",
                '{"packages": {}}',
                '{"packages": ["bash"]}',
                '{"packages": [{"name": "bash", "downloadLocation": 1}]}',
                (
                    '{"packages": [{"name": "bash", "downloadLocation":'
                    ' "https://ghcr.io/v2/homebrew/core/bash/blobs/sha256:aa",'
                    ' "checksums": ["SHA256"]}]}'
                ),
            )
        ):
            with self.subTest(sbom=malformed):
                prefix = self.scratch / f"prefix-{index}"
                keg = pour(prefix, "bash", "5.3.20", None, installed_at=1)
                (keg / "sbom.spdx.json").write_text(malformed)
                identity = host_key.homebrew_identity(prefix, host_key.Watches())
                assert isinstance(identity, dict)
                kegs = identity["kegs"]
                assert isinstance(kegs, list)
                self.assertEqual(len(kegs), 1)
                self.assertIn(" content:", kegs[0])

    def test_prefix_without_cellar_is_skipped_and_watched(self) -> None:
        # Installing Homebrew later must reach Bazel, so the absent Cellar is
        # watched for appearing.
        watches = host_key.Watches()
        absent = self.scratch / "absent"
        self.assertIsNone(host_key.homebrew_identity(absent, watches))
        self.assertIn(str(absent / "Cellar"), watches.files)


class SearchPathTest(unittest.TestCase):
    def setUp(self) -> None:
        scratch = tempfile.TemporaryDirectory()
        self.addCleanup(scratch.cleanup)
        self.directory = Path(scratch.name)

    def identity(self) -> str:
        return host_key.search_path_identity(
            [self.directory], host_key.Watches(), SYSTEM_VOLUME
        )

    def test_changed_tool_bytes_rekey(self) -> None:
        tool = self.directory / "tool"
        tool.write_bytes(b"one")
        before = self.identity()
        tool.write_bytes(b"two")
        self.assertNotEqual(before, self.identity())

    def test_retargeted_link_rekeys(self) -> None:
        # Alternatives select a tool by moving a link, not by changing bytes.
        (self.directory / "first").write_bytes(b"one")
        (self.directory / "second").write_bytes(b"two")
        link = self.directory / "tool"
        link.symlink_to("first")
        before = self.identity()
        link.unlink()
        link.symlink_to("second")
        self.assertNotEqual(before, self.identity())

    def test_linked_directory_is_watched_through_its_link(self) -> None:
        # Bazel records a watched link to a directory only as a directory, so
        # watches under the resolved path would never see the link moved.
        (self.directory / "one").mkdir()
        (self.directory / "one" / "tool").write_bytes(b"one")
        linked = self.directory / "tools"
        linked.symlink_to("one")
        watches = host_key.Watches()
        host_key.search_path_identity([linked], watches, SYSTEM_VOLUME)
        self.assertIn(str(linked / "tool"), watches.files)
        self.assertIn(str(linked), watches.directories)

    @unittest.skipIf(os.geteuid() == 0, "root reads every file")
    def test_unreadable_file_ignores_timestamps(self) -> None:
        # An inode or a timestamp would differ between hosts holding the same
        # file, so the key of an unreadable file must not include them.
        tool = self.directory / "tool"
        tool.write_bytes(b"one")
        tool.chmod(0)
        self.addCleanup(tool.chmod, 0o600)
        before = self.identity()
        os.utime(tool, (1, 1))
        self.assertEqual(before, self.identity())


@unittest.skipUnless(sys.platform == "darwin", "only macOS has a sealed system volume")
class SystemVolumeTest(unittest.TestCase):
    def setUp(self) -> None:
        scratch = tempfile.TemporaryDirectory()
        self.addCleanup(scratch.cleanup)
        # Watches name resolved paths, and macOS reaches the temporary
        # directory through a link.
        self.directory = Path(scratch.name).resolve()

    def test_system_tools_are_not_watched_or_digested(self) -> None:
        # Watching them makes every new Bazel server read each system tool
        # whole, which takes tens of seconds on a freshly booted host; the OS
        # build already keys them.
        watches = host_key.Watches()
        host_key.search_path_identity([Path("/bin")], watches, SYSTEM_VOLUME)
        self.assertEqual((watches.files, watches.directories), (set(), set()))
        identity = host_key.entry_identity(Path("/bin/sh"), {}, watches, SYSTEM_VOLUME)
        self.assertNotIn(host_key.content_digest(Path("/bin/sh")), identity)

    def test_link_within_the_system_volume_is_not_watched(self) -> None:
        # A link that resolves in its own directory never leaves the volume, so
        # the OS build keys the link as well as the file it reaches.
        link = next(
            entry
            for entry in sorted(Path("/usr/bin").iterdir())
            if entry.is_symlink() and "/" not in os.readlink(entry)
        )
        watches = host_key.Watches()
        identity = host_key.entry_identity(link, {}, watches, SYSTEM_VOLUME)
        self.assertTrue(identity.endswith("\0system-volume"), identity)
        self.assertEqual(watches.files, set())

    def test_link_from_elsewhere_into_the_system_volume_stays_watched(self) -> None:
        # Moving a link outside the volume changes which tool runs without an
        # OS update, so the build cannot stand in for it.
        (self.directory / "sh").symlink_to("/bin/sh")
        watches = host_key.Watches()
        host_key.search_path_identity([self.directory], watches, SYSTEM_VOLUME)
        self.assertIn(str(self.directory / "sh"), watches.files)
        self.assertIn(str(self.directory), watches.directories)

    def test_path_through_a_movable_directory_link_is_not_covered(self) -> None:
        # A directory link off the volume can be pointed at other tools without
        # an OS update, even while it leads onto the volume.
        (self.directory / "bin").symlink_to("/bin")
        covered = host_key.on_system_volume
        self.assertTrue(covered(Path("/bin/sh"), SYSTEM_VOLUME))
        self.assertFalse(covered(self.directory / "bin" / "sh", SYSTEM_VOLUME))


@unittest.skipUnless(sys.platform == "darwin", "only macOS selects an Xcode")
class XcodeSelectionTest(unittest.TestCase):
    def test_selection_is_watched_through_its_link(self) -> None:
        # xcode-select moves a link to a directory, which Bazel records only as
        # a directory; without a file watched through it, a newly selected
        # Xcode would keep the old one's key.
        watches = host_key.Watches()
        host_key.macos_fields(watches)
        through = host_key.XCODE_SELECTION / "usr" / "bin" / "xcodebuild"
        self.assertIn(str(through), watches.files)


if __name__ == "__main__":
    unittest.main()
