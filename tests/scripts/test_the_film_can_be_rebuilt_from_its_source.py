"""The film is a build output, and has to stay one.

For most of its life the film was only a bundle: thousands of lines of JSX
gzipped inside an HTML file, editable solely by string replacement against
decompressed JavaScript. Extracting the source fixed that, but only for as long
as the two stay in step — a bundle rebuilt from stale source, or source edited
without a rebuild, puts the repository back where it started except now there
are two copies to disagree.

These pin the properties that keep ``film/src/movie.jsx`` the master copy: the
bundle can be read back, it matches the source, and a rebuild is deterministic
so a three-quarter-megabyte artefact does not churn on every run — including
across Python versions, which do not agree on what a gzip header looks like.
"""

from __future__ import annotations

import base64
import importlib.util
import json
import re
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
SOURCE = ROOT / "film/src/movie.jsx"
HTML = ROOT / "docs/media/prodline_copilot_film.html"
BUILDER = ROOT / "scripts/build_film.py"


def _builder():
    spec = importlib.util.spec_from_file_location("build_film", BUILDER)
    module = importlib.util.module_from_spec(spec)
    sys.modules["build_film"] = module
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def build_film():
    return _builder()


def test_the_source_is_in_the_repository():
    # The whole point of the extraction. A missing source file means the bundle
    # has quietly become the master copy again.
    assert SOURCE.exists(), f"{SOURCE} is missing — the film is unmaintainable without it"
    assert len(SOURCE.read_text().splitlines()) > 500


def test_the_bundle_still_carries_a_readable_film(build_film):
    source = build_film.embedded_source(HTML.read_text())
    assert "function Movie(" in source
    assert "PlaybackBar" in source


def test_the_bundle_matches_the_source(build_film):
    # The check the pre-push hook runs. Failing here means someone edited
    # film/src/movie.jsx and shipped the old film.
    assert build_film.embedded_source(HTML.read_text()) == SOURCE.read_text(), (
        "docs/media/prodline_copilot_film.html is stale — run `python3 scripts/build_film.py`"
    )


def test_rebuilding_is_deterministic(build_film):
    # gzip stamps an mtime by default, so without pinning it every rebuild
    # rewrites the file with an identical payload and a different checksum.
    source = SOURCE.read_text()
    html = HTML.read_text()
    once = build_film.rebuild(source, html)
    twice = build_film.rebuild(source, once)
    assert once == twice


def test_one_interpreters_gzip_header_is_not_a_stale_film(build_film, tmp_path):
    # CPython changed the OS byte gzip stamps into its header between 3.9 and
    # 3.11, so the same source compressed by each differs by exactly one byte
    # with identical contents. --check used to compare the encoded bytes, which
    # meant a film built on one interpreter read as stale on the other: the
    # build script rewrote the bundle, the next developer's run rewrote it back,
    # and the mirror's build guard failed on a film that was perfectly current.
    html = HTML.read_text()
    manifest, match = build_film._load_manifest(html)
    raw = bytearray(base64.b64decode(manifest[build_film.APP_ENTRY]["data"]))
    assert raw[:2] == b"\x1f\x8b", "not a gzip stream — this test is aimed at the wrong bytes"
    raw[9] = 0x03 if raw[9] == 0xFF else 0xFF  # the OS byte, and only it
    manifest[build_film.APP_ENTRY]["data"] = base64.b64encode(bytes(raw)).decode("ascii")
    other = (
        html[: match.start(2)]
        + json.dumps(manifest, separators=(",", ":"))
        + html[match.end(2) :]
    )
    assert other != html, "the bytes did not actually change; the test proves nothing"

    built_elsewhere = tmp_path / "film.html"
    built_elsewhere.write_text(other)
    assert build_film.is_current(built_elsewhere, html), (
        "a one-byte gzip header difference is being reported as a stale film"
    )


