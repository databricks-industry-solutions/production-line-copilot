#!/usr/bin/env python3
"""Render the deep-dive narration and wire it to the film clock.

The film has no audio track. It is a React animation driven by a clock in
`localStorage`, so the voice-over is a sidecar: one MP3 per line, and a player
that watches the clock and plays whichever line belongs to the current moment.
That is what makes seeking work — jump to chapter nine and the narration jumps
with you, because nothing is baked together.

This script owns that sidecar. It reads the script from ``film/vo``, renders
each line with the same neural voice the short film used, measures what came
back, and writes the manifest and player next to the audio.

Timings are the interesting part, and they are computed rather than authored.
Each line declares the ``chapter`` it belongs to and nothing else; this renders
it, measures it, and packs the chapter's lines end to end from the chapter's
own start, leaving ``GAP`` between them. The resulting ``start`` is written back
into the script so the scene reveals have something to align to.

Hand-placed starts were the first design and they failed in a specific way: a
line is only as long as the voice makes it, so a chapter authored to fill sixty
seconds would come back forty seconds long, and the film would sit in silence on
a static frame for the rest of the chapter. Every one of the fourteen chapters
did that. Packing makes that impossible to author by accident — the only way to
leave a hole now is to not write enough lines, which this reports.

The chapter windows live in ``film/src/deep_dive.jsx`` and are read from there,
so the film's own clock stays the single source of truth for when a chapter
starts.

Usage::

    python3 scripts/build_film_vo.py             # render everything
    python3 scripts/build_film_vo.py --timings   # re-pack from existing audio
"""

from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "film/vo/deep_dive.json"
SOURCE = ROOT / "film/src/deep_dive.jsx"
OUT = ROOT / "docs/media/prodline_copilot_deep_dive_vo"

# A beat of silence after a line before the next may start. Without it the
# narration sounds like one continuous sentence across a scene change.
GAP = 0.35

# A chapter opens on its title card rather than talking over it, and closes on
# a held frame rather than cutting mid-breath.
LEAD = 1.5
TAIL = 2.0

# More than this much silence inside a chapter and the film has run out of
# things to say while the viewer is still looking at the frame.
MAX_HOLE = 6.0


def _chapters() -> dict[str, tuple[float, float]]:
    """The chapter windows, read from the film so there is one clock.

    The table in the film is authored from zero and shifted by ``INTRO``, the
    length of the two-minute film that now plays in front of the deep dive.
    Read both, so the starts written back into the script are on the joined
    clock the player actually uses.
    """
    text = SOURCE.read_text()
    block = re.search(r"const DD_LOCAL = \{(.*?)\n\};", text, re.S)
    if not block:
        raise SystemExit(f"no `const DD_LOCAL` chapter table in {SOURCE}")
    intro = re.search(r"^const INTRO = (\d+);", text, re.M)
    if not intro:
        raise SystemExit(f"no `const INTRO` offset in {SOURCE}")
    offset = float(intro.group(1))
    found = re.findall(
        r"id: '([^']+)', start: (\d+), end: (\d+)", block.group(1)
    )
    return {cid: (float(a) + offset, float(b) + offset) for cid, a, b in found}


def _intro_segments() -> list[dict]:
    """The two-minute film's narration, copied in as chapter 0.

    The player resolves an audio file from its own directory, so the intro's
    clips have to live beside the deep dive's rather than be referenced across
    directories. They keep their own timings, which already start at zero.
    """
    src = ROOT / "docs/media/prodline_copilot_film_vo"
    manifest = json.loads((src / "manifest.json").read_text())
    out: list[dict] = []
    for seg in manifest["segments"]:
        sid = f"intro{seg['id']}"
        shutil.copyfile(src / f"{seg['id']}.mp3", OUT / f"{sid}.mp3")
        out.append({**seg, "id": sid, "chapter": "intro"})
    return out


DRIFT_WORTH_REPORTING = 0.4


