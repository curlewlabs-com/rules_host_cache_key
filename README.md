# rules_host_cache_key

Stop Bazel's disk and remote caches from reusing results across changes to the
host tools your actions borrow.

Bazel keys an action by the inputs it declares. Actions also run things they
never declare: whatever is on the action `PATH` (`/bin`, `/usr/bin`,
`/usr/local/bin`), the system libraries those tools load, the selected Xcode,
Homebrew packages. When one of those changes, the action's key does not, so
Bazel replays the old result, and a shared cache hands it to every machine.

This module computes a key from those host tools and puts it on an execution
platform. Bazel adds a platform's `exec_properties` to every action's cache
key, so:

- after an OS update, `brew upgrade`, `apt-get upgrade` or a new Xcode, actions
  run again instead of replaying results built against the old tools;
- machines with the same tools share results through a disk cache or a remote
  cache, and machines with different tools never exchange them.

## Setup

Add to `MODULE.bazel`:

```starlark
bazel_dep(name = "rules_host_cache_key", version = "0.0.1")

host_cache_key = use_repo_rule("@rules_host_cache_key//:defs.bzl", "host_cache_key")
host_cache_key(name = "host_cache_key")
register_execution_platforms("@host_cache_key//:platform")
```

Until the module is in the Bazel Central Registry, add the `archive_override`
from the [release notes](https://github.com/curlewlabs-com/rules_host_cache_key/releases).

The generated platform inherits from `@platforms//host`, so toolchain
resolution sees the same constraints as before. Nothing else changes: the first
build after setup runs every action once under the new key.

## What the key covers

- **The action search path.** Every entry in `/bin`, `/usr/bin` and
  `/usr/local/bin`: its name, mode, link target, and the content of the file it
  resolves to. An unreadable file, such as a setuid `sudo`, contributes its size;
  its bytes belong to the OS or a package, which the next items cover.
- **macOS.** The OS build (`sw_vers`), which stands in for the sealed system
  volume and its libraries; the selected developer directory and its Xcode or
  Command Line Tools version; the shell `/bin/sh` runs.
- **Linux.** The OS release; every installed dpkg package with its architecture
  and version; the alternatives and diversions that pick between packaged files.
- **Homebrew**, in `/opt/homebrew`, `/usr/local` and `/home/linuxbrew/.linuxbrew`
  when present. Each installed keg counts by the sha256 of the bottle it was
  poured from (recorded by Homebrew 6 and later), or by its file contents when
  no bottle is recorded. The `opt/` and `bin/` links count too, since tools and
  library loads resolve through them.

None of it includes a hostname, an install time or an inode, so two machines
with the same tools get the same key.

Not covered:

- Files edited in place without going through dpkg or Homebrew.
- Tools outside the search path, unless you add their directory to
  `search_path`.
- Environment variables. Use `--incompatible_strict_action_env`, or declare
  what an action reads with `--action_env`.
- Linux distributions without dpkg, and Windows. Both fail with an error
  instead of producing a key that covers less than it claims.

## When the key changes

The rule asks Bazel to watch everything it read. When any of it changes, Bazel
recomputes the key at its next command, including in a server that is already
running, and every action whose key changed runs again or comes from a cache
entry made under the same key.

## Comparing two machines

The key and every field it digests are in `key.json`:

```sh
bazel build @host_cache_key//:platform
cat "$(bazel info output_base)"/external/*host_cache_key/key.json
```

Diff two machines' files to see why they do not share results. Machines cloned
from one image share. Machines that installed their Homebrew packages on
different days usually do not, because they poured different bottles.

## Options

All attributes of `host_cache_key` are optional:

| Attribute | Default | Use |
| --- | --- | --- |
| `parent` | `@platforms//host` | Your own host platform, when your build defines one. |
| `search_path` | `/bin`, `/usr/bin`, `/usr/local/bin` | Add directories your actions find tools in. |
| `homebrew_prefixes` | the three standard prefixes | Other Homebrew installations. |
| `exec_property` | `host-cache-key` | The execution property's name. |
| `python` | `python3` | Python 3.9 or later that computes the key. |

Two things to know when your build already uses execution platforms or
properties:

- Bazel uses the first registered execution platform a target's toolchains
  accept. Register `@host_cache_key//:platform` ahead of other platforms that
  would also match, or actions on those platforms go without the key.
- `--remote_default_exec_properties` applies only to platforms that set no
  `exec_properties`. Move those properties into the platform you pass as
  `parent`.

The key describes the machine Bazel runs on. Under remote execution an action
runs elsewhere, so use this for local execution with a disk or remote cache.

## Requirements

- Bazel 9.2.0 is what CI tests on Linux (x86_64, arm64) and macOS (arm64,
  x86_64). The rule needs `repository_ctx.watch`, which Bazel 7.1 introduced.
- Python 3.9 or later on `PATH` when Bazel fetches the repository. On macOS,
  Xcode or the Command Line Tools provide `/usr/bin/python3`.

## Versioning

Releases are 0.x: a 0.x release makes no compatibility promise to an earlier
one. Pin a release by its integrity. A change to what the key covers changes
every host's key once.

## License

[MIT](LICENSE)
