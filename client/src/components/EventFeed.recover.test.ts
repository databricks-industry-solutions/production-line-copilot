import { describe, it, expect } from 'vitest';
import { readFileSync } from 'node:fs';
import { join } from 'node:path';

// "Recover" clears a *live* fault: the endpoint resolves the row in
// ai_serving.scenario_commands that the producer is holding the fault open for.
// An event with no such command resolves nothing and the endpoint says so
// plainly, with `{"count": 0, "status": "no_active_incident"}` and HTTP 200.
//
// The handler used to discard that result, on the stated assumption that a
// state refresh would clarify the outcome. Nothing refreshes when nothing was
// resolved, so the button read "Recovering…", returned to "Recover", and the
// row stayed exactly where it was — indistinguishable from a fault that refused
// to clear. Two of the events it appears on can never be recovered: the seeded
// carryover event, which is a historical record with no producer state behind
// it, and any fault the producer has already cleared.
//
// The fix is only to say so. An operator who knows this is a past event stops
// clicking; one watching a button flicker does not.

const feed = readFileSync(join(__dirname, 'EventFeed.tsx'), 'utf8');

describe('the recover button', () => {
  it('reads the result instead of discarding it', () => {
    expect(feed).toMatch(/const result = await onRecoverMachine\(/);
  });

  it('reports when there was no fault to clear', () => {
    expect(feed).toMatch(/result\.count === 0/);
    expect(feed).toContain('No active fault on this machine');
  });

  it('distinguishes a failed call from a no-op', () => {
    // A thrown request and "nothing to resolve" are different outcomes and must
    // not collapse into one message.
    expect(feed).toContain('Recover failed');
    const note = feed.indexOf('Recover failed');
    const noop = feed.indexOf('No active fault on this machine');
    expect(note).toBeGreaterThan(-1);
    expect(noop).toBeGreaterThan(-1);
    expect(note).not.toEqual(noop);
  });

  it('renders the outcome where the operator is looking', () => {
    expect(feed).toMatch(/\{recoverNote && \(/);
    expect(feed).toContain('role="status"');
  });

  it('clears the previous note before retrying', () => {
    // A stale "no active fault" sitting under a fresh click would be worse than
    // no message at all.
    expect(feed).toMatch(/setRecovering\(true\);\s*\n\s*setRecoverNote\(null\);/);
  });

  it('offers itself on every active event, command or not', () => {
    // The button was gated on the machine having a live scenario_command,
    // because that was all Recover could resolve. The endpoint now appends the
    // resolve marker itself when nothing is holding the event open, so the
    // events the gate used to hide — the seeded carryover row, and anything a
    // producer restart orphaned — are the ones that most need the button.
    expect(feed).not.toContain('hasLiveFault');
    expect(feed).not.toContain('recoverableMachineIds');
  });

  it('still refuses the rows that are not incidents', () => {
    const gate = feed.slice(feed.indexOf('const canRecover ='), feed.indexOf('const canRecover =') + 300);
    expect(gate).toContain('active');
    expect(gate).toContain("event.event_type !== 'resolved'");
    expect(gate).toContain("!== 'info'");
  });

  it('keeps the count in the prop type so the check cannot be widened away', () => {
    // The prop was `Promise<unknown>`, which is what allowed the result to be
    // dropped without a type error in the first place.
    expect(feed).not.toContain('onRecoverMachine?: (machineId: string) => Promise<unknown>');
    expect(feed).toContain('Promise<{ count: number } | void>');
  });
});
