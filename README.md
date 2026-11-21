# humbledownloader

`humbledl` downloads the DRM-free files of your own Humble Bundle library from Humble's redesigned (2026) website. It is a successor in spirit of the archived
[xtream1101/humblebundle-downloader](https://github.com/xtream1101/humblebundle-downloader) (`hbd`), whose JSON API no longer exists. It can take over an
existing download folder with the layout `<bundle>/<product>/<file>` (the layout `hbd` created) without downloading those files again.

## Disclaimer

This is an unofficial tool. It is not affiliated with, endorsed by or supported by Humble Bundle. It reads the website's current page structure, so it may stop
working whenever Humble changes the site. It reports an error instead of guessing: an item page it cannot read is counted as failed, and a library page it
cannot read stops the run. Use it only to download content you own.

## Requirements

- None for the ready-made executables. To install from source: Python 3.15 or newer. uv installs it automatically; with pip or pipx, install it first
  (`uv python install 3.15` or python.org).
- [uv](https://docs.astral.sh/uv/) is recommended. `pipx` or a plain `pip install` work too (the package builds with the standard PEP 517 `uv_build` backend).
- A Humble Bundle account and a browser in which you are logged in to humblebundle.com.
- Developed and used on Windows. CI also runs the tests on Linux; Linux and macOS have not been tried against the real site yet.

## Installation

### Download a ready-made executable

Each [release](https://github.com/navara/humbledownloader/releases) has a standalone `humbledl` for Windows (x64), Linux (x64) and macOS (Apple silicon).
It needs no Python: unpack the archive and put `humbledl` (`humbledl.exe` on Windows) somewhere on your `PATH`. `SHA256SUMS` in the release lists the
checksum of every file.

The executables are not code-signed. Windows SmartScreen may warn on first run ("More info", then "Run anyway"). On macOS, clear the download quarantine
once:

```sh
xattr -d com.apple.quarantine humbledl
```

### With uv, pipx or pip

Install the command from GitHub:

```sh
uv tool install git+https://github.com/navara/humbledownloader
```

Or from a local clone:

```sh
git clone https://github.com/navara/humbledownloader
uv tool install ./humbledownloader
```

Upgrading:

```sh
uv tool upgrade humbledownloader                  # installed from GitHub
uv tool install --reinstall ./humbledownloader    # from a local clone, after git pull
```

Without uv: `pipx install git+https://github.com/navara/humbledownloader` or `pip install git+https://github.com/navara/humbledownloader`.

After installation the command `humbledl` is on your PATH. `humbledl --help` lists the options.

## Getting your session cookie

The tool logs in with your browser's session, not with your password.

1. Log in to humblebundle.com in your browser.
2. With a browser extension that exports cookies in the Netscape `cookies.txt` format, export the cookies of humblebundle.com only (not of all sites). The
   export must include HttpOnly and session cookies; the cookie the tool needs is HttpOnly, so it does not show up in `document.cookie` in the developer tools.
3. Check the file: it must contain one line with `ory_session_` in it.
4. Pass that file with `--cookie-file`.

The tool uses exactly one cookie from that file: the one whose name starts with `ory_session_`. All other cookies in the file are ignored and never sent. The
file must contain exactly one such cookie, otherwise the tool stops and asks you to export again.

**This cookie is a login credential.** Anyone who has it can act as you on humblebundle.com until the session ends. Keep the file private: store it outside
shared, synced or backed-up folders, and never commit it (the repository's `.gitignore` ignores `*cookie*.txt`, but do not rely on that). If the cookie file is
inside the library folder, the tool prints a warning, because it would travel with every copy or backup of the library.

When the session has expired or you were logged out, the run stops with exit code 1 and prints:

```
Session lost (...). Export the humblebundle.com cookies again.
```

Log in again in the browser and export a fresh cookie file.

A cookie file that is missing, unreadable or does not hold exactly one `ory_session_` cookie stops the run with exit code 1 and a message naming the
file, before anything is sent to the site.

## Usage

```
humbledl [-h] -l LIBRARY -c COOKIE_FILE [--exclude-platform [EXCLUDE_PLATFORM ...]] [--item ITEM] {list,download}
```

| Command or option | Default | Meaning |
|---|---|---|
| `list` | | Dry run: reads the site and shows what `download` would do. Writes nothing. |
| `download` | | Adopt existing files, move renamed ones, download the rest. |
| `-l`, `--library` | required | The library folder. It must already exist (otherwise exit 1). |
| `-c`, `--cookie-file` | required | Netscape cookie export of humblebundle.com (see above). |
| `--exclude-platform` | `mac linux` | Platforms to skip. Give the option with no values to exclude nothing. |
| `--item` | none | Only items whose title contains this text (case-insensitive). All library pages are still read. No match: exit 1. |

Platform values seen on the site: `windows mac linux ebook audio video android image other`.

Put `list` or `download` before the options, as in the examples below: `--exclude-platform` takes any number of values, so a command written right
after it would be read as a platform name.

Recommended first run: `list`, then read the output and the summary before running `download`. With an empty library folder, `list` shows
mostly "would download" and "excluded".

Windows:

```bat
humbledl list -l C:\Users\me\Humble -c C:\Users\me\private\humble-cookies.txt
humbledl download -l C:\Users\me\Humble -c C:\Users\me\private\humble-cookies.txt
humbledl download -l C:\Users\me\Humble -c C:\Users\me\private\humble-cookies.txt --exclude-platform mac linux android
```

Linux and macOS (not tested yet):

```sh
humbledl list -l ~/Humble -c ~/.private/humble-cookies.txt
humbledl download -l ~/Humble -c ~/.private/humble-cookies.txt --item "Some Bundle"
humbledl download -l ~/Humble -c ~/.private/humble-cookies.txt --exclude-platform    # all platforms, mac and linux included
```

Progress, warnings and the summary go to standard error. Wrong or missing options exit with code 2.

Example of the end of a `list` run (made-up numbers):

```
Summary:
  adopted              1520
  would download       310
  would update         12
  excluded             240
  items without files  9
  to download: 98.4 GB
```

## Summary counters

| Counter | Meaning |
|---|---|
| skipped | Already tracked in the state file and unchanged on disk. |
| adopted | Found at its target path with the catalog size and recorded without downloading (in `list`: would be adopted). |
| downloaded / would download | New files fetched (or to fetch). |
| updated / would update | A different file was at the target path; the new version replaced it and the old one was kept aside. |
| already current | The file on disk is proven to be what the server serves (before or after downloading it), and was left untouched. |
| newer than catalog | Humble serves a newer file than its catalog lists, and the download was proven complete (see below). |
| moved / would move | A tracked file whose folder name changed was moved to the new path. |
| conflicts | Two catalog files would land on the same path. |
| excluded | Files of excluded platforms. |
| items without files | Items that list no downloadable files (key-only items, messages). |
| failed | Errors; each one is listed again under "Failures". |

## What it does

**What is downloaded.** Every item on the Bundles tab of your library plus every DRM-free item on the Purchased tab. Each item's page lists its files; only
entitlements the site offers as DRM-free downloads are used. Expired entries, entries outside their availability window, and files without a usable name, size
or md5 are skipped with a warning. Torrent links are ignored.

**Folder layout.** Files are stored as `<library>/<bundle>/<product>/<file>`, with the bundle and product titles cleaned the same way `hbd` cleaned them (`+`
becomes `_`, `:` becomes ` -`, only letters and digits of any script, spaces and `_ . - [ ]` are kept, leading and trailing spaces and trailing dots are
removed). Store purchases titled "X (steam)" or "X (download)" are stored under "X". File names are used as Humble lists them. Names Windows cannot use
(reserved names such as `CON`, illegal characters) and paths that, with the temporary `.part` suffix, reach 260 characters are reported as failures, on every
operating system, so that the library stays usable on Windows. When two different files would land on the same path, this is reported as a conflict; if the file
on disk has the size of exactly one of them, it is kept as that one, otherwise none of them is downloaded.

**State file.** The tool records what it has in `<library>/.humble-state.json`: for every file its path relative to the library, Humble's entitlement and
download ids, the catalog's size and md5, whether it was verified, and whether it was downloaded or adopted. The state is saved after each file. It does not
read `hbd`'s `.cache.json`.

**Adopting existing files.** A file that is not in the state file but already exists at its target path with exactly the size the catalog lists is adopted:
recorded as `adopted` and not downloaded. Existing files are not hashed, so adoption trusts the size match.

**Verification.** Every download goes to `<file>.part` and is checked against the catalog's size and md5 before it gets its final name. A failed or interrupted
download removes its `.part` file. A failed download is not retried in the same run, except once with a fresh link when the signed link has expired; run
`download` again to retry the failed files.

**Newer than catalog.** Humble's catalog is sometimes stale: the download server can serve a newer file than the size and md5 the item page lists. Such a
download is accepted only when it is non-empty, the `Content-Length` header equals the bytes received, and the server's own checksum matches them: a strong
single-part `ETag` (32 lowercase hex digits) equal to their md5, a multipart `ETag` (`"<32 hex>-<N>"`) equal to the md5 of the md5s of their 5 MiB parts, or
an `x-amz-meta-md5chksum` header equal to their md5. It is reported as "newer than catalog", and the state file records the real size and md5 (`disk_size`,
`disk_md5`) next to the catalog's. Without that proof the download is counted as failed and the file on disk stays as it is. A file the server holds as an empty
object is reported as such and counted as failed.

**Updated files.** When something else than the tracked or catalog file is at the target path and it has exactly the size the server announces, the tool
hashes it first: if the server's checksum (as above) proves it is the file the server serves, nothing is downloaded and the file is recorded as it is ("already
current"). Otherwise the new version is downloaded and verified first. If it is byte-identical to the file on disk, that file is kept untouched ("already
current"). Otherwise the old file is renamed to `<name>_old-<YYYYMMDD><ext>`, where
the date is the old file's own modification date, so the name says which version it is (`-1`, `-2` and so on are appended if that name exists). The new file
then takes its place. If installing the new file fails, the old one is put back.

**Renamed folders.** If Humble renames a bundle or product so that a tracked file's folder name changes, the file is moved to the new path (only when nothing is
there yet) and the move is appended to `<library>/.humble-moves.log` as `old path<TAB>new path`, so it can be undone by hand.

**Never deletes your files.** Old versions are renamed aside, moves never replace an existing file, and files the tool does not know about are left alone. The
only files it removes are its own `.part` files and its lock file.

**Excluded platforms.** Files for excluded platforms (by default `mac` and `linux`) are counted as "excluded" and neither adopted nor downloaded.

**Exit code.** 0 when nothing failed, 1 otherwise (conflicts alone do not fail a run). After three items in a row whose downloads all failed, the run stops. If
the run stops early (session lost, rate limit, browser check, Cloudflare block, lock, unreadable state file, Ctrl+C), it prints the reason and exits 1
without a summary; files finished so far stay recorded.

**Being polite to the site.** Requests to www.humblebundle.com are paced at about one per second. Requests that meet server errors or HTTP 429 are tried up to
three times, honouring `Retry-After` up to 300 seconds. If Humble keeps asking to slow down, or shows a Cloudflare browser check, the run stops with exit
code 1. The tool does not try to work around bot detection; try again later.

**One run at a time.** `download` creates `<library>/.humble-lock` and refuses to start while that file exists. If a run was killed, delete the file by hand.
`list` takes no lock and writes nothing.

## Troubleshooting

- **"Session lost"**: export the cookie file again (see above).
- **"Cloudflare blocked the request"**: Cloudflare's firewall refused a request although your session is valid. New cookies do not help; the tool does not try
  to get around the block.
- **Lock file**: a run that was killed leaves `<library>/.humble-lock`; delete it when no other run is active.
- **State file cannot be read**: the run stops and leaves `.humble-state.json` untouched. If the previous save was interrupted, the newest state may be in
  `.humble-state.json.tmp`. To start over, move the state file away; existing files are then adopted again by size.
- **Many "failed" lines for one bundle**: check the reason after each file name. Network errors and expired links clear up on the next run.
- **"the CDN serves an empty file"**: Humble's own copy of that file is broken (the download server holds it as 0 bytes). No tool can fetch it; report it
  to Humble support. It keeps failing (exit code 1) until Humble fixes it.

## Privacy and safety

- The session cookie is sent only to humblebundle.com. Downloads use the signed links Humble returns (currently on cdn.humble.com) and are fetched without any
  cookies.
- Logs, tracebacks and warnings are redacted before they are printed: cookie values, the query strings of signed download links (which carry an access token),
  key fields and anything shaped like a product key (three or more dash-joined groups of 3-6 uppercase letters and digits, with at least one letter) are
  replaced with `<redacted>`. The state file holds no cookies, links or keys.
- The tool reads library pages and calls exactly two of the site's actions: listing the Purchased tab and signing download links. It never redeems or reveals
  keys, never gifts, and never calls payment, membership or any other site action. From Purchased rows it reads only the row type and item id; key data is
  discarded.

## Limitations

- Depends on the current structure of humblebundle.com; a site change can break it until the tool is updated.
- Vault items are not supported.
- Interrupted downloads are not resumed; they start again on the next run.
- Only DRM-free downloads are handled; Steam and other keys are not touched.
- No hashing of existing files: an adopted file with the right size but wrong content is not detected.
- `list` only prints; it does not write a catalog file.
- No browser-based fallback if plain requests are blocked.

## Development

```sh
git clone https://github.com/navara/humbledownloader
cd humbledownloader
uv sync                         # creates .venv with the package and the dev tools (pytest, ruff)
uv run pytest                   # unit tests
uv run ruff check .             # lint
uv run ruff format --check .    # formatting
```

The tests use fakes and scrubbed fixtures; they never touch the network or a real Humble account. CI runs lint and tests on Linux and Windows.

### Releasing

A release is cut by pushing a version tag; pushing to `main` publishes nothing.

1. Bump the version in `pyproject.toml` and `src/humbledownloader/__init__.py` (a test checks they agree), run `uv lock`, and commit.
2. Tag that commit and push the tag:

   ```sh
   git tag v0.1.0
   git push origin v0.1.0
   ```

The [release workflow](.github/workflows/release.yml) refuses a tag that does not match the `pyproject.toml` version and runs the full CI matrix. It then
builds the executables with PyInstaller (`packaging/launcher.py`) on Windows, Linux and macOS and runs `packaging/smoke_test.py` against each one: it checks
`--version`, `--help` and the cookie file error, then sends the first request to a closed local proxy, which proves that HTTPS support and the CA bundle
survived freezing without contacting Humble. Only when all of that passes does it publish the GitHub release, with the archives, the wheel, the sdist,
`SHA256SUMS` and generated release notes. A failed run publishes nothing, so after a fix delete the tag, re-tag and push again.

To prove the builds without releasing, run the workflow by hand from `main` (`gh workflow run release.yml`): it does everything except publish.

## License

MIT, see [LICENSE](LICENSE).
