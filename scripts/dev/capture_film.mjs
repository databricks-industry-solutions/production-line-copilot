/**
 * Record a film to MP4, frame by frame.
 *
 * Not a screen recording. The stage exposes `window.__stage.setTime`, so this
 * steps the clock to each frame, screenshots the canvas, and pipes the buffer
 * straight into ffmpeg. That buys three things a real-time capture cannot: the
 * output is deterministic, it does not care how fast the machine is, and there
 * are no dropped frames on a slow reveal.
 *
 * Frames go down the pipe rather than to disk. At 1080p25 a fifteen-minute
 * film is over twenty thousand images; writing them out first would cost tens
 * of gigabytes to delete a minute later.
 *
 * Audio is assembled separately. The narration is one MP3 per line with a
 * start time in the voice-over manifest, so the track is built by delaying
 * each clip to its cue and mixing them — the same timings the interactive
 * player uses, which is what keeps the MP4 and the HTML in sync.
 *
 * Usage:
 *   node scripts/dev/capture_film.mjs --deep-dive
 *   node scripts/dev/capture_film.mjs --deep-dive --seconds 30   # a sample
 *   node scripts/dev/capture_film.mjs --deep-dive --remux        # reuse the frames
 *
 * The rendered MP4 carries chapter markers, so QuickTime, VLC and IINA can jump
 * between chapters even though the agenda itself is not in the frame.
 */
import { chromium } from 'playwright';
import { spawn, spawnSync } from 'node:child_process';
import { fileURLToPath, pathToFileURL } from 'node:url';
import { dirname, join } from 'node:path';
import { existsSync, readFileSync, writeFileSync, mkdirSync, rmSync } from 'node:fs';

const ROOT = join(dirname(fileURLToPath(import.meta.url)), '..', '..');
const MEDIA = join(ROOT, 'docs', 'media');

const FILMS = {
  short: { html: 'prodline_copilot_film.html', vo: 'prodline_copilot_film_vo', out: 'prodline_copilot_film' },
  deep: {
    html: 'prodline_copilot_deep_dive.html',
    vo: 'prodline_copilot_deep_dive_vo',
    out: 'prodline_copilot_deep_dive',
  },
};

const argv = process.argv;
const which = argv.includes('--deep-dive') ? 'deep' : 'short';
const film = FILMS[which];
const arg = (name, fallback) => {
  const i = argv.indexOf(name);
  return i > -1 && argv[i + 1] ? Number(argv[i + 1]) : fallback;
};

const FPS = arg('--fps', 25);
const CRF = arg('--crf', 24);
const WIDTH = 1920;
const HEIGHT = 1080;

const TMP = join(ROOT, 'output', 'capture');
mkdirSync(TMP, { recursive: true });
const silentVideo = join(TMP, `${film.out}.silent.mp4`);
const audioTrack = join(TMP, `${film.out}.audio.m4a`);
const finalOut = join(MEDIA, `${film.out}.mp4`);

// ── 1. frames ───────────────────────────────────────────────────────────────

const browser = await chromium.launch();
const page = await browser.newPage({ viewport: { width: WIDTH, height: HEIGHT } });
await page.goto(pathToFileURL(join(MEDIA, film.html)).href);
await page.waitForFunction(() => window.__stage != null, { timeout: 30_000 });

const duration = arg('--seconds', await page.evaluate(() => window.__stage.duration));
// The bar and its agenda are deliberately not in the frame, so a viewer of the
// MP4 has no way to see the shape of the film or jump around it. MP4 carries
// chapter markers, which gets most of that back in QuickTime, VLC and IINA, and
// gives you the timestamp list to paste into a description. It costs nothing:
// the marks are already on the stage and the mux is a copy either way.
const chapters = await page.evaluate(() => (window.__stage.chapters || []).map(
  (c) => ({ start: c.start, title: c.title, number: c.number })
));

// Hide the playback chrome and pin the canvas to the viewport. The bar is an
// affordance for the HTML, not part of the film.
//
// Shooting the page rather than the canvas element is deliberate: the stage
// centres the canvas with a flex layout that lands it on a half pixel, so an
// element screenshot comes back 1081 rows tall and libx264 rejects the odd
// dimension. A page screenshot is exactly the viewport, which is even by
// construction.
await page.evaluate(() => {
  window.__stage.setPlaying(false);
  const canvas = document.querySelector('div[style*="1920px"]');
  if (!canvas) throw new Error('could not find the film canvas');
  canvas.style.position = 'fixed';
  canvas.style.top = '0';
  canvas.style.left = '0';
  canvas.style.transform = 'none';
  canvas.style.boxShadow = 'none';
  // The canvas is opaque and exactly viewport-sized, so lifting it above
  // everything else is what actually removes the playback bar from the frame.
  // Matching the bar by its inline background colour does not work — the
  // browser re-serialises `rgba(20,20,20,0.92)` with spaces, so the selector
  // silently misses and the bar ends up burned into the video.
  canvas.style.zIndex = '2147483647';
});
const shotTarget = page;

const remuxOnly = argv.includes('--remux');
if (remuxOnly && !existsSync(silentVideo)) {
  console.error(`--remux needs ${silentVideo}, which is not there — capture once first`);
  process.exit(1);
}

