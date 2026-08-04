import { describe, it, expect } from 'vitest';
import { modelLabel } from './modelLabel';

// The attribution line is the only place the user learns that a different
// vendor answered this question than the last one, so it has to stay readable
// no matter which of the three fields the backend managed to supply.

describe('modelLabel', () => {
  it('names the model and its vendor when both are known', () => {
    expect(modelLabel('databricks-claude-sonnet-5', 'Claude Sonnet 5', 'Anthropic')).toBe(
      'Claude Sonnet 5 · Anthropic'
    );
  });

  it('falls back to the raw endpoint when there is no friendly name', () => {
    // Messages restored from the decision log predate the display fields.
    expect(modelLabel('databricks-gpt-5-mini')).toBe('databricks-gpt-5-mini');
  });

  it('shows the endpoint with a vendor when only the display name is missing', () => {
    expect(modelLabel('some-custom-endpoint', undefined, 'Meta')).toBe('some-custom-endpoint · Meta');
  });

  it('omits a vendor the backend could not identify', () => {
    // describe() returns "unknown" for an endpoint it cannot attribute;
    // printing "Foo · unknown" would look like a bug.
    expect(modelLabel('some-custom-endpoint', undefined, 'unknown')).toBe('some-custom-endpoint');
  });

  it('degrades to a readable placeholder rather than undefined', () => {
    expect(modelLabel()).toBe('unknown model');
    expect(modelLabel('', '', '')).toBe('unknown model');
  });

  it('ignores whitespace-only fields', () => {
    expect(modelLabel('databricks-gpt-5-4', '  ', '  ')).toBe('databricks-gpt-5-4');
  });
});
