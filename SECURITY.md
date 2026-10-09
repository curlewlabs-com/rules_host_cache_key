# Security policy

Report suspected vulnerabilities privately using
[GitHub private vulnerability reporting](https://github.com/curlewlabs-com/rules_host_cache_key/security/advisories/new).
Include the affected release or revision, the host OS and Bazel version,
reproduction steps, and the observed impact.

Curlew Labs maintains this repository; Jeffrey Wall
([jeffwall-curlewlabs](https://github.com/jeffwall-curlewlabs)) is the release
and security contact through GitHub. Reports are reviewed on a best-effort
basis without a guaranteed response time. Fixes target the latest release.

## Trust boundary

The repository rule runs a Python script on the host when Bazel fetches it.
The script reads host metadata and executes `sw_vers`, `xcode-select`,
`pkgutil` or `dpkg-query`; it writes only inside the repository.

`key.json` lists the host's installed packages, Homebrew kegs and tool paths.
Treat it like any build log from that machine before sharing it.

The key keeps results apart by host tools. It is not authentication: anyone
who can write to a shared cache can still write any result under any key.
