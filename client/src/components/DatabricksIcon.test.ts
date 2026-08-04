import { describe, it, expect } from 'vitest';
import { readFileSync } from 'node:fs';
import { join } from 'node:path';

// This mark was once hand-drawn and shipped vertically flipped: its bands
// peaked upward, where the real mark's bands dip downward in the middle. It
// went unnoticed until it was placed on every vendor badge in the Multi-AI
// tab. These tests pin the official Simple Icons (CC0) geometry so that a
// redraw-by-hand fails here instead of in front of an audience.

const OFFICIAL_PATH =
  'M.95 14.184L12 20.403l9.919-5.55v2.21L12 22.662l-10.484-5.96-.565.308v.77L12 24l11.05-6.218v-4.317l-.515-.309L12 19.118l-9.867-5.653v-2.21L12 16.805l11.05-6.218V6.32l-.515-.308L12 11.974 2.647 6.681 12 1.388l7.76 4.368.668-.411v-.566L12 0 .95 6.27v.72L12 13.207l9.919-5.55v2.26L12 15.52 1.516 9.56l-.565.308Z';

const source = readFileSync(join(__dirname, 'DatabricksIcon.tsx'), 'utf8');

describe('DatabricksIcon', () => {
  it('uses the official mark geometry verbatim', () => {
    expect(source).toContain(OFFICIAL_PATH);
  });

  it('keeps the 24-unit viewBox the official path is drawn against', () => {
    // The path coordinates run 0..24; any other viewBox crops or floats the mark.
    expect(source).toContain('viewBox="0 0 24 24"');
  });

  it('draws exactly one path, as the official single-path mark does', () => {
    expect(source.match(/<path\b/g) ?? []).toHaveLength(1);
  });

  it('is not flipped or rotated by a transform', () => {
    expect(source).not.toMatch(/transform=|scale\(|rotate\(/);
  });
});
