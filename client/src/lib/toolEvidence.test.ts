import { describe, it, expect } from 'vitest';
import { domainLabel, hasEvidence, rowSummary, rowSummaryTitle } from './toolEvidence';

// The evidence trail is the chat's answer to "why should I believe this",
// so the honesty of the row count matters more than its brevity: both tools
// truncate, and a capped count must not read as the size of the result set.

describe('hasEvidence', () => {
  it('is false for nothing at all', () => {
    expect(hasEvidence(undefined)).toBe(false);
    expect(hasEvidence({})).toBe(false);
  });

  it('is false when the only fields present are empty', () => {
    expect(hasEvidence({ tables: [] })).toBe(false);
  });

  it('is true for a zero row count, which is a real finding', () => {
    // Quality tables are empty on a healthy line; "no rows" is the answer.
    expect(hasEvidence({ rows: 0 })).toBe(true);
  });

  it.each([
    ['sql', { sql: 'SELECT 1' }],
    ['error', { error: 'boom' }],
    ['domain', { domain: 'quality' }],
    ['tables', { tables: ['mvm.quality.ncr'] }],
  ])('is true when %s is present', (_name, evidence) => {
    expect(hasEvidence(evidence)).toBe(true);
  });
});

describe('rowSummary', () => {
  it('returns null when the tool reported no count', () => {
    expect(rowSummary(undefined)).toBeNull();
    expect(rowSummary({})).toBeNull();
  });

  it('singularises one row', () => {
    expect(rowSummary({ rows: 1 })).toBe('1 row');
  });

  it('reads zero rows as words, not a bare number', () => {
    expect(rowSummary({ rows: 0 })).toBe('no rows');
  });

  it('counts plain rows', () => {
    expect(rowSummary({ rows: 14 })).toBe('14 rows');
  });

  it('marks a capped count as a floor', () => {
    expect(rowSummary({ rows: 20, rows_capped: true })).toBe('20+ rows');
    expect(rowSummary({ rows: 500, rows_capped: true })).toBe('500+ rows');
  });

  it('never says "no rows" for a capped count', () => {
    // A cap of zero is nonsense, but the phrasing must not contradict itself.
    expect(rowSummary({ rows: 0, rows_capped: true })).toBe('0+ rows');
  });
});

describe('rowSummaryTitle', () => {
  it('explains only the capped case', () => {
    expect(rowSummaryTitle({ rows: 14 })).toBeNull();
    expect(rowSummaryTitle({})).toBeNull();
    expect(rowSummaryTitle({ rows: 20, rows_capped: true })).toContain('20');
  });
});

describe('domainLabel', () => {
  it('names the governed space that answered', () => {
    expect(domainLabel('quality')).toBe('governed quality domain');
  });

  it('is null for tools that are not domain-scoped', () => {
    expect(domainLabel(undefined)).toBeNull();
    expect(domainLabel('')).toBeNull();
  });
});