if (remuxOnly) {
  console.log(`reusing ${silentVideo} — frames are not re-rendered`);
  await browser.close();
} else {

const total = Math.round(duration * FPS);
console.log(`${film.html}: ${duration}s at ${FPS}fps = ${total} frames, crf ${CRF}`);

const ff = spawn(
  'ffmpeg',
  [
    '-y',
    '-v',
    'error',
    '-f',
    'image2pipe',
    '-framerate',
    String(FPS),
    '-i',
    '-',
    '-c:v',
    'libx264',
    '-preset',
    'slow',
    '-crf',
    String(CRF),
    '-pix_fmt',
    'yuv420p',
    '-movflags',
    '+faststart',
    silentVideo,
  ],
  { stdio: ['pipe', 'inherit', 'inherit'] }
);

const started = Date.now();
for (let i = 0; i < total; i += 1) {
  const t = i / FPS;
  await page.evaluate((v) => window.__stage.setTime(v), t);
  const buf = await shotTarget.screenshot({ type: 'png', animations: 'disabled' });
  if (!ff.stdin.write(buf)) await new Promise((r) => ff.stdin.once('drain', r));
  if (i % (FPS * 30) === 0) {
    const done = i / total;
    const elapsed = (Date.now() - started) / 1000;
    const eta = done > 0 ? (elapsed / done) * (1 - done) : 0;
    console.log(`  ${(done * 100).toFixed(1)}%  t=${t.toFixed(0)}s  eta ${(eta / 60).toFixed(1)} min`);
  }
}
ff.stdin.end();
await new Promise((resolve, reject) =>
  ff.on('close', (code) => (code === 0 ? resolve() : reject(new Error(`ffmpeg exited ${code}`))))
);
await browser.close();
console.log(`video: ${silentVideo}`);

}

// ── 2. narration, laid out on its cues ──────────────────────────────────────

const manifestPath = join(MEDIA, film.vo, 'manifest.json');
let haveAudio = false;
if (existsSync(manifestPath)) {
  const segments = JSON.parse(readFileSync(manifestPath, 'utf8')).segments.filter((s) => s.start < duration);
  if (segments.length) {
    const inputs = segments.flatMap((s) => ['-i', join(MEDIA, film.vo, `${s.id}.mp3`)]);
    const delays = segments
      .map((s, i) => `[${i}:a]adelay=${Math.round(s.start * 1000)}|${Math.round(s.start * 1000)}[a${i}]`)
      .join(';');
    // Pad to the film's length. The last line lands several seconds before the
    // closing card finishes, and without this the `-shortest` mux would cut the
    // video at the final word rather than at the end of the film.
    const mix =
      `${segments.map((_, i) => `[a${i}]`).join('')}amix=inputs=${segments.length}:duration=longest:normalize=0[mixed];` +
      `[mixed]apad=whole_dur=${duration}[out]`;
    const res = spawnSync(
      'ffmpeg',
      [
        '-y',
        '-v',
        'error',
        ...inputs,
        '-filter_complex',
        `${delays};${mix}`,
        '-map',
        '[out]',
        '-c:a',
        'aac',
        '-b:a',
        '128k',
        audioTrack,
      ],
      { stdio: 'inherit' }
    );
    haveAudio = res.status === 0;
    console.log(haveAudio ? `audio: ${segments.length} lines mixed` : 'audio: mix failed, shipping silent');
  }
}

// ── 3. mux ──────────────────────────────────────────────────────────────────

const marks = chapters.filter((c) => c.start < duration).sort((a, b) => a.start - b.start);
let chapterFile = null;
if (marks.length > 1) {
  const ms = (s) => Math.round(s * 1000);
  chapterFile = join(TMP, `${film.out}.chapters.txt`);
  writeFileSync(
    chapterFile,
    ';FFMETADATA1\n' +
      marks
        .map((c, i) => {
          const end = i + 1 < marks.length ? marks[i + 1].start : duration;
          const title = c.number ? `${c.number}. ${c.title}` : c.title;
          return `[CHAPTER]\nTIMEBASE=1/1000\nSTART=${ms(c.start)}\nEND=${ms(end)}\ntitle=${title}\n`;
        })
        .join('')
  );
}

const chapterArgs = chapterFile ? ['-i', chapterFile, '-map_metadata', haveAudio ? '2' : '1'] : [];

const muxArgs = haveAudio
  ? [
      '-y',
      '-v',
      'error',
      '-i',
      silentVideo,
      '-i',
      audioTrack,
      ...chapterArgs,
      '-map',
      '0:v',
      '-map',
      '1:a',
      '-c:v',
      'copy',
      '-c:a',
      'copy',
      '-shortest',
      '-movflags',
      '+faststart',
      finalOut,
    ]
  : ['-y', '-v', 'error', '-i', silentVideo, ...chapterArgs, '-map', '0:v', '-c:v', 'copy', '-movflags', '+faststart', finalOut];
spawnSync('ffmpeg', muxArgs, { stdio: 'inherit' });
// Keep the silent render unless asked to tidy up: re-muxing costs seconds,
// re-rendering twenty thousand frames costs a quarter of an hour.
if (argv.includes('--clean')) {
  rmSync(silentVideo, { force: true });
}
rmSync(audioTrack, { force: true });

const size = spawnSync('stat', ['-f%z', finalOut], { encoding: 'utf8' }).stdout.trim();
console.log(`\n${finalOut}\n${(Number(size) / 1048576).toFixed(1)} MB` + (marks.length > 1 ? `, ${marks.length} chapters` : ''));
