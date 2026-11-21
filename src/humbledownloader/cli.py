import argparse
import logging
import os
import sys

from . import __version__, redact
from .flight import LayoutError
from .run import LibraryLocked, run
from .site import (
    Blocked,
    Challenge,
    CookieFileError,
    RateLimited,
    SessionLost,
    Site,
    SiteError,
    load_session,
)
from .state import StateError

logger = logging.getLogger(__name__)

DEFAULT_EXCLUDE = ["mac", "linux"]


def parse_args(args):
    parser = argparse.ArgumentParser(
        prog="humbledl",
        description="Download your Humble Bundle library from the Humble website.",
    )
    parser.add_argument(
        "--version", action="version", version=f"humbledl {__version__}"
    )
    parser.add_argument(
        "command",
        choices=["list", "download"],
        help="list: show what would be adopted and downloaded (changes nothing); "
        "download: adopt existing files and download the rest",
    )
    parser.add_argument("-l", "--library", required=True, help="library folder")
    parser.add_argument(
        "-c",
        "--cookie-file",
        required=True,
        help="Netscape cookie export of humblebundle.com",
    )
    parser.add_argument(
        "--exclude-platform",
        nargs="*",
        default=DEFAULT_EXCLUDE,
        help="platforms to skip (default: mac linux); "
        "seen values: windows mac linux ebook audio video android image other",
    )
    parser.add_argument(
        "--item",
        help="only items whose title contains this text (all pages are still read)",
    )
    return parser.parse_args(args)


def print_summary(summary, dry_run):
    c = summary.counts
    lines = ["", "Summary:"]
    for label in (
        "skipped",
        "adopted",
        "downloaded",
        "updated",
        "already current",
        "newer than catalog",
        "moved",
        "would move",
        "conflicts",
        "would download",
        "would update",
        "excluded",
        "items without files",
        "failed",
    ):
        if c.get(label):
            lines.append(f"  {label:<20} {c[label]}")
    gb = (summary.pending_bytes if dry_run else summary.downloaded_bytes) / 1e9
    lines.append(f"  {'to download' if dry_run else 'downloaded'}: {gb:.1f} GB")
    logger.info("\n".join(lines))
    if summary.failures:
        logger.error("Failures:\n  " + "\n  ".join(summary.failures))


def main(argv=None):
    args = parse_args(sys.argv[1:] if argv is None else argv)
    redact.install(logging.INFO)
    library = os.path.abspath(args.library)
    if not os.path.isdir(library):
        logger.error("Library folder %s does not exist", library)
        sys.exit(1)
    cookie = os.path.abspath(args.cookie_file)
    if os.path.normcase(cookie).startswith(os.path.normcase(os.path.join(library, ""))):
        logger.warning(
            "Note: the cookie file is inside the library; it travels with any copy "
            "or backup of the library"
        )
    exclude = {p.lower() for p in args.exclude_platform}
    dry_run = args.command == "list"
    try:
        site = Site(load_session(cookie))
        summary = run(library, site, exclude, dry_run=dry_run, only=args.item)
    except CookieFileError as e:
        logger.error("%s. Export the humblebundle.com cookies again.", e)
        sys.exit(1)
    except SessionLost as e:
        logger.error("Session lost (%s). Export the humblebundle.com cookies again.", e)
        sys.exit(1)
    except Blocked as e:
        logger.error(
            "%s: Cloudflare blocked the request. This is not a session problem; new "
            "cookies will not help.",
            e,
        )
        sys.exit(1)
    except Challenge as e:
        logger.error("%s. Try again later; this tool does not solve browser checks.", e)
        sys.exit(1)
    except RateLimited as e:
        logger.error("%s: Humble asks to slow down. Try again later.", e)
        sys.exit(1)
    except (LayoutError, SiteError, StateError, LibraryLocked, OSError) as e:
        logger.error("%s", e)
        sys.exit(1)
    except KeyboardInterrupt:
        logger.error("Interrupted")
        sys.exit(1)
    print_summary(summary, dry_run)
    if summary.counts.get("failed"):
        sys.exit(1)