def test_an_edited_source_is_still_stale(build_film, tmp_path):
    # The other half: ignoring the header must not blunt the actual check.
    shipped = tmp_path / "film.html"
    shipped.write_text(HTML.read_text())
    edited = build_film.rebuild(SOURCE.read_text() + "\n// a real change\n", HTML.read_text())
    assert not build_film.is_current(shipped, edited)


def test_a_change_outside_the_payload_is_still_stale(build_film, tmp_path):
    # Blanking the payload must not blind the comparison to the document around
    # it — that is where the deep dive's swaps live.
    html = HTML.read_text()
    shipped = tmp_path / "film.html"
    shipped.write_text(html)
    repointed = html.replace("prodline_copilot_film_vo/player.js", "elsewhere/player.js", 1)
    assert repointed != html
    assert not build_film.is_current(shipped, repointed)


def test_a_rebuild_round_trips(build_film):
    source = SOURCE.read_text()
    rebuilt = build_film.rebuild(source, HTML.read_text())
    assert build_film.embedded_source(rebuilt) == source


def test_only_the_film_entry_is_rewritten(build_film):
    # The runtime is generated upstream and marked do-not-edit; the rest are
    # fonts. A build that touched them would be rewriting things it does not own.
    import base64
    import gzip

    def entries(html: str) -> dict[str, str]:
        manifest, _ = build_film._load_manifest(html)
        return {k: v["data"] for k, v in manifest.items()}

    before = entries(HTML.read_text())
    after = entries(build_film.rebuild(SOURCE.read_text(), HTML.read_text()))

    assert set(before) == set(after)
    changed = {k for k in before if before[k] != after[k]}
    assert changed <= {build_film.APP_ENTRY}

    # And the runtime is still the thing we think it is.
    runtime = next(k for k in before if k.startswith("da97f8f5"))
    text = gzip.decompress(base64.b64decode(after[runtime])).decode("utf-8")
    assert "do not edit" in text.lower()


# ── The deep-dive film, and the one thing it must never do ──────────────────
#
# An animation can draw a capability the product does not have, which makes the
# long film the easiest place in this repository to overclaim. A chapter covers
# work that is not built. These pin the convention that makes that honest: the
# chapter is badged in the agenda, the scene is badged on screen, and the
# narration says the word out loud.
#
# The deep dive is an internal asset and its source may legitimately be absent
# — a published copy of this repository ships the short film alone. These skip
# rather than fail in that case, which is the same reason `build_film.py`
# treats it as an optional target. `test_the_short_film_carries_no_deep_dive`
# below is the other half: it does not skip, because the point of separating
# the two was that the short film's bundle stays free of the long one.

DEEP_DIVE_SOURCE = ROOT / "film/src/deep_dive.jsx"
VO = ROOT / "film/vo/deep_dive.json"
# The rendered sidecar. It carries the measured audio durations, which is the
# only place the film's actual pacing can be read from — the script says what
# is spoken, not how long it takes to say it.
MANIFEST = ROOT / "docs/media/prodline_copilot_deep_dive_vo/manifest.json"

needs_deep_dive = pytest.mark.skipif(
    not DEEP_DIVE_SOURCE.exists(),
    reason="deep dive source not present — this tree ships the short film only",
)


def test_the_short_film_carries_no_deep_dive():
    """The published film must not smuggle the internal one inside its bundle."""
    bundled = _builder().embedded_source(HTML.read_text())
    for marker in ("DeepDive", "ChapterHead", "ai_parse_document", "DEEP_DIVE_CHAPTERS"):
        assert marker not in bundled, f"{marker!r} leaked into the short film's bundle"

_CHAPTER_RE = re.compile(
    r"^\s*(\w+):\s*\{\s*id:\s*'(?P<id>[\w-]+)',\s*start:\s*(?P<start>[\d.]+),"
    r"\s*end:\s*(?P<end>[\d.]+),\s*title:\s*'(?P<title>[^']*)'(?P<rest>[^}]*)\}",
    re.M,
)