def _drift_by_chapter(
    rendered: list[dict], windows: dict[str, tuple[float, float]]
) -> list[tuple[str, float]]:
    """How far each chapter's narration moved since the manifest on disk.

    The reveals in ``deep_dive.jsx`` are hand-aligned against these starts, so
    anything that changes a duration — a reworded line, a different voice, a
    pronunciation fix — slides the narration underneath visuals that stay put.
    That has been the source of every timing regression in this film, and it is
    invisible at render time because the render itself succeeds. Reporting the
    per-chapter movement turns it into something you are told rather than
    something you find by watching twenty-two minutes of film.

    Chapter-local drift is what matters: a chapter whose window moved by the same
    amount as its narration has not drifted at all, because the reveals are
    timed from the chapter's own start.
    """
    manifest_path = OUT / "manifest.json"
    if not manifest_path.exists():
        return []
    previous = json.loads(manifest_path.read_text())
    was = {s["id"]: s["start"] for s in previous["segments"]}
    # Written by an earlier build. Absent on the first run after this change, in
    # which case the comparison falls back to film time.
    was_window = previous.get("chapters", {})

    moved: dict[str, float] = {}
    for seg in rendered:
        before = was.get(seg["id"])
        window = windows.get(seg["chapter"])
        if before is None or window is None:
            continue
        then = was_window.get(seg["chapter"], [window[0]])[0]
        # Measured from each chapter's own start, so widening a window — which
        # shifts every chapter after it — does not read as drift.
        delta = (seg["start"] - window[0]) - (before - then)
        if abs(delta) > abs(moved.get(seg["chapter"], 0.0)):
            moved[seg["chapter"]] = delta
    return [(c, d) for c, d in moved.items() if abs(d) >= DRIFT_WORTH_REPORTING]


def _pack(rendered: list[dict], windows: dict[str, tuple[float, float]]) -> list[str]:
    """Lay each chapter's lines end to end from its start. Returns complaints."""
    problems: list[str] = []
    for cid, (start, end) in windows.items():
        lines = [s for s in rendered if s["chapter"] == cid]
        if not lines:
            problems.append(f"{cid}: no narration at all")
            continue
        clock = start + LEAD
        for seg in lines:
            seg["start"] = round(clock, 3)
            clock += seg["audioDuration"] + GAP
        overrun = clock - GAP + TAIL - end
        if overrun > 0:
            problems.append(
                f"{cid}: {overrun:.1f}s too much narration for a "
                f"{end - start:.0f}s chapter — cut a line or widen the chapter"
            )
        hole = end - (clock - GAP)
        if hole > MAX_HOLE:
            problems.append(
                f"{cid}: {hole:.1f}s of silence before the next chapter — "
                f"write another line"
            )
    return problems


# Words the voice says wrong, and the spelling that makes it say them right.
#
# This is applied to the text handed to the synthesiser and nowhere else. The
# script and the manifest keep the real spelling, because they are the record of
# what the film says: "Jemma" in a changelog or a test would be a typo, and the
# tests do read the narration.
#
# Initialisms are handled the other way round — spaced in the script itself
# ("S Q L", "O E E") — because that is also how you want to read them while
# editing a line out loud. This table is only for the words where the written
# form and the speakable form genuinely differ.
SAID_AS = {
    "Gemma": "Jemma",      # otherwise a hard g; the model is jeh-muh
    "LIMS": "Limz",        # a word, not an initialism; spelled out it drags
    "M L flow": "ML-flow",  # the product is one word with the L said, not "M-L flow"
    "MLflow": "ML-flow",
    # The two-minute film predates the spacing convention and still writes these
    # closed up, which the voice reads as words rather than letters.
    "OEE": "O E E",
    "MCP": "M C P",
    "KPI": "K P I",
    "CIP": "C I P",
}

# Spaced capitals get the letters right but leave a pause between each one, so a
# line with two initialisms in it sounds laboured. Hyphenating them keeps the
# spelling-out and takes the pauses away — "C M M S" is half a second shorter as
# "C-M-M-S". SCADA and LIMS are deliberately not in this shape: they are said as
# words, and spelling them would be wrong rather than merely slow.
_SPACED_CAPS = re.compile(r"\b[A-Z](?: [A-Z])+\b")


