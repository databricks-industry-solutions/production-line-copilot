import { describe, expect, it } from 'vitest';

import { rosterStats, vendorBreakdown } from './modelRosterView';
import type { ModelRoster, RoleModel } from './useModelRoster';

function role(partial: Partial<RoleModel> & { role: string; vendor: string }): RoleModel {
  return {
    endpoint: `databricks-${partial.role}`,
    display: partial.display ?? `${partial.vendor} model`,
    rationale: 'because',
    preferred_endpoint: `databricks-${partial.role}`,
    preferred_display: partial.display ?? `${partial.vendor} model`,
    preferred_vendor: partial.vendor,
    substituted: false,
    ...partial,
  } as RoleModel;
}

function roster(roles: RoleModel[], multi = true): ModelRoster {
  return {
    multi_vendor: multi,
    vendors: [...new Set(roles.map((r) => r.vendor))],
    substituted: roles.filter((r) => r.substituted).length,
    roles,
  };
}

describe('vendorBreakdown', () => {
  it('groups roles under the vendor that answers, busiest first', () => {
    const groups = vendorBreakdown(
      roster([
        role({ role: 'intent_router', vendor: 'Google' }),
        role({ role: 'downtime_analyst', vendor: 'Anthropic' }),
        role({ role: 'strategic_advisor', vendor: 'Anthropic' }),
      ]),
    );
    expect(groups.map((g) => g.vendor)).toEqual(['Anthropic', 'Google']);
    expect(groups[0].roles).toHaveLength(2);
  });

  it('breaks ties on roster order so the layout does not shuffle between reloads', () => {
    const groups = vendorBreakdown(
      roster([
        role({ role: 'a', vendor: 'Zhipu AI' }),
        role({ role: 'b', vendor: 'Meta' }),
      ]),
    );
    expect(groups.map((g) => g.vendor)).toEqual(['Zhipu AI', 'Meta']);
  });

  it('lists each distinct model once per vendor', () => {
    const groups = vendorBreakdown(
      roster([
        role({ role: 'a', vendor: 'OpenAI', display: 'GPT-5 mini' }),
        role({ role: 'b', vendor: 'OpenAI', display: 'GPT-5 mini' }),
        role({ role: 'c', vendor: 'OpenAI', display: 'GPT-5.4' }),
      ]),
    );
    expect(groups[0].models).toEqual(['GPT-5 mini', 'GPT-5.4']);
  });

  it('returns nothing before the roster loads', () => {
    expect(vendorBreakdown(null)).toEqual([]);
  });
});

describe('rosterStats', () => {
  it('counts vendors that are actually answering, not the ones we hoped for', () => {
    // Two roles fell back onto one OpenAI tier, so this workspace runs two
    // vendors even though the default roster names three.
    const stats = rosterStats(
      roster([
        role({ role: 'a', vendor: 'Anthropic' }),
        role({
          role: 'b',
          vendor: 'OpenAI',
          display: 'GPT-5 mini',
          preferred_vendor: 'Zhipu AI',
          substituted: true,
        }),
        role({
          role: 'c',
          vendor: 'OpenAI',
          display: 'GPT-5 mini',
          preferred_vendor: 'Google',
          substituted: true,
        }),
      ]),
    );
    expect(stats.vendorCount).toBe(2);
    expect(stats.substituted).toBe(2);
    expect(stats.fullRoster).toBe(false);
  });

  it('ignores vendors it could not identify', () => {
    const stats = rosterStats(
      roster([role({ role: 'a', vendor: 'Meta' }), role({ role: 'b', vendor: 'unknown' })]),
    );
    expect(stats.vendorCount).toBe(1);
  });

  it('reports a full roster when nothing was substituted', () => {
    const stats = rosterStats(
      roster([role({ role: 'a', vendor: 'Meta' }), role({ role: 'b', vendor: 'Google' })]),
    );
    expect(stats.fullRoster).toBe(true);
    expect(stats.substituted).toBe(0);
  });

  it('is safe before the roster loads', () => {
    expect(rosterStats(null)).toMatchObject({ vendorCount: 0, roleCount: 0, fullRoster: false });
  });
});
