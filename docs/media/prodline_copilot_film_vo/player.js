/**
 * Synchronized voice-over for prodline_copilot_film.html.
 *
 * Works from file:// (no fetch). Voice is always on — browsers may still
 * require one user gesture (play/scrub/click) before the first audible play;
 * that unlock is silent (no enable/disable UI).
 *
 * Narration: Microsoft neural TTS (en-US-JennyNeural) as MP3.
 */
(function voiceoverPlayer() {
  const PERSIST_KEY = 'prodline-copilot';
  const AUDIO_EXT = 'mp3';

  const scriptEl =
    document.currentScript || Array.from(document.scripts).find((s) => (s.src || '').includes('player.js'));
  const voBase =
    scriptEl && scriptEl.src
      ? scriptEl.src.replace(/player\.js(?:\?.*)?$/, '')
      : new URL('prodline_copilot_film_vo/', window.location.href).href;

  const MANIFEST = {
    version: 11,
    persistKey: PERSIST_KEY,
    segments: [
      { id: '01', start: 0.2, end: 9.456 },
      { id: '02', start: 9.5, end: 21.804 },
      { id: '03', start: 22.3, end: 35.588 },
      { id: '04', start: 36.3, end: 48.46 },
      { id: '05', start: 50.2, end: 57.368 },
      { id: '06', start: 57.8, end: 67.344 },
      { id: '07', start: 67.7, end: 78.348 },
      { id: '08', start: 78.6, end: 86.2 },
      { id: '08mcp', start: 86.2, end: 94.4 },
      { id: '09', start: 94.4, end: 101.712 },
      { id: '10', start: 102.3, end: 109.78 }
    ],
  };

  const cache = new Map();
  let audio = null;
  let activeId = null;
  let unlocked = false;
  let lastTime = 0;
  // Wall clock of the last tick that saw the film's time actually move — not
  // the last tick of any kind. See the note on tick().
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
  // localStorage and this player infers the rest. "Paused" means the clock has
  // not moved for a moment, which has to be measured from the last time it
  // *did* move. Measured from the previous frame instead — tick() runs on rAF,
  // so the gap was always ~16ms — the test never fired, and pausing looped the
  // last quarter second of narration for as long as the film sat paused.
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
    const probe = cache.get('01') || new Audio(audioUrl('01'));
    cache.set('01', probe);
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
    // Try immediately in case autoplay is already allowed.
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
