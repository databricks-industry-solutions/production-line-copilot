import { describe, it, expect } from 'vitest';
import { formatCell } from './AskLinePanel';

// formatCell renders arbitrary Genie result-cell values (unknown) safely for
// the ask-line data table. It must never throw and never emit
// "[object Object]" for structured values.
describe('formatCell', () => {
  it('renders null/undefined as empty string', () => {
    expect(formatCell(null)).toBe('');
    expect(formatCell(undefined)).toBe('');
  });

  it('passes strings through unchanged', () => {
    expect(formatCell('FILLER')).toBe('FILLER');
    expect(formatCell('')).toBe('');
  });

  it('stringifies numbers and booleans', () => {
    expect(formatCell(82.5)).toBe('82.5');
    expect(formatCell(0)).toBe('0');
    expect(formatCell(true)).toBe('true');
    expect(formatCell(false)).toBe('false');
  });

  it('JSON-encodes objects and arrays instead of [object Object]', () => {
    expect(formatCell({ oee: 82 })).toBe('{"oee":82}');
    expect(formatCell([1, 2, 3])).toBe('[1,2,3]');
  });
});