def _intro() -> float:
    """How long the two-minute film runs before the deep dive's own chapter 1."""
    m = re.search(r"^const INTRO = (\d+);", DEEP_DIVE_SOURCE.read_text(), re.M)
    assert m, "no `const INTRO` offset in the deep-dive source"
    return float(m.group(1))


def _chapters() -> list[dict]:
    """The deep dive's chapters, on the clock the player uses.

    The table is authored from zero and shifted by `INTRO`, because the
    two-minute film plays in front of it. Everything that compares a chapter
    against the narration manifest needs the shifted numbers, since that is what
    the packer wrote.
    """
    source = DEEP_DIVE_SOURCE.read_text()
    block = source[source.index("const DD_LOCAL = {") : source.index("const DD = Object")]
    offset = _intro()
    out = []
    for m in _CHAPTER_RE.finditer(block):
        out.append(
            {
                "id": m.group("id"),
                "start": float(m.group("start")) + offset,
                "end": float(m.group("end")) + offset,
                "title": m.group("title"),
                "roadmap": "roadmap: true" in m.group("rest"),
            }
        )
    return out


@needs_deep_dive
def test_the_deep_dive_has_chapters_that_run_in_order_without_gaps():
    chapters = _chapters()
    assert len(chapters) >= 10, f"only parsed {len(chapters)} chapters"
    for earlier, later in zip(chapters, chapters[1:]):
        assert earlier["end"] == later["start"], (
            f"{earlier['id']} ends at {earlier['end']} but {later['id']} starts at {later['start']}"
        )
    # The deep dive's own first chapter picks up exactly where the two-minute
    # film hands over. A gap here is silence on a held frame; an overlap is the
    # intro's closing card playing under chapter 1's title.
    assert chapters[0]["start"] == _intro(), (
        f"chapter 1 starts at {chapters[0]['start']}s but the intro runs to {_intro()}s"
    )


@needs_deep_dive
def test_the_deep_dive_is_the_length_it_claims():
    """Long, on purpose, but not unboundedly so.

    The brief was ten to fifteen minutes. It is now twenty: the two-minute film
    was put in front to carry the business case, and the model-selection chapter
    doubled to answer why each vendor holds the role it does. Both were asked
    for. The ceiling stays low enough that the next thing added has to displace
    something rather than simply extend the film.
    """
    total = _chapters()[-1]["end"]
    assert 900 <= total <= 1400, f"{total}s is outside the twenty-minute shape"


@needs_deep_dive
def test_every_roadmap_chapter_is_badged_on_screen():
    source = DEEP_DIVE_SOURCE.read_text()
    for chapter in _chapters():
        if not chapter["roadmap"]:
            continue
        # Find the scene that renders this chapter and check it badges itself.
        scenes = re.findall(r"function (DD\w+)\(\) \{(.*?)\n\}", source, re.S)
        owning = [body for _, body in scenes if f"DD.{chapter['id']}.start" in body]
        assert owning, f"no scene renders chapter {chapter['id']}"
        assert any("roadmap" in body for body in owning), (
            f"chapter {chapter['id']} is roadmap in the agenda but its scene shows no badge"
        )


@needs_deep_dive
def test_the_narration_says_roadmap_out_loud_where_it_must():
    spec = json.loads(VO.read_text())
    for chapter in _chapters():
        if not chapter["roadmap"]:
            continue
        spoken = " ".join(
            s["text"].lower()
            for s in spec["segments"]
            if chapter["start"] <= s["start"] < chapter["end"]
        )
        assert spoken, f"chapter {chapter['id']} has no narration at all"
        assert "roadmap" in spoken or "not built" in spoken, (
            f"chapter {chapter['id']} draws unbuilt capability without saying so"
        )


