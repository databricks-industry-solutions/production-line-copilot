#!/usr/bin/env python3
"""Rebuild the bundled film HTML from the film source in ``film/src``.

The film ships as one self-contained HTML file so it plays from ``file://``
with no server, no build step and no network — you can mail it to someone. The
cost of that is the shape of the artefact: the JSX lives gzipped and
base64-encoded inside a ``<script type="__bundler/manifest">`` block, next to
two dozen embedded font binaries and a generated runtime that boots React.

That made the film unmaintainable in a specific way. The only copy of the
source was the blob, so the only way to change anything was string replacement
against decompressed JavaScript — fine for retitling a panel, hopeless for
writing a scene. It also meant nobody who cloned the repository could rebuild
the film at all, which for a public accelerator is a defect on its own.

So the source moved to ``film/src/movie.jsx`` and this script puts it back into
the bundle. The bundle is now a build output. Edit the source, run this, play
the HTML.

Only the film entry is rewritten. The runtime is generated upstream and marked
do-not-edit, and the fonts are binary payloads; both are copied through
untouched, which is why this rewrites one manifest entry rather than rebuilding
the file.

Usage::

    python3 scripts/build_film.py            # rebuild the HTML from source
    python3 scripts/build_film.py --check    # verify they already agree
"""

from __future__ import annotations

import argparse
import base64
import gzip
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "film/src/movie.jsx"
HTML = ROOT / "docs/media/prodline_copilot_film.html"

# The long film, which is optional.
#
# Its scenes are appended to the short film's source rather than importing from
# it, so it inherits every component, colour and easing curve and the two cannot
# drift apart. Its HTML is derived from the short film's, so they also share the
# runtime and the embedded fonts.
#
# Optional is the important part. The deep dive is an internal asset: a tree
# without ``film/src/deep_dive.jsx`` builds and checks a complete short film and
# says nothing about a second one. That is what makes it safe to drop the deep
# dive from a published copy of this repository without leaving the build in a
# state that fails for whoever clones it.
DEEP_DIVE_SOURCE = ROOT / "film/src/deep_dive.jsx"
DEEP_DIVE_HTML = ROOT / "docs/media/prodline_copilot_deep_dive.html"
DEEP_DIVE_SWAPS = (
    # The mount point, inside the JSON-escaped inner document.
    (r'component=\"Movie\"', r'component=\"DeepDive\"'),
    # Its own narration, so the two films do not read each other's cues.
    ("prodline_copilot_film_vo/player.js", "prodline_copilot_deep_dive_vo/player.js"),
)

# The film. The sibling entry (da97f8f5…) is the generated dc-runtime that loads
# React and boots the stage; the remaining two dozen are fonts. Pinned by id
# because "the biggest JavaScript entry" would silently retarget the day the
# runtime grows past the film.
APP_ENTRY = "933fab98-f06c-4b3d-be81-0c9cc5232254"

_MANIFEST_RE = re.compile(r'(<script type="__bundler/manifest">\s*)(\{.*?\})(\s*</script>)', re.S)


def _load_manifest(html: str) -> tuple[dict, re.Match[str]]:
    match = _MANIFEST_RE.search(html)
    if not match:
        raise SystemExit(f"{HTML}: no __bundler/manifest block — is this still a bundled film?")
    manifest = json.loads(match.group(2))
    if APP_ENTRY not in manifest:
        raise SystemExit(f"{HTML}: manifest has no entry {APP_ENTRY}")
    return manifest, match


def embedded_source(html: str) -> str:
    """The film JSX currently inside ``html``."""
    manifest, _ = _load_manifest(html)
    return gzip.decompress(base64.b64decode(manifest[APP_ENTRY]["data"])).decode("utf-8")