def _speakable(text: str) -> str:
    for written, said in SAID_AS.items():
        text = re.sub(rf"\b{re.escape(written)}\b", said, text)
    return _SPACED_CAPS.sub(lambda m: m.group(0).replace(" ", "-"), text)


def _render(text: str, voice: str, dest: Path, rate: str | None = None) -> None:
    cmd = ["edge-tts", "--voice", voice, "--text", _speakable(text), "--write-media", str(dest)]
    if rate:
        cmd += ["--rate", rate]
    subprocess.run(cmd, check=True, capture_output=True)


def _duration(path: Path) -> float:
    out = subprocess.run(
        [
            "ffprobe", "-v", "error",
            "-show_entries", "format=duration",
            "-of", "default=noprint_wrappers=1:nokey=1",
            str(path),
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    return round(float(out.stdout.strip()), 3)


SHORT_OUT = ROOT / "docs/media/prodline_copilot_film_vo"

# The short film's lines are pinned to scene beats rather than packed, so a
# re-render has to fit the words into the window it was given instead of moving
# the window. Speaking faster is the only lever; past this much it stops
# sounding like someone talking and starts sounding like a disclaimer.
MAX_SPEEDUP = 0.20
SHORT_GAP = 0.15


def _fit_rate(over: float) -> str:
    """The `--rate` that makes a line `over`× too long fit its window."""
    return f"+{min(round(over * 100) + 2, round(MAX_SPEEDUP * 100))}%"


def rebuild_short_film(voice: str, timings_only: bool) -> int:
    """Re-render the two-minute film's narration, keeping its authored starts.

    The short film predates the deep dive's packer and is timed the other way
    round: each line is anchored to the scene it describes, so the starts are
    the fixed thing and the audio has to fit between them. A voice change moves
    every duration, so each line that no longer fits is re-rendered slightly
    faster rather than allowed to run over the line after it.

    Its `player.js` is hand-maintained and has drifted from the generated one,
    so only the manifest inside it is rewritten — the code is left alone.
    """
    manifest_path = SHORT_OUT / "manifest.json"
    manifest = json.loads(manifest_path.read_text())
    segs = manifest["segments"]
    ext = manifest.get("audioExt", "mp3")

    # The window a line has to fit: up to the next line, or to the end of the
    # film for the last one. The film's own length is the last cue's end.
    film_end = max(s["end"] for s in segs)
    problems: list[str] = []
    for i, seg in enumerate(segs):
        nxt = segs[i + 1]["start"] if i + 1 < len(segs) else film_end
        window = nxt - seg["start"] - SHORT_GAP
        dest = SHORT_OUT / f"{seg['id']}.{ext}"
        if not timings_only:
            print(f"  {seg['id']}  {seg['text'][:60]}…")
            _render(seg["text"], voice, dest)
            dur = _duration(dest)
            if dur > window:
                rate = _fit_rate(dur / window - 1)
                print(f"        {dur:.1f}s into a {window:.1f}s window — re-rendering at {rate}")
                _render(seg["text"], voice, dest, rate=rate)
                dur = _duration(dest)
        else:
            dur = _duration(dest)
        if dur > window:
            problems.append(
                f"{seg['id']}: {dur - window:.1f}s longer than its window even at "
                f"{int(MAX_SPEEDUP * 100)}% faster — shorten the line"
            )
        seg["audioDuration"] = dur
        seg["end"] = round(min(seg["start"] + dur + 0.4, nxt), 3)

    manifest["voice"] = voice
    manifest["version"] = manifest.get("version", 1) + 1
    manifest_path.write_text(json.dumps(manifest, indent=2) + "\n")

    # Surgically swap the cue table inside the hand-maintained player, leaving
    # every line of its logic untouched.
    player_path = SHORT_OUT / "player.js"
    player = player_path.read_text()
    cues = ",\n".join(
        f"      {{ id: '{s['id']}', start: {s['start']}, end: {s['end']} }}" for s in segs
    )
    new_player, n = re.subn(
        r"(    segments: \[\n).*?(\n    \],)",
        lambda m: m.group(1) + cues + m.group(2),
        player,
        count=1,
        flags=re.S,
    )
    if n != 1:
        raise SystemExit(f"could not find the cue table in {player_path}")
    new_player = re.sub(r"(version: )\d+", rf"\g<1>{manifest['version']}", new_player, count=1)
    player_path.write_text(new_player)

    print(f"\n{len(segs)} lines re-voiced as {voice}")
    print(f"wrote {manifest_path.relative_to(ROOT)} and player.js")
    for note in problems:
        print(f"  {note}", file=sys.stderr)
    return 1 if problems else 0


PLAYER_TEMPLATE = """\
/**
 * Synchronized voice-over for the ProdLine CoPilot deep-dive film.
 *
 * GENERATED by scripts/build_film_vo.py from film/vo/deep_dive.json.
 * Edit the script, not this file.
 *
 * Works from file:// (no fetch). The film keeps its playhead in localStorage;
 * this watches that clock and plays whichever line owns the current second, so
 * seeking to a chapter takes the narration with it.
 *
 * Narration: Microsoft neural TTS (%(voice)s) as %(ext)s.
 */
(function voiceoverPlayer() {
  const PERSIST_KEY = '%(persist_key)s';
  const AUDIO_EXT = '%(ext)s';

  const scriptEl =
    document.currentScript || Array.from(document.scripts).find((s) => (s.src || '').includes('player.js'));
  const voBase =
    scriptEl && scriptEl.src
      ? scriptEl.src.replace(/player\\.js(?:\\?.*)?$/, '')
      : new URL('%(dirname)s/', window.location.href).href;

  const MANIFEST = %(manifest)s;

  const cache = new Map();
  let audio = null;
  let activeId = null;
  let unlocked = false;
  let lastTime = 0;
  // Wall clock of the last tick that saw the film's time actually move. This
  // used to be the last tick of *any* kind, which is a different thing: tick()
  // runs on rAF, so the gap was always ~16ms and the "nothing moved recently"
  // test below could never fire. See the note on tick().
  let lastAdvance = performance.now();
  let playing = true;
  let started = false;

  function audioUrl(id) {
    return new URL(`${id}.${AUDIO_EXT}`, voBase).href;
  }

  function preloadAll() {
    for (const seg of MANIFEST.segments) {
      if (cache.has(seg.id)) continue;
      const a = new Audio(audioUrl(seg.id));
      a.preload = 'auto';
      a.load();
      cache.set(seg.id, a);
    }
  }

  function readTime() {
    try {
      const v = parseFloat(localStorage.getItem(PERSIST_KEY + ':t') || '0');
      return Number.isFinite(v) ? v : 0;
    } catch {
      return 0;
    }
  }

  function segmentForTime(t) {
    return MANIFEST.segments.find((s) => t >= s.start && t < s.end) || null;
  }

  function stopAudio() {
    if (!audio) return;
    try {
      audio.pause();
    } catch {}
    audio = null;
    activeId = null;
  }

  const finished = new Set();

  function playSegment(seg, t) {
    if (!unlocked || !seg) {
      if (!seg) stopAudio();
      return;
    }
    const offset = Math.max(0, t - seg.start);
    if (finished.has(seg.id) && offset > 0.3) return;

    if (activeId === seg.id && audio) {
      if (audio.ended || (audio.duration && offset >= audio.duration - 0.05)) {
        finished.add(seg.id);
        return;
      }
      if (Math.abs(audio.currentTime - offset) > 0.25) {
        try {
          audio.currentTime = offset;
        } catch {}
      }
      if (playing && audio.paused) {
        audio.play().catch(() => {});
      }
      return;
    }

    stopAudio();
    activeId = seg.id;
    audio = cache.get(seg.id) || new Audio(audioUrl(seg.id));
    cache.set(seg.id, audio);
    audio.preload = 'auto';

    const startAt = () => {
      if (audio.duration && offset >= audio.duration - 0.05) {
        finished.add(seg.id);
        return;
      }
      try {
        audio.currentTime = offset > 0.02 ? offset : 0;
      } catch {}
      if (playing) {
        audio.play().catch(() => {});
      }
    };

    if (audio.readyState >= 1) startAt();
    else audio.addEventListener('loadedmetadata', startAt, { once: true });

    audio.onended = () => {
      finished.add(seg.id);
      if (activeId === seg.id) activeId = null;
    };
  }

  // There is no pause event to listen for: the film publishes its clock to
  // localStorage and this player infers the rest. "Paused" therefore means the
  // clock has not moved for a moment, which has to be measured from the last
  // time it *did* move — not from the previous frame.
  //
  // Measuring it from the previous frame made pausing loop the last word
  // forever. `playing` stayed true, so playSegment kept running against a
  // frozen offset while the audio element carried on; the moment the element
  // drifted more than 0.25s past that offset the resync below yanked it back,
  // and it re-played the same quarter second for as long as the film sat paused.
  function tick() {
    const t = readTime();
    const now = performance.now();

    if (Math.abs(t - lastTime) > 0.015) {
      playing = true;
      lastAdvance = now;
    } else if (now - lastAdvance > 120) {
      playing = false;
    }
    if (t + 0.05 < lastTime) {
      stopAudio();
      finished.clear();
    }
    lastTime = t;

    if (!unlocked) return;

    const seg = segmentForTime(t);
    if (!seg) {
      if (audio) stopAudio();
      return;
    }
    if (!playing) {
      if (audio) audio.pause();
      return;
    }
    playSegment(seg, t);
  }

  function unlock() {
    if (unlocked) return;
    preloadAll();
    const first = MANIFEST.segments[0];
    if (!first) return;
    const probe = cache.get(first.id) || new Audio(audioUrl(first.id));
    cache.set(first.id, probe);
    probe.volume = 0;
    return probe
      .play()
      .then(() => {
        probe.pause();
        probe.currentTime = 0;
        probe.volume = 1;
        unlocked = true;
        const t = readTime();
        const seg = segmentForTime(t);
        if (seg) playSegment(seg, t);
      })
      .catch(() => {});
  }

  function armUnlock() {
    const tryUnlock = () => {
      unlock();
    };
    document.addEventListener('pointerdown', tryUnlock, { capture: true });
    document.addEventListener('keydown', tryUnlock, { capture: true });
    unlock();
  }

  function loop() {
    if (window.__stage) started = true;
    if (started) tick();
    requestAnimationFrame(loop);
  }

  preloadAll();
  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', () => {
      armUnlock();
      requestAnimationFrame(loop);
    });
  } else {
    armUnlock();
    requestAnimationFrame(loop);
  }
})();
"""


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--timings", action="store_true", help="recheck timings from existing audio")
    ap.add_argument(
        "--only",
        help="comma-separated line ids to re-render; every other line keeps the audio it has. "
        "Re-rendering all of them costs eight minutes, so a wording fix on one line should not.",
    )
    ap.add_argument(
        "--short",
        action="store_true",
        help="rebuild the two-minute film's narration instead (it is the deep dive's intro, "
        "so both have to be the same voice)",
    )
    args = ap.parse_args()

    spec = json.loads(SCRIPT.read_text())
    voice = spec["voice"]
    ext = spec.get("audioExt", "mp3")

    if args.short:
        if not args.timings and not shutil.which("edge-tts"):
            raise SystemExit("edge-tts not on PATH — `uv tool install edge-tts`")
        if not shutil.which("ffprobe"):
            raise SystemExit("ffprobe not on PATH — install ffmpeg")
        return rebuild_short_film(voice, args.timings)

    windows = _chapters()
    segments = spec["segments"]

    unknown = {s["chapter"] for s in segments} - set(windows)
    if unknown:
        raise SystemExit(f"narration for chapters the film does not have: {sorted(unknown)}")

    if not args.timings and not shutil.which("edge-tts"):
        raise SystemExit("edge-tts not on PATH — `uv tool install edge-tts`")
    if not shutil.which("ffprobe"):
        raise SystemExit("ffprobe not on PATH — install ffmpeg")

    OUT.mkdir(parents=True, exist_ok=True)

    only = {i.strip() for i in args.only.split(",")} if args.only else None
    if only:
        unknown = only - {s["id"] for s in segments}
        if unknown:
            raise SystemExit(f"no such line: {sorted(unknown)}")

    rendered = []
    for i, seg in enumerate(segments):
        dest = OUT / f"{seg['id']}.{ext}"
        if not args.timings and (only is None or seg["id"] in only):
            print(f"  {seg['id']}  {seg['text'][:64]}…")
            _render(seg["text"], voice, dest)
        if not dest.exists():
            raise SystemExit(f"{dest} missing — run without --timings first")
        rendered.append({**seg, "audioDuration": _duration(dest)})

    # Measured, so now they can be placed. Packing cannot produce an overlap;
    # what it can produce is a chapter with too much or too little to say.
    clashes = _pack(rendered, windows)
    drift = _drift_by_chapter(rendered, windows)
    rendered.sort(key=lambda s: s["start"])

    for i, seg in enumerate(rendered):
        finish = seg["start"] + seg["audioDuration"]
        nxt = rendered[i + 1]["start"] if i + 1 < len(rendered) else None
        seg["end"] = round(finish + 0.4, 3)
        if nxt is not None:
            seg["end"] = round(min(seg["end"], nxt), 3)

    # The packed starts go back into the script: the scene reveals in
    # deep_dive.jsx are aligned by hand against these numbers, so they have to
    # be readable somewhere other than the generated manifest.
    placed = {s["id"]: s["start"] for s in rendered}
    spec["segments"] = [{**s, "start": placed[s["id"]]} for s in spec["segments"]]
    SCRIPT.write_text(json.dumps(spec, indent=2, ensure_ascii=False) + "\n")

    # Chapter 0 is the two-minute film, already narrated. It is prepended after
    # packing so that its fixed timings are never re-flowed by the packer.
    rendered = _intro_segments() + rendered

    manifest = {
        "version": 1,
        "persistKey": spec["persistKey"],
        "voice": voice,
        "audioExt": ext,
        # Not read by the player. Kept so the next build can tell narration that
        # moved because a line changed from narration that moved because the
        # chapter around it was widened.
        "chapters": {cid: [start, end] for cid, (start, end) in windows.items()},
        "segments": [
            {
                "id": s["id"],
                "start": s["start"],
                "end": s["end"],
                "text": s["text"],
                "audioDuration": s["audioDuration"],
            }
            for s in rendered
        ],
    }
    (OUT / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")

    player_manifest = json.dumps(
        {
            "version": manifest["version"],
            "persistKey": manifest["persistKey"],
            "segments": [
                {"id": s["id"], "start": s["start"], "end": s["end"], "text": s["text"]}
                for s in manifest["segments"]
            ],
        },
        indent=2,
    )
    (OUT / "player.js").write_text(
        PLAYER_TEMPLATE
        % {
            "voice": voice,
            "ext": ext,
            "persist_key": spec["persistKey"],
            "dirname": OUT.name,
            "manifest": player_manifest,
        }
    )

    spoken = sum(s["audioDuration"] for s in rendered)
    film = max(end for _, end in windows.values())
    print(f"\n{len(rendered)} lines, {spoken / 60:.1f} min of narration over {film / 60:.1f} min of film")
    print(f"{100 * spoken / film:.0f}% of the running time is narrated")
    print(f"wrote {OUT.relative_to(ROOT)}/manifest.json and player.js")

    if drift:
        print(
            f"\n{len(drift)} chapter(s) moved against the reveals in "
            f"{SOURCE.relative_to(ROOT)} — check them, or re-time them:"
        )
        for cid, delta in sorted(drift, key=lambda d: -abs(d[1])):
            when = "earlier" if delta < 0 else "later"
            print(f"  {cid}: narration is {abs(delta):.1f}s {when} than it was")

    if clashes:
        print(f"\n{len(clashes)} chapter(s) do not fit their narration:", file=sys.stderr)
        for note in clashes:
            print(f"  {note}", file=sys.stderr)
        return 1
    print("every chapter is spoken for, end to end")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