@needs_deep_dive
def test_the_narration_asserts_no_payback_figure():
    # Same rule the demo guide is held to: the data is synthetic and the value
    # is line-specific, so a spoken number would acquire authority it has not
    # earned. This is stricter than the on-screen check because narration is
    # what a customer remembers.
    spoken = " ".join(s["text"] for s in json.loads(VO.read_text())["segments"])
    assert not re.search(r"\$\s?\d", spoken)
    assert not re.search(r"\b\d+\s?%\s?(roi|return|savings?|reduction)", spoken, re.I)


@needs_deep_dive
def test_the_narration_fits_inside_the_film():
    spec = json.loads(VO.read_text())
    end = _chapters()[-1]["end"]
    for seg in spec["segments"]:
        assert 0 <= seg["start"] < end, f"line {seg['id']} starts at {seg['start']}, film ends at {end}"


@needs_deep_dive
def test_every_line_belongs_to_a_chapter_that_exists():
    ids = {c["id"] for c in _chapters()}
    for seg in json.loads(VO.read_text())["segments"]:
        assert seg.get("chapter") in ids, (
            f"line {seg['id']} is filed under {seg.get('chapter')!r}, which is not a chapter"
        )


@needs_deep_dive
@pytest.mark.skipif(not MANIFEST.exists(), reason="voice-over has not been rendered")
def test_no_chapter_runs_out_of_things_to_say():
    """The defect this pins is what the first cut of the film actually did.

    Chapter lengths were round numbers and the narration was written to taste,
    so a ninety-second chapter got fifty seconds of voice. The other forty were
    spent holding a still frame in silence — every chapter revealed its whole
    layout inside eleven seconds, so there was nothing moving either. Across
    fourteen chapters that was seven minutes of dead air in a fifteen-minute
    film, and it reads as unfinished rather than as a pause.

    Silence is measured against the whole chapter rather than between lines,
    because the hole was always at the end, where a between-lines check does
    not look.
    """
    segments = json.loads(MANIFEST.read_text())["segments"]
    lonely = []
    for chapter in _chapters():
        spoken = [s for s in segments if chapter["start"] <= s["start"] < chapter["end"]]
        assert spoken, f"chapter {chapter['id']} is silent for its whole {chapter['end'] - chapter['start']}s"
        quiet = chapter["end"] - max(s["start"] + s["audioDuration"] for s in spoken)
        if quiet > 6.0:
            lonely.append(f"{chapter['id']}: {quiet:.1f}s of silence before the next chapter")
    assert not lonely, "run scripts/build_film_vo.py — " + "; ".join(lonely)


@needs_deep_dive
@pytest.mark.skipif(not MANIFEST.exists(), reason="voice-over has not been rendered")
def test_the_film_is_narrated_as_densely_as_the_short_one():
    """The two-minute film is 95% voice. The deep dive has no excuse to be less.

    The remainder is deliberate: a beat before a chapter speaks and a beat after
    it stops, which is what stops fourteen chapters sounding like one sentence.
    """
    segments = json.loads(MANIFEST.read_text())["segments"]
    film = _chapters()[-1]["end"]
    spoken = sum(s["audioDuration"] for s in segments)
    assert spoken / film > 0.85, f"only {100 * spoken / film:.0f}% of the film is narrated"


def _reveals(body: str) -> list[float]:
    """Every timed reveal in a scene, in either form the layout kit accepts.

    A panel used to take one `at={n}` and an even stride. Items now carry their
    own `at: n`, because an even stride put chapter 2's Zerobus box on screen
    sixteen seconds after the narration said the word. A regex that only knew
    the prop form reported the rewritten scenes as having no reveals at all.
    """
    return sorted(float(v) for v in re.findall(r"at[=:] *\{?([\d.]+)\}?", body))


def _scene_bodies(source: str) -> dict[str, str]:
    out = {}
    for _name, body in re.findall(r"function (DD\w+)\(\) \{(.*?)\n\}", source, re.S):
        owns = re.search(r"DD\.(\w+)\.start", body)
        if owns:
            out[owns.group(1)] = body
    return out