def _comparable(html: str) -> tuple[str, str]:
    """``html`` split into (the film source it carries, everything else).

    Two bundles are the same film when both halves match. The payload is
    compared as decompressed text and never as bytes, because gzip stamps an OS
    byte that CPython changed between releases: the same source compressed under
    3.9 and under 3.11 differs by exactly one byte, with identical contents.
    Comparing the encoded bytes made ``--check`` report a freshly built film as
    stale depending on which interpreter happened to run it, and made a rebuild
    flip that byte back and forth on a repository shared by two developers.
    """
    manifest, match = _load_manifest(html)
    source = gzip.decompress(base64.b64decode(manifest[APP_ENTRY]["data"])).decode("utf-8")
    # Blank the payload so the rest of the manifest — and the document around it,
    # which is what the deep dive's swaps edit — is still compared exactly.
    manifest[APP_ENTRY]["data"] = ""
    rest = html[: match.start(2)] + json.dumps(manifest, separators=(",", ":")) + html[match.end(2) :]
    return source, rest


def is_current(path: Path, want: str) -> bool:
    """Whether ``path`` already holds the film ``want`` describes."""
    return path.exists() and _comparable(path.read_text()) == _comparable(want)


def _encode(source: str) -> str:
    # mtime=0 so a rebuild from unchanged source produces unchanged bytes.
    # Without it every run rewrites the 747 KB file and the diff is noise. The
    # OS byte is not ours to pin (see _comparable), so freshness is judged on
    # content rather than on these bytes.
    return base64.b64encode(gzip.compress(source.encode("utf-8"), mtime=0)).decode("ascii")


def rebuild(source: str, html: str) -> str:
    manifest, match = _load_manifest(html)
    manifest[APP_ENTRY]["data"] = _encode(source)
    return html[: match.start(2)] + json.dumps(manifest, separators=(",", ":")) + html[match.end(2) :]


def deep_dive_source(short_film_source: str) -> str:
    """The deep dive's bundle source: the short film, then its own scenes."""
    return short_film_source.rstrip() + "\n\n" + DEEP_DIVE_SOURCE.read_text()


def derive_deep_dive(film_html: str, source: str) -> str:
    """The long film: the short film's HTML, re-pointed and re-bundled."""
    out = film_html
    for old, new in DEEP_DIVE_SWAPS:
        if out.count(old) != 1:
            raise SystemExit(
                f"expected exactly one {old!r} to swap for the deep dive, found {out.count(old)}"
            )
        out = out.replace(old, new)
    return rebuild(source, out)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument(
        "--check",
        action="store_true",
        help="exit non-zero if the bundle does not already match the source",
    )
    args = ap.parse_args()

    if not SOURCE.exists():
        raise SystemExit(f"{SOURCE}: missing — extract it from the bundle before building")

    source = SOURCE.read_text()
    html = HTML.read_text()

    # (output path, source that belongs in it). The deep dive only joins the
    # list when its source is present.
    targets = [(HTML, source, rebuild(source, html))]
    if DEEP_DIVE_SOURCE.exists():
        long_source = deep_dive_source(source)
        targets.append((DEEP_DIVE_HTML, long_source, derive_deep_dive(html, long_source)))

    what = "both films" if len(targets) > 1 else "the film"
    # The deep dive's bundle is the short film's source with its own scenes
    # appended, so naming only movie.jsx would hide half of what was built from.
    from_what = (
        f"{SOURCE.relative_to(ROOT)} and {DEEP_DIVE_SOURCE.relative_to(ROOT)}"
        if len(targets) > 1
        else str(SOURCE.relative_to(ROOT))
    )

    if args.check:
        stale = [path.relative_to(ROOT) for path, _, want in targets if not is_current(path, want)]
        if not stale:
            print(f"{what} up to date with {from_what}")
            return 0
        joined = ", ".join(str(p) for p in stale)
        print(f"stale: {joined} — run `python3 scripts/build_film.py`", file=sys.stderr)
        return 1

    # Round-trip before writing: a bundle that cannot be read back is worse than
    # one that was never rebuilt, and these files are the deliverable.
    for path, want_source, built in targets:
        if embedded_source(built) != want_source:
            raise SystemExit(f"{path.name} did not round-trip — refusing to write")

    wrote = []
    for path, _, want in targets:
        if is_current(path, want):
            continue
        path.write_text(want)
        wrote.append(path.relative_to(ROOT))

    if not wrote:
        print(f"{what} already current")
        return 0
    print(f"Rebuilt {', '.join(str(p) for p in wrote)} from {from_what}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
