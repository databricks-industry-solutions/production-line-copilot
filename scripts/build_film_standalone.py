#!/usr/bin/env python3
"""Fold a film and its narration into one HTML file you can send someone.

The films ship as an HTML bundle plus a sidecar directory of MP3s, because that
is what makes seeking work: the animation keeps its playhead in ``localStorage``
and a small player watches that clock and plays whichever line owns the current
second. It is the right shape for the repository and the wrong shape for email.
A customer who is sent two things will open the one that does not talk.

This writes a third artefact for that case: the same film with the audio inlined
as ``data:`` URIs, so a single file plays offline with narration, the chapter
agenda and the skip controls intact. Base64 costs a third in size, which is
still a fraction of the rendered video and keeps what the video cannot — the
ability to jump to chapter nine.

It is a *derived* artefact and is written to ``output/`` rather than
``docs/media/``. Nothing should ever depend on it: rebuild it when you need to
send it.

Usage::

    python3 scripts/build_film_standalone.py                 # the deep dive
    python3 scripts/build_film_standalone.py --short         # the two-minute film
    python3 scripts/build_film_standalone.py --out /tmp/x.html
"""

from __future__ import annotations

import argparse
import base64
import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MEDIA = ROOT / "docs/media"

FILMS = {
    "deep": ("prodline_copilot_deep_dive.html", "prodline_copilot_deep_dive_vo"),
    "short": ("prodline_copilot_film.html", "prodline_copilot_film_vo"),
}


def _inline_audio(player: str, vo: Path, ext: str) -> str:
    """Give the player its clips as data URIs instead of a directory to fetch."""
    manifest = json.loads((vo / "manifest.json").read_text())
    clips = {}
    for seg in manifest["segments"]:
        clip = vo / f"{seg['id']}.{ext}"
        if not clip.exists():
            raise SystemExit(f"{clip} is missing — rebuild the narration first")
        encoded = base64.b64encode(clip.read_bytes()).decode("ascii")
        clips[seg["id"]] = f"data:audio/{ext};base64,{encoded}"

    # `audioUrl` is the single place the player turns a line id into something
    # an <audio> element can load, so it is the only thing that has to change.
    patched, n = re.subn(
        r"function audioUrl\(id\) \{.*?\n  \}",
        "function audioUrl(id) {\n    return AUDIO[id];\n  }",
        player,
        count=1,
        flags=re.S,
    )
    if n != 1:
        raise SystemExit("could not find audioUrl() in the player")

    table = "  const AUDIO = " + json.dumps(clips) + ";\n"
    marker = "  const MANIFEST = {"
    if marker not in patched:
        raise SystemExit("could not find the manifest in the player")
    return patched.replace(marker, table + marker, 1)


def _inline_player(html: str, player: str, vo_name: str) -> str:
    """Swap the loader's `src` for the player's own text.

    The bundle replaces the whole document as it unpacks, so the player cannot
    simply be appended to the body — it has to go in through the same hook the
    fetched one used, which runs after the film has mounted.
    """
    loader = re.search(
        r"( *)const s = document\.createElement\('script'\);\n"
        r" *s\.src = new URL\('" + re.escape(vo_name) + r"/player\.js'[^\n]*\n"
        r"( *s\.async = true;\n)?"
        r" *document\.head\.appendChild\(s\);",
        html,
    )
    if not loader:
        raise SystemExit(f"could not find the {vo_name} loader in the film")

    indent = loader.group(1)
    # json.dumps gives a safe JS string literal; the only thing it does not
    # escape is a closing tag, which would end the script this sits inside.
    literal = json.dumps(player).replace("</", "<\\/")
    replacement = (
        f"{indent}const s = document.createElement('script');\n"
        f"{indent}s.textContent = {literal};\n"
        f"{indent}document.head.appendChild(s);"
    )
    return html[: loader.start()] + replacement + html[loader.end() :]


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--short", action="store_true", help="the two-minute film")
    ap.add_argument("--out", type=Path, help="where to write it")
    args = ap.parse_args()

    name, vo_name = FILMS["short" if args.short else "deep"]
    html_path, vo = MEDIA / name, MEDIA / vo_name
    for path in (html_path, vo / "player.js", vo / "manifest.json"):
        if not path.exists():
            raise SystemExit(f"{path.relative_to(ROOT)} is missing")

    ext = json.loads((vo / "manifest.json").read_text()).get("audioExt", "mp3")
    player = _inline_audio((vo / "player.js").read_text(), vo, ext)
    standalone = _inline_player(html_path.read_text(), player, vo_name)

    out = args.out or ROOT / "output" / name.replace(".html", "_standalone.html")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(standalone)

    was = html_path.stat().st_size + sum(p.stat().st_size for p in vo.glob(f"*.{ext}"))
    print(f"wrote {out}")
    print(f"{out.stat().st_size / 1e6:.1f} MB, one file (was {was / 1e6:.1f} MB across a file and a folder)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