@needs_deep_dive
def test_no_reveal_lands_while_the_film_is_silent():
    """A box that appears before its sentence is worse than one that appears late.

    Chapter 2 narrated "signals arrive over Zerobus" one and a half seconds in
    and drew the Zerobus box at seventeen. Both the film and the narration were
    individually fine, and the only test looking at reveals asked whether the
    *last* one landed near the end — which an even stride satisfies while being
    wrong everywhere in between.

    This does not catch that bug, and nothing here can: whether the voice is
    discussing *this* box is not mechanically knowable. What it catches is the
    cruder version — something appearing while nobody is saying anything at all,
    which is what an even stride produces once a chapter is re-timed and the
    stride is left behind. Semantic alignment is checked by watching it.
    """
    if not MANIFEST.exists():
        pytest.skip("voice-over has not been rendered")
    scenes = _scene_bodies(DEEP_DIVE_SOURCE.read_text())
    segments = json.loads(MANIFEST.read_text())["segments"]

    orphans = []
    for chapter in _chapters():
        base, body = chapter["start"], scenes[chapter["id"]]
        spoken = [
            (s["start"] - base, s["start"] - base + s["audioDuration"])
            for s in segments
            if base <= s["start"] < chapter["end"]
        ]
        for at in _reveals(body):
            if at <= 1.2:
                continue  # the title card, which opens every chapter
            # A second of slack each side: a reveal may lead its sentence in or
            # land on the tail of it, and the voice does not start on the frame.
            if not any(start - 1.0 <= at <= end + 1.0 for start, end in spoken):
                orphans.append(f"  {chapter['id']}: something appears at {at:.1f}s with nothing being said")

    assert not orphans, "reveals that land in silence:\n" + "\n".join(orphans)


@needs_deep_dive
def test_the_scenes_keep_moving_for_as_long_as_the_voice_does():
    """A chapter that draws itself in ten seconds and then talks for eighty is
    a slide, not a film. Every panel takes a `step`, so a scene can spread its
    reveals across the lines being spoken over it; this checks each scene's
    last reveal lands near its last spoken line rather than a minute before it.
    """
    if not MANIFEST.exists():
        pytest.skip("voice-over has not been rendered")
    source = DEEP_DIVE_SOURCE.read_text()
    scenes = {}
    for name, body in re.findall(r"function (DD\w+)\(\) \{(.*?)\n\}", source, re.S):
        owns = re.search(r"DD\.(\w+)\.start", body)
        if owns:
            scenes[owns.group(1)] = body

    segments = json.loads(MANIFEST.read_text())["segments"]
    stalled = []
    for chapter in _chapters():
        body = scenes.get(chapter["id"])
        assert body, f"no scene renders chapter {chapter['id']}"
        reveals = _reveals(body)
        assert reveals, f"chapter {chapter['id']} has no timed reveals"
        spoken = [s for s in segments if chapter["start"] <= s["start"] < chapter["end"]]
        last_cue = max(s["start"] for s in spoken) - chapter["start"]
        if max(reveals) < last_cue - 12.0:
            stalled.append(
                f"{chapter['id']}: last reveal at {max(reveals):.0f}s, still talking at {last_cue:.0f}s"
            )
    assert not stalled, "; ".join(stalled)


# ── The voice player's idea of "paused" ─────────────────────────────────────
#
# The films publish their clock to localStorage; the voice player watches that
# value and infers everything else, because there is no pause event to listen
# for. So "paused" has to mean "the clock has not moved for a moment", measured
# from the last time it *did* move.
#
# It once measured from the previous frame instead. `tick()` runs on rAF, so
# that gap is always ~16ms and the "nothing moved recently" branch could never
# fire: `playing` latched true and stayed true. Pausing then left the player
# re-seeking a still-running audio element back to a frozen offset every time
# it drifted 0.25s past it, and the last quarter second of narration looped for
# as long as the film sat paused.
#
# `scripts/dev/check_film_pause.mjs` proves the behaviour in a browser. This is
# the cheap half that runs in the gate.

