/**
 * The chips are the app's only hint about what it can be asked, so the rule that
 * matters is that it never offers a question this deployment cannot answer.
 *
 * The plant-documents assistant is opt-in and off by default. Offering a document
 * question without one produces a fluent "the documents could not be read" on
 * every click, which reads as a bug rather than as a feature nobody enabled.
 */
import { describe, expect, it } from 'vitest';

import { availableSuggestions, DOCUMENT_QUERIES, QUICK_QUERIES } from './chatSuggestions';

const NONE = new Set<string>();

describe('availableSuggestions', () => {
  it('offers the telemetry questions with no assistant provisioned', () => {
    const chips = availableSuggestions(NONE);
    expect(chips).toEqual(QUICK_QUERIES.flatMap((g) => g.queries));
  });

  it('withholds the document questions when the assistant is absent', () => {
    const chips = availableSuggestions(NONE, false);
    for (const q of DOCUMENT_QUERIES) expect(chips).not.toContain(q);
  });

  it('offers the document questions once the assistant is wired', () => {
    const chips = availableSuggestions(NONE, true);
    for (const q of DOCUMENT_QUERIES) expect(chips).toContain(q);
  });

  it('treats an unreachable topology as no assistant', () => {
    // `plant_docs` is optional on the response and the fetch can fail outright,
    // so undefined has to mean "do not offer" rather than "assume yes".
    expect(availableSuggestions(NONE, undefined)).not.toContain(DOCUMENT_QUERIES[0]);
  });

  it('drops what has already been asked, documents included', () => {
    const asked = new Set([QUICK_QUERIES[0].queries[0], DOCUMENT_QUERIES[0]]);
    const chips = availableSuggestions(asked, true);
    expect(chips).not.toContain(QUICK_QUERIES[0].queries[0]);
    expect(chips).not.toContain(DOCUMENT_QUERIES[0]);
    expect(chips).toContain(DOCUMENT_QUERIES[1]);
  });

  it('never returns the same chip twice', () => {
    const chips = availableSuggestions(NONE, true);
    expect(new Set(chips).size).toBe(chips.length);
  });

  it('keeps the conflict question, which is the one worth demonstrating', () => {
    expect(DOCUMENT_QUERIES[0]).toMatch(/conflicting/i);
  });
});
