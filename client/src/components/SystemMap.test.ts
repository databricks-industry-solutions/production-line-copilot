import { describe, it, expect } from 'vitest';
import { readFileSync } from 'node:fs';
import path from 'node:path';
import { TAB_IDS, type TabId } from '../lib/tabRouting';

/**
 * The System Map is the picture the README puts on the front page and the About
 * tab shows a reviewer, and it describes the app's own surfaces from a list
 * typed into the component. That list once said four operational tabs while the
 * app had five, and nothing failed — a screenshot cannot notice it is out of
 * date, and neither can a hardcoded count beside it. The list has since had to
 * shrink as well as grow, which a hardcoded count gets wrong in both directions.
 *
 * So every tab has to be accounted for here: drawn on the Control Tower branch,
 * or deliberately not, with the reason written down.
 */
const source = readFileSync(path.join(path.dirname(new URL(import.meta.url).pathname), 'SystemMap.tsx'), 'utf8');
const controlTower = source.slice(
  source.indexOf('const CONTROL_TOWER = ['),
  source.indexOf('];', source.indexOf('const CONTROL_TOWER = ['))
);

/** Tabs the Control Tower branch draws, and the card name each appears under. */
const OPERATIONAL: Partial<Record<TabId, string>> = {
  tower: 'Line Status',
  alerts: 'Events & AI',
  ops: 'Cost & Obs',
  quality: 'Data Integrity',
};

/** Tabs that belong on neither branch, and why. */
const NOT_OPERATIONAL: Partial<Record<TabId, string>> = {
  guide: 'a presenter aid, not a view of the line',
  multiai: 'drawn on the Specialist Copilot branch instead',
  about: 'the page the map itself lives on',
};

describe('the System Map describes the app it is part of', () => {
  it('accounts for every tab, one way or the other', () => {
    const accounted = new Set([...Object.keys(OPERATIONAL), ...Object.keys(NOT_OPERATIONAL)]);
    const unaccounted = TAB_IDS.filter((id) => !accounted.has(id));
    expect(
      unaccounted,
      `${unaccounted.join(', ')} added to TAB_IDS but not placed on the System Map. ` +
        'Add a Control Tower card, or list it in NOT_OPERATIONAL with a reason.'
    ).toEqual([]);
  });

  it('draws a card for each operational tab', () => {
    for (const [id, name] of Object.entries(OPERATIONAL)) {
      expect(controlTower, `${id} is missing its "${name}" card`).toContain(`name: '${name}'`);
    }
  });

  it('draws no card for a tab that belongs elsewhere', () => {
    expect(controlTower).not.toContain("name: 'Demo Guide'");
    expect(controlTower).not.toContain("name: 'Multi-AI'");
  });

  it('counts its own cards rather than stating a number', () => {
    // The literal is how this drifted the first time.
    expect(source).toContain('{CONTROL_TOWER.length} operational tabs');
    expect(source).not.toMatch(/<SectionLabel>\d+ operational tabs<\/SectionLabel>/);
  });

  it('gives each card its own accent, so none reads as a duplicate', () => {
    const accents = [...controlTower.matchAll(/accent: '([^']+)'/g)].map((m) => m[1]);
    expect(accents).toHaveLength(Object.keys(OPERATIONAL).length);
    expect(new Set(accents).size).toBe(accents.length);
  });
});