PLAYERS = (
    ROOT / "scripts/build_film_vo.py",
    ROOT / "docs/media/prodline_copilot_film_vo/player.js",
    ROOT / "docs/media/prodline_copilot_deep_dive_vo/player.js",
)


@pytest.mark.parametrize("player", PLAYERS, ids=lambda p: p.name)
def test_pause_is_judged_from_the_last_time_the_clock_moved(player):
    if not player.exists():
        pytest.skip(f"{player.name} is not in this tree")
    text = player.read_text()
    if "playing = false" not in text:
        pytest.skip(f"{player.name} does not carry the voice player")

    assert "lastAdvance" in text, (
        f"{player.name} no longer tracks when the film's clock last advanced; "
        "if pause is inferred from the frame delta instead, it never fires and "
        "the narration loops while paused"
    )
    assert re.search(r"now - lastAdvance > \d+", text), (
        f"{player.name} does not compare against the last advance"
    )
    assert "lastWall" not in text, (
        f"{player.name} is back to measuring the gap between frames, which on "
        "requestAnimationFrame is always ~16ms and never reads as paused"
    )


# ── The playback bar ────────────────────────────────────────────────────────
# A twenty-minute film is watched by jumping around it, so the bar is part of
# the deliverable rather than authoring furniture. These pin the two things a
# viewer reaches for and that nothing else would notice the loss of.


def test_the_bar_can_skip_five_seconds_either_way():
    """Arrow keys scrub by a tenth of a second, which is an authoring tool.

    Someone watching wants to skip back over a sentence they missed, so the
    bar carries buttons and J/L for a five-second jump.
    """
    source = SOURCE.read_text()
    assert "onSkip" in source, "the playback bar no longer takes a skip handler"
    for key, direction in (("KeyJ", "- 5"), ("KeyL", "+ 5")):
        assert re.search(rf"{key}.*?\n.*?clamp\(t {re.escape(direction)}", source), (
            f"{key} no longer jumps five seconds"
        )
    for label in ("Back 5 seconds (J)", "Forward 5 seconds (L)"):
        assert label in source, f"the {label!r} button is gone from the bar"


@needs_deep_dive
def test_the_deep_dive_agenda_is_numbered_and_the_intro_is_not():
    """The agenda has to agree with the "CHAPTER n" eyebrow on screen.

    The introduction is the short film rather than a chapter of this one, so
    it is the one entry that carries no number.
    """
    chapters = DEEP_DIVE_SOURCE.read_text()
    intro = re.search(r"\{ id: 'intro',[^}]*\}", chapters)
    assert intro, "the deep dive no longer opens on the short film"
    assert "number:" not in intro.group(0), (
        "the introduction is numbered, so every chapter after it disagrees "
        "with the eyebrow drawn on screen"
    )
    assert re.search(r"number: i \+ 1", chapters), (
        "deep-dive chapters are no longer numbered from one"
    )
    assert "current.number" in SOURCE.read_text(), (
        "the bar no longer shows which chapter you are in by number"
    )


# ── Pronunciation ───────────────────────────────────────────────────────────
# The voice says some words wrong, so the synthesiser is handed a respelling.
# That respelling must not leak into the script or the manifest, which are the
# record of what the film actually says.


