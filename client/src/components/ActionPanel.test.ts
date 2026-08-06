import { describe, it, expect } from 'vitest';
import { readFileSync } from 'node:fs';
import { join } from 'node:path';
import { buildHandoff, type ActionItem } from './ActionPanel';

// The action chips are the visible end of the human-in-the-loop story, and for
// a long time they were theatre: the header read "Click to execute", clicking
// advanced a chip to a green ✅ "done", and nothing happened. No request was
// made, no ticket was raised, and the state was discarded on the next render.
// The ids the server emits (create_ticket, dispatch_tech, …) have no endpoint
// behind them; the one action route that exists drives demo infrastructure.
//
// A cosmetic button is bad; a cosmetic button that reports completion is worse,
// because someone stops chasing work the screen says is handled.
//
// Stripping the buttons removed the lie and left a list the panel itself told
// the operator to hand onward — by retyping it. The buttons are back and do
// exactly what the panel asks: copy a ready-to-paste handoff. These pin the
// panel to effects it really has, and keep the execution path available for
// when a connector exists.

const panel = readFileSync(join(__dirname, 'ActionPanel.tsx'), 'utf8');
const chatPanel = readFileSync(join(__dirname, 'ChatPanel.tsx'), 'utf8');

describe('the recommended actions panel', () => {
  it('does not promise execution when no handler is wired', () => {
    expect(panel).toContain('Proposed — not sent to any system');
    // Only reachable on the interactive branch now.
    const claim = panel.indexOf("'Click to execute'");
    expect(claim).toBeGreaterThan(-1);
    expect(panel.slice(claim - 120, claim)).toContain('interactive ?');
  });

  it('offers copy, not execution, on the unwired path', () => {
    expect(panel).toMatch(/interactive \? handleExecute\(action\) : void handleCopy\(action\)/);
    expect(panel).toContain('Copy handoff');
  });

  it('does not advance status when there is nothing to advance', () => {
    expect(panel).toMatch(/if \(!onActionClick \|\| action\.status === 'done'\) return;/);
    // The copy path must never touch status — that is the state that lied.
    const copy = panel.slice(panel.indexOf('const handleCopy'), panel.indexOf('if (!localActions.length)'));
    expect(copy).not.toContain('setLocalActions');
    expect(copy).not.toContain("'done'");
  });

  it('hides the done tick unless wired', () => {
    // A ✅ asserts a workflow state that does not exist without a connector.
    expect(panel).toMatch(/interactive && action\.status === 'done'/);
  });

  it('counts proposals rather than pending work when unwired', () => {
    // "2 pending" implies a queue someone is working; nothing is queued.
    expect(panel).toMatch(/interactive \? `\$\{pending\} pending` : `\$\{localActions\.length\} proposed`/);
  });

  it('says plainly that write-back is not implemented', () => {
    expect(panel).toMatch(/designed but not\s*\n?\s*implemented/);
  });

  it('tells the operator when the clipboard refused', () => {
    // Permission-gated and unavailable over plain http; a silent no-op here
    // would be the original bug in a new costume.
    expect(panel).toContain('Copy blocked');
  });

  it('is still rendered without an execution handler, so copy is the live path', () => {
    expect(chatPanel).toMatch(/<ActionPanel actions=\{msg\.actions\}/);
    expect(chatPanel).not.toContain('onActionClick=');
  });
});

const action: ActionItem = {
  id: 'create_ticket',
  title: 'Create Maintenance Ticket',
  description: 'Open a work order in CMMS for line',
  icon: 'ticket',
  system: 'CMMS / SAP PM',
  status: 'pending',
};

describe('the copied handoff', () => {
  const at = new Date('2026-08-06T10:04:00Z');

  it('carries what the receiving system needs', () => {
    const text = buildHandoff(action, 'Downtime Analyst', at);
    expect(text).toContain('Create Maintenance Ticket');
    expect(text).toContain('CMMS / SAP PM');
    expect(text).toContain('Open a work order in CMMS for line');
  });

  it('keeps the provenance so the item is traceable', () => {
    const text = buildHandoff(action, 'Downtime Analyst', at);
    expect(text).toContain('ProdLine CoPilot — Downtime Analyst');
    expect(text).toContain('2026-08-06 10:04 UTC');
  });

  it('states that a human still owns the decision', () => {
    expect(buildHandoff(action, undefined, at)).toContain('requires human approval');
  });

  it('never claims the item was sent', () => {
    const text = buildHandoff(action, 'Downtime Analyst', at).toLowerCase();
    expect(text).toContain('not sent to any system');
    expect(text).not.toContain('created');
    expect(text).not.toContain('submitted');
  });

  it('reads cleanly without a specialist', () => {
    expect(buildHandoff(action, undefined, at)).toContain('Proposed by: ProdLine CoPilot\n');
  });
});
