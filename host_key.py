#!/usr/bin/env python3
"""Compute a cache key for the host tools that Bazel actions borrow.

The `host_cache_key` repository rule runs this when Bazel fetches the
repository and puts the key on an execution platform. Bazel adds a platform's
exec_properties to every action's cache key, so a result is reused only where
the borrowed tools match the host that produced it.

Prints one JSON object: the `key`, the `fields` it digests, and the paths the
rule watches so that a change to any of them computes the key again.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import stat
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path

# Bump when what a field means changes, so every host gets a new key.
SCHEMA = 1

MACOS_VERSION = Path("/System/Library/CoreServices/SystemVersion.plist")
# /bin/sh on macOS is a shim that runs the shell this link selects.
MACOS_SHELL_SELECTION = Path("/private/var/select/sh")
XCODE_SELECTION = Path("/var/db/xcode_select_link")
COMMAND_LINE_TOOLS_PACKAGE = "com.apple.pkg.CLTools_Executables"
COMMAND_LINE_TOOLS_RECEIPT = Path(
    f"/Library/Apple/System/Library/Receipts/{COMMAND_LINE_TOOLS_PACKAGE}.plist"
)

OS_RELEASES = (Path("/etc/os-release"), Path("/usr/lib/os-release"))
DPKG_STATUS = Path("/var/lib/dpkg/status")
DPKG_DIVERSIONS = Path("/var/lib/dpkg/diversions")
DPKG_ALTERNATIVES = Path("/var/lib/dpkg/alternatives")
ALTERNATIVES = Path("/etc/alternatives")

# Homebrew writes these into a keg on every pour, with install times and the
# Homebrew revision that poured it, so they differ between hosts holding the
# same bytes.
KEG_INSTALL_RECORDS = frozenset({"INSTALL_RECEIPT.json", "sbom.spdx.json", ".brew"})


class UnsupportedHost(Exception):
    pass


@dataclass
class Watches:
    """Paths whose change must make Bazel compute the key again."""

    files: set[str] = field(default_factory=set)
    directories: set[str] = field(default_factory=set)

    def file(self, path: Path) -> None:
        self.files.add(str(path))

    def listing(self, path: Path) -> None:
        # Bazel can list only a directory that exists; an absent one is watched
        # for appearing.
        if path.is_dir():
            self.directories.add(str(path))
        else:
            self.files.add(str(path))


def content_digest(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def command_output(*command: str) -> str:
    return subprocess.run(
        list(command), check=True, capture_output=True, text=True
    ).stdout.strip()


def entry_identity(entry: Path, digests: dict[Path, str], watches: Watches) -> str:
    # Type and mode decide whether a PATH search runs the entry at all.
    parts = [entry.name, oct(entry.lstat().st_mode)]
    if entry.is_symlink():
        parts.append(os.readlink(entry))
    try:
        # Resolution follows alternatives and other links to the file that runs.
        target = entry.resolve(strict=True)
    except OSError:
        parts.append("unresolved")
        return "\0".join(parts)
    status = target.stat()
    parts += [str(target), oct(status.st_mode)]
    if stat.S_ISREG(status.st_mode):
        if os.access(target, os.R_OK):
            if target not in digests:
                digests[target] = content_digest(target)
            parts.append(digests[target])
            watches.file(entry)
        else:
            # Bazel cannot watch a file it cannot read, and an inode or a
            # timestamp would differ between hosts holding the same file. Its
            # bytes belong to the OS build or to a package, which the platform
            # fields key.
            parts.append(f"unreadable:{status.st_size}")
    return "\0".join(parts)


def search_path_identity(directories: list[Path], watches: Watches) -> str:
    """Digest every entry a PATH search or an absolute tool path can reach."""
    digest = hashlib.sha256()
    digests: dict[Path, str] = {}
    listed: set[Path] = set()
    for directory in directories:
        resolved = directory.resolve()
        digest.update(f"{directory}={resolved}\n".encode())
        if directory != resolved:
            watches.file(directory)
        watches.listing(resolved)
        if resolved in listed:
            continue
        listed.add(resolved)
        if not resolved.is_dir():
            digest.update(b"absent\n")
            continue
        # PATH search looks at direct entries only, never into subdirectories.
        for entry in sorted(resolved.iterdir()):
            digest.update(f"{entry_identity(entry, digests, watches)}\n".encode())
    return digest.hexdigest()


def developer_tools_version(developer_dir: Path, watches: Watches) -> str:
    # An Xcode bundle versions its developer directory in version.plist; the
    # Command Line Tools record their version only in a package receipt.
    version_plist = developer_dir.parent / "version.plist"
    watches.file(version_plist)
    if version_plist.is_file():
        return content_digest(version_plist)
    watches.file(COMMAND_LINE_TOOLS_RECEIPT)
    try:
        receipt = command_output(
            "/usr/sbin/pkgutil", f"--pkg-info={COMMAND_LINE_TOOLS_PACKAGE}"
        )
    except subprocess.CalledProcessError:
        return "none"
    # The receipt's install time differs between hosts with the same tools.
    return next(
        (line for line in receipt.splitlines() if line.startswith("version:")),
        "unversioned",
    )


def macos_fields(watches: Watches) -> dict[str, object]:
    watches.file(MACOS_VERSION)
    watches.file(XCODE_SELECTION)
    try:
        developer_dir = command_output("/usr/bin/xcode-select", "--print-path")
    except subprocess.CalledProcessError:
        developer_dir = ""
    if MACOS_SHELL_SELECTION.is_symlink():
        watches.file(MACOS_SHELL_SELECTION)
        shell = os.readlink(MACOS_SHELL_SELECTION)
    else:
        shell = "unselected"
    return {
        # The build number stands in for the system libraries and every file
        # on the sealed system volume, which macOS replaces only as a whole.
        "sw_vers": command_output("/usr/bin/sw_vers"),
        # The /usr/bin developer shims run tools from this directory.
        "developer_dir": developer_dir or "none",
        "developer_tools": (
            developer_tools_version(Path(developer_dir), watches)
            if developer_dir
            else "none"
        ),
        "shell": shell,
    }


def installed_packages() -> str:
    listing = command_output(
        "/usr/bin/dpkg-query",
        "--show",
        "--showformat=${db:Status-Status} ${binary:Package} ${Architecture} "
        "${Version}\n",
    )
    installed = sorted(
        line for line in listing.splitlines() if line.startswith("installed ")
    )
    if not installed:
        raise UnsupportedHost("dpkg lists no installed package")
    return hashlib.sha256("\n".join(installed).encode()).hexdigest()


def package_selections(watches: Watches) -> str:
    """Digest the alternatives and diversions that pick a path's file."""
    digest = hashlib.sha256()
    watches.listing(ALTERNATIVES)
    if ALTERNATIVES.is_dir():
        for entry in sorted(ALTERNATIVES.iterdir()):
            if entry.is_symlink():
                digest.update(f"{entry.name}={os.readlink(entry)}\n".encode())
    # update-alternatives rewrites a selection's record here when it moves one.
    watches.listing(DPKG_ALTERNATIVES)
    if DPKG_ALTERNATIVES.is_dir():
        for record in DPKG_ALTERNATIVES.iterdir():
            if record.is_file():
                watches.file(record)
    watches.file(DPKG_DIVERSIONS)
    if DPKG_DIVERSIONS.is_file():
        digest.update(DPKG_DIVERSIONS.read_bytes())
    return digest.hexdigest()


