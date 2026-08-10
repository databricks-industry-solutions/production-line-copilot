import { describe, it, expect } from 'vitest';
import { readFileSync } from 'node:fs';
import { join } from 'node:path';

import { DEMO_STEPS, totalMinutes } from './demoScript';
import { MACHINE_LAYOUT } from './constants';
import { SPECIALISTS } from './specialistCatalog';
import { TAB_IDS, isTabId } from './tabRouting';

// The demo guide is the one screen a presenter reads out loud, which changes
// what a defect costs: a stale number here is said to a customer rather than
// merely displayed, and a dead deep-link button fails in front of an audience.
// These pin the properties that make it safe to hand to someone who did not
// build the app.

const script = readFileSync(join(__dirname, 'demoScript.ts'), 'utf8');
const tab = readFileSync(join(__dirname, '..', 'components', 'DemoGuideTab.tsx'), 'utf8');
const app = readFileSync(join(__dirname, '..', 'App.tsx'), 'utf8');

describe('every walkthrough step can actually be followed', () => {
  it('sends you to a tab that exists', () => {
    for (const step of DEMO_STEPS) expect(isTabId(step.tab)).toBe(true);
  });

  it('never sends you to the guide you are already reading', () => {
    for (const step of DEMO_STEPS) expect(step.tab).not.toBe('guide');
  });

  it('gives the presenter something to say, point at, and name', () => {
    for (const step of DEMO_STEPS) {
      expect(step.title.length).toBeGreaterThan(0);
      expect(step.script.length).toBeGreaterThan(80);
      expect(step.pointAt.length).toBeGreaterThan(0);
      expect(step.products.length).toBeGreaterThan(0);
    }
  });

  it('names the tab its button opens', () => {
    // The button reads "Open <tabLabel>", so a blank label ships a button that
    // says "Open →" and tells the presenter nothing.
    for (const step of DEMO_STEPS) expect(step.tabLabel.trim().length).toBeGreaterThan(0);
  });

  it('has a stable, unique key per step', () => {
    // Two steps share a tab, so the tab cannot be the key.
    const ids = DEMO_STEPS.map((step) => step.id);
    expect(new Set(ids).size).toBe(ids.length);
    for (const id of ids) expect(id.trim().length).toBeGreaterThan(0);
  });

  it('budgets a time per step, and a sane total', () => {
    for (const step of DEMO_STEPS) {
      expect(step.seconds).toBeGreaterThan(15);
      expect(step.seconds).toBeLessThanOrEqual(120);
    }
    expect(totalMinutes()).toBeGreaterThanOrEqual(3);
    expect(totalMinutes()).toBeLessThanOrEqual(15);
  });
});

describe('the walkthrough covers the product', () => {
  it('visits every tab except itself', () => {
    // A tab nobody demos is a tab nobody sees. This fails when a tab is added
    // and the guide is not updated, which is the whole point.
    const visited = new Set(DEMO_STEPS.map((step) => step.tab));
    const expected = TAB_IDS.filter((id) => id !== 'guide');
    expect([...visited].sort()).toEqual([...expected].sort());
  });

  it('opens on the line', () => {
    expect(DEMO_STEPS[0].tab).toBe('tower');
  });
});

describe('the numbers are derived, never typed', () => {
  it('takes its counts from the catalogs the rest of the app renders from', () => {
    expect(script).toContain('MACHINE_LAYOUT.length');
    expect(tab).toContain('SPECIALISTS.length');
    expect(tab).toContain('Object.keys(OPTIMIZER_METHODS).length');
  });

  it('states the machine count as the layout actually has it', () => {
    // This is the drift that matters: the script says the number out loud, so a
    // machine added to the line has a presenter reading the old count to a
    // customer. Matched by shape rather than by the phrase around it, because
    // the sentence has already been rewritten once and pinning the wording is
    // how a check quietly retires itself.
    const spoken = DEMO_STEPS.flatMap((step) => [step.script, ...step.pointAt])
      .flatMap((line) => [...line.matchAll(/(\d+) machines\b/g)])
      .map((match) => Number(match[1]));

    expect(spoken.length, 'no step says the machine count out loud any more').toBeGreaterThan(0);
    for (const count of spoken) expect(count).toBe(MACHINE_LAYOUT.length);
  });

  it('does not hardcode a specialist count in the copy', () => {
    const copy = DEMO_STEPS.flatMap((step) => [step.script, ...step.pointAt]).join(' ');
    expect(copy).not.toMatch(new RegExp(`\\b${SPECIALISTS.length} specialists\\b`));
  });
});

describe('the guide does not overclaim', () => {
  it('asserts no ROI or payback figure', () => {
    // The README declines to assert dollar figures because the data is
    // synthetic and the value is line-specific. A talk track is exactly where
    // an invented number would acquire authority.
    const copy = DEMO_STEPS.flatMap((step) => [step.script, ...step.pointAt]).join(' ');
    expect(copy).not.toMatch(/\$\s?\d/);
    expect(copy).not.toMatch(/\b\d+\s?% (?:roi|return|savings?|reduction)/i);
  });

  it('tells the presenter that actions do not write anywhere', () => {
    // The single most likely thing to be overclaimed live.
    expect(script).toContain('proposal');
    expect(script.toLowerCase()).toContain('write-back');
  });

  it('keeps the pushback answers on the page', () => {
    expect(script).toContain('HONEST_ANSWERS');
    expect(script).toContain('synthetic');
    expect(tab).toContain('HONEST_ANSWERS');
  });
});

describe('the guide is reachable', () => {
  it('is mounted by App', () => {
    // A component nobody routed to is a component nobody sees.
    expect(app).toContain("import DemoGuideTab from './components/DemoGuideTab'");
    expect(app).toContain("activeTab === 'guide'");
    expect(app).toContain('<DemoGuideTab onOpenTab={selectTab} />');
  });

  it('has a tab button, placed first', () => {
    expect(app).toContain('label="Demo Guide"');
    const guideAt = app.indexOf('label="Demo Guide"');
    const lineAt = app.indexOf('label="Line Status"');
    expect(guideAt).toBeGreaterThan(-1);
    expect(guideAt).toBeLessThan(lineAt);
  });

  it('routes tab clicks through the URL-aware setter', () => {
    // setActiveTab alone would change the view without changing the address,
    // which is how the deep links silently stop working.
    expect(app).not.toMatch(/onClick=\{\(\) => setActiveTab\(/);
  });
});
