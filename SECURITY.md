# Security policy

This repository is [no longer maintained](README.md#no-longer-maintained).
Reports are no longer acted on, and no further releases or fixes are planned.

## Trust boundary

The repository rule runs a Python script on the host when Bazel fetches it.
The script reads host metadata and executes `sw_vers`, `xcode-select`,
`pkgutil` or `dpkg-query`; it writes only inside the repository.

`key.json` lists the host's installed packages, Homebrew kegs and tool paths.
Treat it like any build log from that machine before sharing it.

The key keeps results apart by host tools. It is not authentication: anyone
who can write to a shared cache can still write any result under any key.
