import { describe, it, expect } from 'vitest';
import {
  optimizerMethodList,
  OPTIMIZER_METHODS,
  OPTIMIZER_TOOLS,
  readsLineOps,
  SPECIALISTS,
  toolMix,
} from './specialistCatalog';

// The System Map draws counts from these helpers instead of naming every tool,
// so a miscount is invisible on screen — it just reads as a slightly different
// number. These tests pin the claims the surrounding UI copy makes in prose.

/** Named in the map as what all eight carry, so the sentence must stay true. */
const BASELINE = ['sql_query', 'calculator', 'load_plan_and_constraints'];

describe('the shared baseline the map states once', () => {
  it.each(SPECIALISTS.map((s) => [s.name, s] as const))(
    '%s carries direct SQL, a calculator and the shift plan',
    (_name, s) => {
      for (const tool of BASELINE) expect(s.tools).toContain(tool);
    }
  );
});

describe('toolMix', () => {
  it('never counts genie_query, which gets its own chip', () => {
    const briefing = SPECIALISTS.find((s) => s.id === 'shift_briefing')!;
    expect(briefing.tools).toContain('genie_query');
    // sql_query, calculator, load_plan_and_constraints, retrieve_similar_cases
    expect(toolMix(briefing)).toEqual({ optimizers: 0, functions: 4 });
  });

  it('splits solvers from plain functions', () => {
    const schedule = SPECIALISTS.find((s) => s.id === 'schedule_optimizer')!;
    // schedule_recovery, stochastic_schedule_recovery, production_forecast
    expect(toolMix(schedule)).toEqual({ optimizers: 3, functions: 3 });
  });

  it('accounts for every tool exactly once', () => {
    for (const s of SPECIALISTS) {
      const { optimizers, functions } = toolMix(s);
      const genie = s.tools.filter((t) => t === 'genie_query').length;
      expect(optimizers + functions + genie).toBe(s.tools.length);
    }
  });

  it('leaves every specialist with functions to show', () => {
    // A zero would render "0 functions", which reads as a broken agent.
    for (const s of SPECIALISTS) expect(toolMix(s).functions).toBeGreaterThan(0);
  });
});

describe('OPTIMIZER_TOOLS', () => {
  it('holds the seven the map and the About tab both advertise', () => {
    expect(OPTIMIZER_TOOLS.size).toBe(7);
  });

  it('lists no solver that no specialist can call', () => {
    // Drift guard: a renamed optimizer would silently stop being counted.
    const wired = new Set(SPECIALISTS.flatMap((s) => s.tools));
    for (const tool of OPTIMIZER_TOOLS) expect(wired).toContain(tool);
  });
});

describe('readsLineOps', () => {
  it('is the three that reason beyond a single question', () => {
    const live = SPECIALISTS.filter(readsLineOps).map((s) => s.id).sort();
    expect(live).toEqual(['oee_coach', 'shift_briefing', 'strategic_advisor']);
  });
});

describe('the counts the UI states in prose', () => {
  // Both surfaces name these numbers in text. The System Map's optimizer card
  // read "7 deterministic optimizers" above a sentence naming six, having been
  // written by hand and never revisited when the seventh arrived. On a page
  // whose entire claim is that its numbers are real, the reviewer who counts is
  // the one you have to survive.
  it('every optimizer tool has a name for the prose', () => {
    expect(new Set(Object.keys(OPTIMIZER_METHODS))).toEqual(new Set(OPTIMIZER_TOOLS));
  });

  it('the method list names every one of them', () => {
    const list = optimizerMethodList();
    for (const method of Object.values(OPTIMIZER_METHODS)) {
      expect(list).toContain(method);
    }
    expect(list.split(', ')).toHaveLength(OPTIMIZER_TOOLS.size);
  });

  it('no two optimizers share a name', () => {
    const names = Object.values(OPTIMIZER_METHODS);
    expect(new Set(names).size).toBe(names.length);
  });

  it('the roster is the eight the map claims', () => {
    // SystemMap renders `${SPECIALISTS.length} specialist agents`; this pins the
    // number the surrounding copy was written against.
    expect(SPECIALISTS).toHaveLength(8);
  });

  it('every specialist carries at least one tool', () => {
    for (const s of SPECIALISTS) {
      expect(s.tools.length, `${s.id} has no tools`).toBeGreaterThan(0);
    }
  });
});