def linux_fields(watches: Watches) -> dict[str, object]:
    for release in OS_RELEASES:
        watches.file(release)
    releases = [path for path in OS_RELEASES if path.is_file()]
    if not releases:
        raise UnsupportedHost("no os-release file")
    watches.file(DPKG_STATUS)
    if not DPKG_STATUS.is_file():
        raise UnsupportedHost(
            "the installed package set is read from dpkg, and this host has no "
            f"{DPKG_STATUS}"
        )
    return {
        "os_release": releases[0].read_text(),
        # The system libraries and the dynamic loader belong to installed
        # packages, and the selections pick among packaged files.
        "packages": installed_packages(),
        "package_selections": package_selections(watches),
    }


def bottle_digest(keg: Path, formula: str) -> str | None:
    """Return the sha256 of the bottle Homebrew poured into `keg`, if recorded.

    Pouring is deterministic for one prefix, so the bottle's digest identifies
    the keg's bytes on every host that poured it, without reading them.
    """
    try:
        document = json.loads((keg / "sbom.spdx.json").read_text())
    except (OSError, ValueError):
        return None
    # An SBOM of another shape names no bottle, so the keg is keyed by its
    # content rather than failing the fetch.
    packages = document.get("packages") if isinstance(document, dict) else None
    for package in packages if isinstance(packages, list) else []:
        if not isinstance(package, dict) or package.get("name") != formula:
            continue
        location = package.get("downloadLocation")
        if not isinstance(location, str) or "/blobs/sha256:" not in location:
            continue
        checksums = package.get("checksums")
        for checksum in checksums if isinstance(checksums, list) else []:
            if isinstance(checksum, dict) and checksum.get("algorithm") == "SHA256":
                value = checksum.get("checksumValue")
                return value if isinstance(value, str) else None
    return None


