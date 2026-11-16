"""Check that a frozen humbledl executable really works, not just that it starts.

Usage: python packaging/smoke_test.py <path-to-executable> <expected-version>

CI has no Humble account and must not reach the network, so the last check sends
the first request to a proxy on a closed local port. Reaching that proxy proves
what freezing tends to break: requests, urllib3 and ssl import, and certifi's CA
bundle is found (requests checks it before it connects, and a missing bundle
fails with a different message).
"""

import os
import subprocess
import sys
import tempfile
from pathlib import Path

# A made-up session cookie in the Netscape export format the tool reads.
COOKIES = (
    "# Netscape HTTP Cookie File\n"
    "www.humblebundle.com\tFALSE\t/\tTRUE\t0\tory_session_smoke\tnot-a-real-value\n"
)


def run(*args, env=None, expect=0):
    result = subprocess.run(
        args, check=False, capture_output=True, text=True, timeout=180, env=env
    )
    output = result.stdout + result.stderr
    if result.returncode != expect:
        sys.exit(
            f"FAIL: {args} exited {result.returncode}, expected {expect}\n{output}"
        )
    return output


def main():
    # Resolved because Windows will not run a relative path like dist/humbledl.exe.
    exe, version = str(Path(sys.argv[1]).resolve()), sys.argv[2]

    reported = run(exe, "--version").strip()
    if reported != f"humbledl {version}":
        sys.exit(f"FAIL: --version printed {reported!r}, expected 'humbledl {version}'")
    print(f"ok: {reported}")

    if "--exclude-platform" not in run(exe, "--help"):
        sys.exit("FAIL: --help does not list --exclude-platform")
    print("ok: --help")

    with tempfile.TemporaryDirectory() as tmp:
        library, cookies = Path(tmp, "library"), Path(tmp, "cookies.txt")
        library.mkdir()

        cookies.write_text("not a cookie export\n", encoding="utf-8")
        out = run(exe, "list", "-l", str(library), "-c", str(cookies), expect=1)
        if "Export the humblebundle.com cookies again" not in out:
            sys.exit(f"FAIL: a broken cookie file gave no hint\n{out}")
        print("ok: broken cookie file refused")

        cookies.write_text(COOKIES, encoding="utf-8")
        env = {k: v for k, v in os.environ.items() if k.lower() != "no_proxy"}
        env["HTTPS_PROXY"] = env["https_proxy"] = "http://127.0.0.1:9"
        out = run(
            exe, "list", "-l", str(library), "-c", str(cookies), env=env, expect=1
        )
        if "ProxyError" not in out:
            sys.exit(f"FAIL: the first request did not reach the proxy\n{out}")
        print("ok: requests, ssl and the CA bundle load (request stopped at the proxy)")


if __name__ == "__main__":
    main()
