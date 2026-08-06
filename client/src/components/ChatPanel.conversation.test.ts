import { describe, it, expect } from 'vitest';
import { readFileSync } from 'node:fs';
import { join } from 'node:path';

// Multi-turn memory is a whole server-side feature — a conversation store, a
// history window, a context overlay — that the UI switched off by omission.
// The server created a conversation, returned its id on every answer event and
// recorded both sides of every exchange; the client neither declared the field
// on its event type nor sent it back, so each question opened a new
// conversation and arrived with no idea what preceded it. Nothing errored, and
// "what about the next machine?" simply answered as though asked cold.
//
// Static assertions because the round-trip is a two-line contract in a large
// component, and the failure was that the lines were absent.

const chatPanel = readFileSync(join(__dirname, 'ChatPanel.tsx'), 'utf8');

describe('the conversation id round-trip', () => {
  it('is declared on the answer event, or it is dropped at the parse boundary', () => {
    const answerEvent = chatPanel.slice(
      chatPanel.indexOf("type: 'answer';"),
      chatPanel.indexOf("| { type: 'error'")
    );
    expect(answerEvent).toContain('conversation_id?: string');
  });

  it('is captured when an answer arrives', () => {
    expect(chatPanel).toMatch(/conversationIdRef\.current = event\.conversation_id/);
  });

  it('is sent on the next request', () => {
    const body = chatPanel.slice(
      chatPanel.indexOf('body: JSON.stringify({'),
      chatPanel.indexOf('signal: controller.signal')
    );
    expect(body).toContain('conversation_id: conversationIdRef.current');
  });

  it('is omitted rather than sent as null on the first turn', () => {
    // The server treats a falsy id as "start a new conversation", but sending
    // an explicit null through the JSON body is a different contract from
    // omitting the key, and only one of them is what the model expects.
    const body = chatPanel.slice(
      chatPanel.indexOf('body: JSON.stringify({'),
      chatPanel.indexOf('signal: controller.signal')
    );
    expect(body).toMatch(/\.\.\.\(conversationIdRef\.current \?/);
  });

  it('is dropped when the shift changes', () => {
    // A conversation row carries a shift_id. Carrying the thread across a
    // shift change would prime the model with a different production run.
    expect(chatPanel).toMatch(
      /conversationIdRef\.current = null;\s*\n\s*\}, \[shiftId\]\)/
    );
  });
});

// Token streaming. The answer used to arrive as one buffered `answer` event:
// tool chips animated, then a finished multi-paragraph reply appeared at once.
// The text now streams as the model writes it, but it remains a DRAFT — a
// second reasoning round can replace it and the synthesis pass rewrites it
// into the sectioned format the message bubble renders. Anything that treats
// the draft as the answer will format half a document and then swap it out.

describe('the streamed answer draft', () => {
  it('accumulates token frames', () => {
    expect(chatPanel).toMatch(/event\.type === 'token'[\s\S]{0,80}setDraft\(\(prev\) => prev \+ event\.content\)/);
  });

  it('discards the draft when the model abandons a preamble for a tool call', () => {
    expect(chatPanel).toMatch(/event\.type === 'token_reset'[\s\S]{0,60}setDraft\(''\)/);
  });

  it('drops the draft once the authoritative answer arrives', () => {
    const answerBranch = chatPanel.slice(chatPanel.indexOf("event.type === 'answer'"));
    expect(answerBranch.slice(0, 400)).toContain("setDraft('')");
  });

  it('clears the draft when a request ends for any reason', () => {
    const cleanup = chatPanel.slice(chatPanel.indexOf('} finally {'));
    expect(cleanup.slice(0, 260)).toContain("setDraft('')");
  });

  it('renders the draft as plain text, not as a parsed answer', () => {
    // parseStructuredResponse on a half-written document produces sections
    // that appear and rearrange as tokens land.
    expect(chatPanel).toMatch(/\{draft && \(/);
    expect(chatPanel).toMatch(/whitespace-pre-wrap[^)]*\{draft\}/s);
  });
});
