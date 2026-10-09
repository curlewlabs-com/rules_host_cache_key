"""An execution platform keyed by the host tools that Bazel actions borrow."""

_BUILD = """\
package(default_visibility = ["//visibility:public"])

# key.json lists what the key covers on the host that computed it.
platform(
    name = "platform",
    parents = ["{parent}"],
    exec_properties = {{"{exec_property}": "{key}"}},
)

exports_files(["key.json"])
"""

def _host_cache_key_impl(rctx):
    python = rctx.attr.python
    interpreter = rctx.path(python) if python.startswith("/") else rctx.which(python)
    if interpreter == None or not interpreter.exists:
        fail("host_cache_key: no Python interpreter at %r; set the `python` attribute" % python)
    command = [interpreter, rctx.path(Label("//:host_key.py"))]
    for directory in rctx.attr.search_path:
        command.extend(["--search-path", directory])
    for prefix in rctx.attr.homebrew_prefixes:
        command.extend(["--homebrew-prefix", prefix])
    result = rctx.execute(command, environment = {"LC_ALL": "C"}, quiet = True)
    if result.return_code != 0:
        fail("host_cache_key: computing the host key failed:\n" + result.stderr)
    report = json.decode(result.stdout)

    # Bazel re-runs this rule, in a running server too, when any watched path
    # changes; that is what keeps the key current after a host update.
    for path in report["watch_files"]:
        rctx.watch(path)
    for path in report["watch_directories"]:
        rctx.path(path).readdir(watch = "yes")

    rctx.file(
        "key.json",
        json.encode_indent({"fields": report["fields"], "key": report["key"]}, indent = "  ") + "\n",
    )
    rctx.file("BUILD.bazel", _BUILD.format(
        exec_property = rctx.attr.exec_property,
        key = report["key"],
        parent = str(rctx.attr.parent),
    ))

host_cache_key = repository_rule(
    implementation = _host_cache_key_impl,
    # Like Bazel's own host autodetection, this describes the local machine.
    configure = True,
    doc = """Generates `:platform`, the host execution platform plus a host key.

The key is a digest of the host tools an action can reach without declaring
them: the files on the action search path, the OS release and its installed
packages, the selected Xcode or Command Line Tools on macOS, and Homebrew's
installed kegs. Register `:platform` as an execution platform and Bazel adds
the key to every action's cache key, so the disk cache or a remote cache
reuses a result only on a host whose tools match the one that produced it.
Hosts with identical tools share results.

`:key.json` records the fields the key digests, for comparing two hosts.
""",
    attrs = {
        "exec_property": attr.string(
            default = "host-cache-key",
            doc = "Name of the execution property that carries the key.",
        ),
        "homebrew_prefixes": attr.string_list(
            default = ["/opt/homebrew", "/usr/local", "/home/linuxbrew/.linuxbrew"],
            doc = "Homebrew prefixes whose installed kegs the key covers. A prefix " +
                  "without a Cellar is skipped, and watched in case one appears.",
        ),
        "parent": attr.label(
            default = "@platforms//host",
            doc = "Platform the generated one inherits constraints and properties " +
                  "from. Name your own when your build registers one.",
        ),
        "python": attr.string(
            default = "python3",
            doc = "Python 3.9 or later that computes the key: a name to find on " +
                  "PATH, or an absolute path.",
        ),
        "search_path": attr.string_list(
            default = ["/bin", "/usr/bin", "/usr/local/bin"],
            doc = "Directories whose entries actions can run without declaring " +
                  "them. The default is the PATH Bazel gives actions under " +
                  "`--incompatible_strict_action_env`; add any directory an " +
                  "`--action_env=PATH` or a rule's absolute tool path reaches.",
        ),
    },
)
