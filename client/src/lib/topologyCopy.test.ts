import { describe, expect, it } from 'vitest';

import { genieSpacePhrase, genieSpaceRole, reachSummary, spaceCountLabel } from './topologyCopy';

// These strings are read by an audience, not only by an operator, and the same
// components render in a deployment that runs the governed model and in one
// that does not. Every case below is wrong in the second kind of deployment if
// the copy is a constant: the count reads "1 Genie spaces", and the closing
// paragraph claims a wiring that is not there. None of it shows up in the
// environment where the feature was built, which is what makes it worth
// pinning.

describe('genieSpacePhrase', () => {
  it('says "space" when line ops is the only one', () => {
    // The only case that produces 1, and the one nobody sees: a deployment
    // running without the governed model.
    expect(genieSpacePhrase(1)).toBe('1 Genie space');
  });

  it('says "spaces" for the four governed domains plus live', () => {
    expect(genieSpacePhrase(5)).toBe('5 Genie spaces');
  });

  it('says "spaces" for none', () => {
    expect(genieSpacePhrase(0)).toBe('0 Genie spaces');
  });
});

describe('spaceCountLabel', () => {
  it('builds on the same phrase, so the two surfaces cannot disagree', () => {
    for (const n of [0, 1, 4, 5]) {
      expect(spaceCountLabel(n)).toBe(`${genieSpacePhrase(n)} wired in this deployment`);
    }
  });
});

describe('reachSummary', () => {
  const shared = 'read the live tables with direct SQL';

  it('claims a governed domain per specialist only when all four are wired', () => {
    const text = reachSummary(4, 4);
    expect(text).toContain('Each specialist reaches one governed domain');
    expect(text).toContain(shared);
  });

  it('does not claim governed domains when none are installed', () => {
    const text = reachSummary(0, 4);
    expect(text).not.toContain('Each specialist reaches one governed domain');
    expect(text).toContain('not installed in this deployment');
  });

  it('describes an absent governed model as absent, not as broken', () => {
    // "Not provisioned" reads as a fault. Running without the governed model
    // is a supported configuration and the copy has to say so.
    const text = reachSummary(0, 4).toLowerCase();
    for (const alarming of ['not provisioned', 'unavailable', 'failed', 'missing', 'error']) {
      expect(text).not.toContain(alarming);
    }
  });

  it('names the count when only some domains are wired', () => {
    const text = reachSummary(2, 4);
    expect(text).toContain('2 of the 4 governed domains');
    expect(text).toContain('fall back to SQL');
    expect(text).not.toContain('Each specialist reaches one governed domain');
  });

  it('always mentions the live-SQL path, whatever is wired', () => {
    // It is the one thing true of every deployment, and it is what makes an
    // unprovisioned map still describe a working app.
    for (let n = 0; n <= 4; n += 1) {
      expect(reachSummary(n, 4)).toContain(shared);
    }
  });

  it('ends every variant as a sentence', () => {
    for (let n = 0; n <= 4; n += 1) {
      expect(reachSummary(n, 4).trim()).toMatch(/\.$/);
    }
  });

  it('never calls the line ops space "live"', () => {
    // Liveness is not what separates the three specialists that reach it — all
    // eight read the live tables with SQL — and "live" against "governed" reads
    // as two kinds of Genie rather than two scopes of one.
    for (let n = 0; n <= 4; n += 1) {
      expect(reachSummary(n, 4)).not.toMatch(/live (space|genie)/i);
    }
  });

  it('explains why line ops is broader wherever a governed domain is shown', () => {
    // The map argues that narrow scope keeps Genie accurate, beside a space of
    // 14 tables. Left unanswered, that is the first thing a sceptic asks.
    for (const n of [1, 2, 3, 4]) {
      const text = reachSummary(n, 4);
      expect(text).toContain('broader on purpose');
      expect(text).toContain('grants live');
    }
  });

  it('skips that explanation when there is no governed domain to contrast with', () => {
    expect(reachSummary(0, 4)).not.toContain('broader on purpose');
  });
});

describe('genieSpaceRole', () => {
  // The About tab's platform card stated "across five curated spaces" as a
  // constant, two hundred lines above a paragraph counting the same spaces from
  // the topology endpoint. In a deployment without the governed model the page
  // said both five and one.
  it('names all four domains when the governed model is fully wired', () => {
    const role = genieSpaceRole(4, true);
    expect(role).toContain('5 Genie spaces');
    expect(role).toContain('asset, quality, inventory, production');
  });

  it('drops to the live space alone when no domain is provisioned', () => {
    const role = genieSpaceRole(0, true);
    expect(role).toContain('1 Genie space:');
    expect(role).not.toContain('Genie spaces');
    expect(role).toContain('live streaming tables');
    expect(role).not.toContain('governed');
  });

  it('counts a partial rollout without naming domains it does not have', () => {
    const role = genieSpaceRole(2, true);
    expect(role).toContain('3 Genie spaces');
    expect(role).toContain('2 of the 4 governed domains');
    expect(role).not.toContain('asset, quality, inventory, production');
  });

  it('reads as singular for exactly one governed domain', () => {
    expect(genieSpaceRole(1, true)).toContain('plus one governed domain');
  });

  it('says so plainly when nothing is wired at all', () => {
    // Reachable only in a broken deployment, which is when honest copy matters
    // most: the alternative renders "across 0 Genie spaces:" with a dangling colon.
    expect(genieSpaceRole(0, false)).toContain('no space is wired');
  });

  it('never dangles a connective', () => {
    for (const configured of [0, 1, 2, 3, 4]) {
      for (const lineOps of [true, false]) {
        const role = genieSpaceRole(configured, lineOps);
        expect(role.trim(), `${configured}/${lineOps}`).not.toMatch(/[:,]$/);
        expect(role, `${configured}/${lineOps}`).not.toContain(', plus ,');
      }
    }
  });
});