def keg_content_digest(keg: Path) -> str:
    """Digest a keg Homebrew built rather than poured, minus its records."""
    digest = hashlib.sha256()
    pending = [keg]
    while pending:
        directory = pending.pop()
        for entry in sorted(directory.iterdir()):
            if directory == keg and entry.name in KEG_INSTALL_RECORDS:
                continue
            relative = entry.relative_to(keg)
            if entry.is_symlink():
                digest.update(f"link {relative} {os.readlink(entry)}\n".encode())
            elif entry.is_dir():
                digest.update(f"directory {relative}\n".encode())
                pending.append(entry)
            elif entry.is_file():
                mode = oct(entry.stat().st_mode & 0o777)
                line = f"file {relative} {mode} {content_digest(entry)}\n"
                digest.update(line.encode())
    return digest.hexdigest()


def homebrew_identity(prefix: Path, watches: Watches) -> dict[str, object] | None:
    cellar = prefix / "Cellar"
    watches.listing(cellar)
    if not cellar.is_dir():
        return None
    kegs: list[str] = []
    for formula in sorted(path for path in cellar.iterdir() if path.is_dir()):
        watches.listing(formula)
        for keg in sorted(path for path in formula.iterdir() if path.is_dir()):
            # Homebrew rewrites both records whenever it pours or rebuilds the
            # keg, which is when its bytes can change.
            watches.file(keg / "INSTALL_RECEIPT.json")
            watches.file(keg / "sbom.spdx.json")
            bottle = bottle_digest(keg, formula.name)
            identity = (
                f"bottle:{bottle}"
                if bottle is not None
                else f"content:{keg_content_digest(keg)}"
            )
            kegs.append(f"{formula.name} {keg.name} {identity}")
    # Executables and libraries are reached through these links, so they
    # decide which keg a tool or a library load resolves to.
    links = hashlib.sha256()
    for farm in ("opt", "bin"):
        directory = prefix / farm
        watches.listing(directory)
        if not directory.is_dir():
            continue
        for entry in sorted(directory.iterdir()):
            target = os.readlink(entry) if entry.is_symlink() else "-"
            links.update(f"{farm}/{entry.name} {target}\n".encode())
    return {"kegs": kegs, "links": links.hexdigest()}


def host_fields(
    search_path: list[Path], homebrew_prefixes: list[Path], watches: Watches
) -> dict[str, object]:
    system = platform.system()
    fields: dict[str, object] = {
        "schema": SCHEMA,
        "system": system,
        "machine": platform.machine(),
    }
    if system == "Darwin":
        fields.update(macos_fields(watches))
    elif system == "Linux":
        fields.update(linux_fields(watches))
    else:
        raise UnsupportedHost(f"no host key for {system}")
    fields["search_path"] = search_path_identity(search_path, watches)
    homebrew: dict[str, object] = {}
    for prefix in homebrew_prefixes:
        identity = homebrew_identity(prefix, watches)
        if identity is not None:
            homebrew[str(prefix)] = identity
    fields["homebrew"] = homebrew
    return fields


def host_key(fields: dict[str, object]) -> str:
    canonical = json.dumps(fields, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode()).hexdigest()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--search-path", action="append", default=[], type=Path)
    parser.add_argument("--homebrew-prefix", action="append", default=[], type=Path)
    args = parser.parse_args(argv)
    watches = Watches()
    try:
        fields = host_fields(args.search_path, args.homebrew_prefix, watches)
    except UnsupportedHost as error:
        print(f"host_cache_key: {error}", file=sys.stderr)
        return 2
    json.dump(
        {
            "key": host_key(fields),
            "fields": fields,
            "watch_files": sorted(watches.files),
            "watch_directories": sorted(watches.directories),
        },
        sys.stdout,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
