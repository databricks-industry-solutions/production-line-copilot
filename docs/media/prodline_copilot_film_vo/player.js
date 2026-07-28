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
    version: 8,
    persistKey: PERSIST_KEY,
    segments: [
      {
        id: '01',
        start: 0.2,
        end: 9.4,
        text: 'This is Maya, who runs Packaging Line Four: nineteen machines moving toward one shift target, with plenty that can change along the way.',
      },
      {
        id: '02',
        start: 9.5,
        end: 22.1,
        text: "Her real challenge isn't more data; it's that alarms, production, quality, and maintenance sit in different systems — so answers used to arrive in the next-morning report, long after the recovery call.",
      },
      {
        id: '03',
        start: 22.3,
        end: 35.6,
        text: 'That is why her day starts in the control tower, where those systems come together on Databricks, in one governed live view — letting her follow the line as a whole instead of piecing the shift together screen by screen.',
      },
      {
        id: '04',
        start: 36.3,
        end: 48.8,
        text: 'At eleven forty-seven, Filler Three stops and downstream machines begin to starve; each OEE point is worth about three hundred thousand euros a year, so Maya has minutes to make the recovery call.',
      },
      {
        id: '05',
        start: 50.2,
        end: 57.4,
        text: 'Maya asks exactly that, in the same language she would use with her team: can we still hit the shift target?',
      },
      {
        id: '06',
        start: 57.8,
        end: 67.4,
        text: 'The copilot reads the live line state, carries that context forward, and hands the problem to the right specialist from a whole roster built for the line.',
      },
      {
        id: '07',
        start: 67.7,
        end: 78.4,
        text: 'Behind the scenes, it runs real solvers, testing the plan against a thousand scenarios of speed, overtime, and CIP timing — not just guessing.',
      },
      {
        id: '08',
        start: 78.6,
        end: 86.2,
        text: 'The recommendation returns with assumptions, cost, and trade-offs in view, so Maya can review the plan — and approve it.',
      },
      {
        id: '08mcp',
        start: 86.2,
        end: 94.4,
        text: 'On the roadmap, MCP connections carry approved work orders, holds, and schedule updates into the systems that own them.',
      },
      {
        id: '09',
        start: 94.4,
        end: 101.7,
        text: 'As the filler comes back online, the control tower closes the loop, showing the line recover in real time.',
      },
      {
        id: '10',
        start: 102.3,
        end: 109.9,
        text: 'That is ProdLine CoPilot: one connected picture, clearer options, and Maya still firmly in control.',
      },
    ],
  };

  const cache = new Map();
  let audio = null;
  let activeId = null;
  let unlocked = false;
  let lastTime = 0;
  let lastWall = performance.now();
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

  function tick() {
    const t = readTime();
    const now = performance.now();
    const dt = (now - lastWall) / 1000;
    lastWall = now;

    if (Math.abs(t - lastTime) > 0.015) playing = true;
    else if (dt > 0.12) playing = false;
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