def _vo_builder():
    spec = importlib.util.spec_from_file_location(
        "build_film_vo", ROOT / "scripts/build_film_vo.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_the_voice_is_handed_the_respelling_and_nothing_else_is():
    vo = _vo_builder()
    assert vo.SAID_AS, "the pronunciation table is empty"
    for written, said in vo.SAID_AS.items():
        # Hyphenation runs after the table, so an entry that respells a word as
        # spaced capitals is expected to arrive at the voice hyphenated.
        expected = vo._SPACED_CAPS.sub(lambda m: m.group(0).replace(" ", "-"), said)
        assert vo._speakable(f"a {written} b") == f"a {expected} b", (
            f"{written} is in the table but is not applied"
        )

    scripts = [VO, ROOT / "docs/media/prodline_copilot_film_vo/manifest.json"]
    for path in scripts:
        if not path.exists():
            continue
        spoken = " ".join(s["text"] for s in json.loads(path.read_text())["segments"])
        for written, said in vo.SAID_AS.items():
            if said == written or " " in said:
                continue  # spacing an initialism is not a misspelling
            assert not re.search(rf"\b{re.escape(said)}\b", spoken), (
                f"{path.name} spells {written!r} as {said!r}; the respelling belongs "
                "in the build, not in the record of what the film says"
            )


def test_narration_that_slid_under_the_reveals_is_reported(tmp_path, monkeypatch):
    """Every timing regression in this film has arrived the same way.

    A line is reworded or the voice changes, the audio comes out a different
    length, and the narration slides underneath reveals that were hand-timed
    against where it used to be. The render succeeds either way, so the drift
    has to be reported or it is only found by watching the whole film.
    """
    vo = _vo_builder()
    monkeypatch.setattr(vo, "OUT", tmp_path)
    (tmp_path / "manifest.json").write_text(
        json.dumps(
            {
                "segments": [{"id": "09a", "start": 100.0}],
                "chapters": {"agents": [90.0, 190.0]},
            }
        )
    )

    moved = [{"id": "09a", "chapter": "agents", "start": 97.0}]
    assert vo._drift_by_chapter(moved, {"agents": (90.0, 190.0)}) == [("agents", -3.0)]

    # Widening a chapter carries its narration along, and the reveals are timed
    # from the chapter's own start — so that is not drift and must stay quiet.
    shifted = [{"id": "09a", "chapter": "agents", "start": 104.0}]
    assert vo._drift_by_chapter(shifted, {"agents": (94.0, 194.0)}) == []


def test_initialisms_reach_the_voice_hyphenated_and_words_do_not():
    """Spaced capitals make the voice pause between every letter.

    The script writes them spaced because that is how you read a line out loud
    while editing it, so the hyphens are added on the way to the synthesiser.
    Acronyms that are said as words have to survive that untouched — spelling
    SCADA out would be wrong rather than merely slow.
    """
    vo = _vo_builder()
    assert vo._speakable("it reads S Q L") == "it reads S-Q-L"
    assert vo._speakable("the C M M S feed") == "the C-M-M-S feed"
    assert vo._speakable("from SCADA") == "from SCADA"
    assert vo._speakable("from LIMS") == "from Limz"

    # A lone capital is a word, not an initialism.
    assert vo._speakable("A machine stopped") == "A machine stopped"

    for path in (VO, ROOT / "docs/media/prodline_copilot_film_vo/manifest.json"):
        if not path.exists():
            continue
        for seg in json.loads(path.read_text())["segments"]:
            said = vo._speakable(seg["text"])
            assert not vo._SPACED_CAPS.search(said), (
                f"{path.name} line {seg['id']} still hands the voice spaced "
                f"capitals: {vo._SPACED_CAPS.search(said).group(0)!r}"
            )


def test_the_synthesiser_never_sees_the_raw_text():
    """A second render path would quietly skip the table."""
    source = (ROOT / "scripts/build_film_vo.py").read_text()
    calls = re.findall(r'"--text",\s*([^,\]]+)', source)
    assert calls, "no edge-tts invocation found"
    for call in calls:
        assert "_speakable" in call, (
            f"edge-tts is handed {call.strip()} directly, so the pronunciation "
            "table does not apply to it"
        )
