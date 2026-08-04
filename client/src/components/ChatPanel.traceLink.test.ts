import { describe, it, expect } from 'vitest';
import { readFileSync } from 'node:fs';
import { join } from 'node:path';

// The "Full trace in MLflow" link leaves the app for the Databricks workspace,
// which makes it the one anchor in the chat where a missing rel attribute or an
// unguarded render actually costs something. It replaced an inline span viewer
// that had been dead for a release, so these also pin the removal.

const chatPanel = readFileSync(join(__dirname, 'ChatPanel.tsx'), 'utf8');
const decisionLog = readFileSync(join(__dirname, 'DecisionLog.tsx'), 'utf8');
const storage = readFileSync(join(__dirname, '..', 'lib', 'decisionLogStorage.ts'), 'utf8');

describe('the MLflow trace link', () => {
  it('only renders when the server supplied a URL', () => {
    // The server omits trace_url off-platform. Rendering an anchor with an
    // undefined href would give every local answer a link to nowhere.
    expect(chatPanel).toContain('{msg.traceUrl && (');
  });

  it('opens in a new tab without handing the workspace a window opener', () => {
    const anchor = chatPanel.slice(chatPanel.indexOf('{msg.traceUrl && ('));
    expect(anchor).toContain('target="_blank"');
    expect(anchor).toContain('rel="noopener noreferrer"');
  });

  it('says where the link goes before the user clicks it', () => {
    // Anyone without workspace access lands on a login screen, so the
    // requirement belongs on the link rather than behind it.
    expect(chatPanel).toMatch(/title="[^"]*Requires workspace access\./);
  });

  it('lives inside the collapsed expander, not on the message', () => {
    const expanderStart = chatPanel.indexOf('How this decision was made');
    const linkStart = chatPanel.indexOf('{msg.traceUrl && (');
    expect(expanderStart).toBeGreaterThan(-1);
    expect(linkStart).toBeGreaterThan(expanderStart);
    expect(chatPanel.indexOf('</details>', linkStart)).toBeGreaterThan(linkStart);
  });
});

describe('the inline span viewer it replaced', () => {
  // Rebuilding it means mlflow.get_trace(id, flush=True), which blocks on the
  // async export queue on every answer to render a truncated copy of what the
  // MLflow UI shows in full.
  it('is gone from every surface that carried it', () => {
    for (const source of [chatPanel, decisionLog, storage]) {
      expect(source).not.toMatch(/TraceSpan|traceSpans|trace_spans/);
    }
  });
});
