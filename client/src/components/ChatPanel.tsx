/**
 * Hosts the streaming copilot chat UI, including quick snapshot + final specialist answer flow.
 * Used in `App` Events & AI tab for line-manager questions across all 8 specialists.
 * SSE-style event parsing preserves partial status/tool updates while long analyses are still running.
 */
import { useState, useRef, useEffect, useCallback, useMemo } from 'react';
import Markdown from 'react-markdown';
import { cn } from '../lib/utils';
import { addDecision } from '../lib/decisionLogStorage';
import { modelLabel } from '../lib/modelLabel';
import {
  domainLabel,
  hasEvidence,
  rowSummary,
  rowSummaryTitle,
  type ToolEvidence,
} from '../lib/toolEvidence';
import ActionPanel from './ActionPanel';
import DataSourceBadge from './DataSourceBadge';

const CHAT_SOURCE_DETAILS: Record<string, string> = {
  MES: 'Production events, commands, and shift context used in grounding.',
  SCADA: 'Live machine telemetry used in operational reasoning.',
  'SAP MM': 'Material/inventory context for shortage and replenishment analysis.',
  LIMS: 'Quality and lab signals referenced for deviation diagnostics.',
  CMMS: 'Maintenance history and incident remediation suggestions.',
};

export interface ReasoningStep {
  label: string;
  detail: string;
  /** Present on tool steps: the query that ran and what it returned. */
  evidence?: ToolEvidence;
}

export interface ActionItem {
  id: string;
  title: string;
  description: string;
  icon: string;
  system: string;
  status: 'pending' | 'in_progress' | 'done';
}

interface AgentErrorResponse {
  error?: string;
}

type StreamEvent =
  | { type: 'snapshot'; content: string }
  | { type: 'status'; content: string }
  | { type: 'tool_update'; content: string }
  | {
      type: 'answer';
      content: string;
      specialist_used?: string;
      model_used?: string;
      model_display?: string;
      model_vendor?: string;
      response_time_ms?: number;
      reasoning_steps?: ReasoningStep[];
      /** Absent unless the server has both a workspace host and an experiment. */
      trace_url?: string;
      actions?: ActionItem[];
    }
  | { type: 'error'; content: string };

export interface ChatMessage {
  id: string;
  role: 'user' | 'assistant' | 'snapshot';
  content: string;
  specialistUsed?: string;
  modelUsed?: string;
  modelDisplay?: string;
  modelVendor?: string;
  responseTimeMs?: number;
  reasoningSteps?: ReasoningStep[];
  traceUrl?: string;
  actions?: ActionItem[];
}

export interface ChatPanelProps {
  shiftId: string;
  timestamp: string;
  machineId: string | null;
  externalQuery?: { text: string; id: number; overrideTimestamp?: string };
}

const SHIFT_BRIEFING_QUERY =
  'Generate a shift start briefing: production plan, carry-over issues, material status, crew, and maintenance schedule.';

const QUICK_QUERIES = [
  {
    category: 'Performance',
    queries: [
      'What is the current OEE and how can we improve it?',
      'Which machine is the biggest bottleneck right now?',
      'How does this shift compare to the plan so far?',
    ],
  },
  {
    category: 'Issues',
    queries: [
      'Summarize the active events and their impact',
      'Are there any material shortages I should worry about?',
      'What is the root cause of the latest downtime?',
    ],
  },
  {
    category: 'Planning',
    queries: [
      'Can we still meet the shift target? What needs to change?',
      'Predict potential issues for the rest of the shift',
      'What should I brief the next shift about?',
    ],
  },
];

const SPECIALIST_COLORS: Record<string, string> = {
  oee_coach: 'bg-green-500/20 text-green-700 dark:text-green-300',
  downtime_analyst: 'bg-red-500/20 text-red-700 dark:text-red-300',
  quality_specialist: 'bg-purple-500/20 text-purple-700 dark:text-purple-300',
  schedule_optimizer: 'bg-yellow-500/20 text-yellow-700 dark:text-yellow-300',
  maintenance_predictor: 'bg-orange-500/20 text-orange-700 dark:text-orange-300',
  supply_chain_advisor: 'bg-cyan-500/20 text-cyan-700 dark:text-cyan-300',
  strategic_advisor: 'bg-blue-500/20 text-blue-700 dark:text-blue-300',
  orchestrator: 'bg-muted text-muted-foreground',
};

const SPECIALIST_LABELS: Record<string, string> = {
  oee_coach: 'OEE Coach',
  downtime_analyst: 'Downtime Analyst',
  quality_specialist: 'Quality Specialist',
  schedule_optimizer: 'Schedule Optimizer',
  maintenance_predictor: 'Maintenance Predictor',
  supply_chain_advisor: 'Supply Chain Advisor',
  strategic_advisor: 'Strategic Advisor',
  orchestrator: 'Orchestrator',
};

/**
 * The checkable half of a tool step: which governed space answered, which
 * tables were touched, how many rows came back, and the query itself.
 *
 * Styling mirrors AskLinePanel's provenance block so the same facts look the
 * same wherever the app shows them.
 */
function EvidenceTrail({ evidence }: { evidence: ToolEvidence }) {
  const rows = rowSummary(evidence);
  const rowsTitle = rowSummaryTitle(evidence);
  const domain = domainLabel(evidence.domain);
  // Deduped: the table name is the React key, and a query that reads one table
  // twice (a self-join, a CTE re-read) would otherwise render the same chip
  // twice under a duplicate-key warning.
  const tables = [...new Set(evidence.tables ?? [])];
  const hasChips = Boolean(domain) || Boolean(rows) || tables.length > 0;

  return (
    <div className="mt-1 space-y-1 border-l border-border/60 pl-2">
      {evidence.error && (
        <div className="text-amber-600 dark:text-amber-400">Failed — {evidence.error}</div>
      )}
      {!evidence.error && hasChips && (
        <div className="flex flex-wrap items-center gap-1">
          {domain && (
            <span className="rounded-full bg-violet-500/15 px-1.5 py-0.5 text-[10px] font-medium text-violet-700 dark:text-violet-300">
              {domain}
            </span>
          )}
          {tables.map((table) => (
            <span
              key={table}
              className="rounded-full bg-blue-500/15 px-1.5 py-0.5 font-mono text-[10px] text-blue-700 dark:text-blue-300"
            >
              {table}
            </span>
          ))}
          {rows && (
            <span className="text-[10px] text-muted-foreground/80" title={rowsTitle ?? undefined}>
              {rows}
            </span>
          )}
        </div>
      )}
      {evidence.sql && (
        <details>
          <summary className="cursor-pointer select-none text-[10px] font-semibold uppercase tracking-wide text-muted-foreground hover:text-foreground">
            Query
          </summary>
          <pre className="mt-1 max-h-40 overflow-auto rounded bg-muted/60 p-1.5 text-[10px] leading-snug">
            <code>{evidence.sql}</code>
          </pre>
        </details>
      )}
    </div>
  );
}

function ThinkingIndicator({
  statusText,
  elapsed,
  toolSteps,
}: {
  statusText: string;
  elapsed: number;
  toolSteps: string[];
}) {
  return (
    <div className="flex justify-start">
      <div className="rounded-xl bg-muted/60 px-4 py-3 max-w-[80%]">
        <div className="flex items-center gap-2.5">
          <div className="flex gap-1">
            <span className="h-2 w-2 rounded-full bg-primary animate-bounce [animation-delay:0ms]" />
            <span className="h-2 w-2 rounded-full bg-primary animate-bounce [animation-delay:150ms]" />
            <span className="h-2 w-2 rounded-full bg-primary animate-bounce [animation-delay:300ms]" />
          </div>
          <span className="text-xs text-muted-foreground animate-pulse">{statusText}</span>
          <span className="text-[11px] text-muted-foreground/60 tabular-nums">{elapsed}s</span>
        </div>
        {toolSteps.length > 0 && (
          <div className="mt-2 space-y-0.5 border-t border-border/50 pt-1.5">
            {toolSteps.map((step) => (
              <div key={step} className="flex items-center gap-1.5 text-[11px] text-muted-foreground">
                <span className={step.includes('✓') ? 'text-green-500' : 'text-blue-500 animate-pulse'}>
                  {step.includes('✓') ? '✓' : '▸'}
                </span>
                <span>{step.replace(' ✓', '')}</span>
              </div>
            ))}
          </div>
        )}
      </div>
    </div>
  );
}

const SECTION_META: Record<string, { icon: string; accent: string; bg: string }> = {
  'Root Cause': { icon: '🔍', accent: 'text-red-600 dark:text-red-400', bg: 'border-red-500/30 bg-red-500/5' },
  Impact: { icon: '⚡', accent: 'text-orange-600 dark:text-orange-400', bg: 'border-orange-500/30 bg-orange-500/5' },
  'Priority Actions': {
    icon: '🎯',
    accent: 'text-blue-600 dark:text-blue-400',
    bg: 'border-blue-500/30 bg-blue-500/5',
  },
  'Estimated Recovery': {
    icon: '⏱️',
    accent: 'text-green-600 dark:text-green-400',
    bg: 'border-green-500/30 bg-green-500/5',
  },
  Confidence: {
    icon: '📊',
    accent: 'text-purple-600 dark:text-purple-400',
    bg: 'border-purple-500/30 bg-purple-500/5',
  },
  Situation: { icon: '📋', accent: 'text-blue-600 dark:text-blue-400', bg: 'border-blue-500/30 bg-blue-500/5' },
  'Key Risks': { icon: '⚠️', accent: 'text-amber-600 dark:text-amber-400', bg: 'border-amber-500/30 bg-amber-500/5' },
  Outlook: { icon: '📈', accent: 'text-green-600 dark:text-green-400', bg: 'border-green-500/30 bg-green-500/5' },
  'Current State': {
    icon: '📊',
    accent: 'text-slate-600 dark:text-slate-400',
    bg: 'border-slate-500/30 bg-slate-500/5',
  },
  Recommendation: {
    icon: '💡',
    accent: 'text-indigo-600 dark:text-indigo-400',
    bg: 'border-indigo-500/30 bg-indigo-500/5',
  },
  'Expected Outcome': {
    icon: '🎯',
    accent: 'text-emerald-600 dark:text-emerald-400',
    bg: 'border-emerald-500/30 bg-emerald-500/5',
  },
};

const ALL_KNOWN_SECTIONS = [
  'Root Cause',
  'Impact',
  'Priority Actions',
  'Estimated Recovery',
  'Confidence',
  'Situation',
  'Key Risks',
  'Outlook',
  'Current State',
  'Recommendation',
  'Expected Outcome',
];

function parseStructuredResponse(
  content: string
): { preamble: string; sections: Array<{ title: string; body: string }> } | null {
  const h2Regex = /^## (.+)$/gm;
  const headings: Array<{ title: string; idx: number }> = [];
  let match;
  while ((match = h2Regex.exec(content)) !== null) {
    const title = match[1].trim();
    if (ALL_KNOWN_SECTIONS.includes(title)) {
      headings.push({ title, idx: match.index });
    }
  }
  if (headings.length < 3) return null;

  const preamble = content.slice(0, headings[0].idx).trim();
  const sections: Array<{ title: string; body: string }> = [];
  for (let i = 0; i < headings.length; i++) {
    const start = headings[i].idx + `## ${headings[i].title}`.length;
    const end = i + 1 < headings.length ? headings[i + 1].idx : content.length;
    sections.push({ title: headings[i].title, body: content.slice(start, end).trim() });
  }
  return { preamble, sections };
}

function StructuredResponse({ content }: { content: string }) {
  const parsed = useMemo(() => parseStructuredResponse(content), [content]);
  if (!parsed) return <Markdown>{content}</Markdown>;

  return (
    <div className="space-y-2">
      {parsed.preamble && (
        <div className="prose prose-sm dark:prose-invert max-w-none text-sm [&_p]:my-1">
          <Markdown>{parsed.preamble}</Markdown>
        </div>
      )}
      {parsed.sections.map((sec) => {
        const meta = SECTION_META[sec.title] ?? {
          icon: '📋',
          accent: 'text-foreground',
          bg: 'border-border bg-muted/30',
        };
        return (
          <details key={sec.title} open className={cn('rounded-lg border p-2.5', meta.bg)}>
            <summary
              className={cn('cursor-pointer text-xs font-bold uppercase tracking-wide select-none', meta.accent)}
            >
              {meta.icon} {sec.title}
            </summary>
            <div className="mt-1.5 prose prose-sm dark:prose-invert max-w-none text-sm [&_p]:my-1 [&_ul]:my-1 [&_ol]:my-1 [&_li]:my-0.5">
              <Markdown>{sec.body}</Markdown>
            </div>
          </details>
        );
      })}
    </div>
  );
}

const ALL_SUGGESTIONS = QUICK_QUERIES.flatMap((g) => g.queries);

export default function ChatPanel({ shiftId, timestamp, machineId, externalQuery }: ChatPanelProps) {
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [input, setInput] = useState('');
  const [isLoading, setIsLoading] = useState(false);
  const [statusText, setStatusText] = useState('Analyzing...');
  const [toolSteps, setToolSteps] = useState<string[]>([]);
  const [elapsed, setElapsed] = useState(0);
  const [askedQueries, setAskedQueries] = useState<Set<string>>(new Set());
  const messagesEndRef = useRef<HTMLDivElement>(null);
  const textareaRef = useRef<HTMLTextAreaElement>(null);
  const timerRef = useRef<ReturnType<typeof setInterval> | null>(null);
  const snapshotIdRef = useRef<string | null>(null);
  const lastExternalIdRef = useRef(0);

  const availableSuggestions = useMemo(() => ALL_SUGGESTIONS.filter((q) => !askedQueries.has(q)), [askedQueries]);

  const scrollToBottom = () => {
    messagesEndRef.current?.scrollIntoView({ behavior: 'smooth' });
  };

  useEffect(() => {
    scrollToBottom();
  }, [messages, isLoading]);

  const startTimer = () => {
    setElapsed(0);
    const start = Date.now();
    timerRef.current = setInterval(() => {
      setElapsed(Math.floor((Date.now() - start) / 1000));
    }, 1000);
  };

  const stopTimer = () => {
    if (timerRef.current) {
      clearInterval(timerRef.current);
      timerRef.current = null;
    }
  };

  const submitQuery = useCallback(
    async (query: string, overrideTimestamp?: string) => {
      const effectiveTimestamp = overrideTimestamp || timestamp;
      if (!query || isLoading || !effectiveTimestamp) return;

      setAskedQueries((prev) => new Set(prev).add(query));

      const userMessage: ChatMessage = {
        id: crypto.randomUUID(),
        role: 'user',
        content: query,
      };
      setMessages((prev) => [...prev, userMessage]);
      setInput('');
      setIsLoading(true);
      setStatusText('Analyzing your question...');
      setToolSteps([]);
      snapshotIdRef.current = null;
      startTimer();

      const controller = new AbortController();
      const timeoutId = setTimeout(() => controller.abort(), 180000);

      try {
        const response = await fetch('/api/agent/stream', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            query,
            shift_id: shiftId,
            timestamp: effectiveTimestamp,
            machine_id: machineId,
          }),
          signal: controller.signal,
        });

        if (!response.ok) {
          const data = (await response.json().catch(() => ({ error: 'Request failed' }))) as AgentErrorResponse;
          const errorContent = typeof data.error === 'string' ? data.error : JSON.stringify(data);
          setMessages((prev) => [
            ...prev,
            { id: crypto.randomUUID(), role: 'assistant', content: `Error: ${errorContent}` },
          ]);
          return;
        }

        const reader = response.body?.getReader();
        if (!reader) throw new Error('No response stream');

        const decoder = new TextDecoder();
        let buffer = '';

        while (true) {
          const { done, value } = await reader.read();
          if (done) break;

          buffer += decoder.decode(value, { stream: true });

          const lines = buffer.split('\n');
          buffer = lines.pop() ?? '';

          for (const line of lines) {
            if (!line.startsWith('data: ')) continue;
            const payload = line.slice(6).trim();
            if (payload === '[DONE]') continue;

            try {
              const event = JSON.parse(payload) as StreamEvent;

              if (event.type === 'snapshot') {
                const snapId = crypto.randomUUID();
                snapshotIdRef.current = snapId;
                setMessages((prev) => [...prev, { id: snapId, role: 'snapshot', content: event.content }]);
                setStatusText('Running specialist analysis...');
              } else if (event.type === 'status') {
                setStatusText(event.content);
              } else if (event.type === 'tool_update') {
                setStatusText(event.content);
                setToolSteps((prev) => {
                  const label = event.content;
                  if (label.includes('✓')) {
                    const base = label.replace(' ✓', '');
                    return prev.map((s) => (s === base ? `${base} ✓` : s));
                  }
                  if (!prev.includes(label)) return [...prev, label];
                  return prev;
                });
              } else if (event.type === 'answer') {
                if (snapshotIdRef.current) {
                  setMessages((prev) => prev.filter((m) => m.id !== snapshotIdRef.current));
                }
                setMessages((prev) => [
                  ...prev,
                  {
                    id: crypto.randomUUID(),
                    role: 'assistant',
                    content: event.content,
                    specialistUsed: event.specialist_used,
                    modelUsed: event.model_used,
                    modelDisplay: event.model_display,
                    modelVendor: event.model_vendor,
                    responseTimeMs: event.response_time_ms,
                    reasoningSteps: event.reasoning_steps,
                    traceUrl: event.trace_url,
                    actions: event.actions,
                  },
                ]);
                addDecision(
                  query,
                  event.specialist_used ?? 'general',
                  event.content,
                  'noted',
                  event.reasoning_steps,
                  event.response_time_ms,
                  event.model_used
                );
              } else if (event.type === 'error') {
                setMessages((prev) => [
                  ...prev,
                  { id: crypto.randomUUID(), role: 'assistant', content: `Error: ${event.content}` },
                ]);
              }
            } catch {
              // skip unparseable lines
            }
          }
        }
      } catch (err) {
        setMessages((prev) => [
          ...prev,
          {
            id: crypto.randomUUID(),
            role: 'assistant',
            content: `Error: ${err instanceof Error ? err.message : 'Network or server error'}`,
          },
        ]);
      } finally {
        clearTimeout(timeoutId);
        setIsLoading(false);
        stopTimer();
        snapshotIdRef.current = null;
      }
    },
    [isLoading, timestamp, shiftId, machineId]
  );

  useEffect(() => {
    if (externalQuery && externalQuery.text.trim() && externalQuery.id !== lastExternalIdRef.current) {
      lastExternalIdRef.current = externalQuery.id;
      void submitQuery(externalQuery.text.trim(), externalQuery.overrideTimestamp);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps -- only re-fire when external query id changes
  }, [externalQuery?.id]);

  const handleSubmit = (e: React.FormEvent) => {
    e.preventDefault();
    void submitQuery(input.trim());
  };

  const handleKeyDown = (e: React.KeyboardEvent<HTMLTextAreaElement>) => {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault();
      void submitQuery(input.trim());
    }
  };

  const autoResize = () => {
    const el = textareaRef.current;
    if (!el) return;
    el.style.height = 'auto';
    el.style.height = Math.min(el.scrollHeight, 120) + 'px';
  };

  return (
    <div className="flex h-full flex-col rounded-lg border border-border bg-card">
      {/* Header */}
      <div className="flex shrink-0 items-center gap-2 border-b border-border px-4 py-2">
        <div
          className={cn(
            'h-2 w-2 rounded-full transition-colors',
            isLoading ? 'bg-yellow-500 animate-pulse' : 'bg-green-500'
          )}
        />
        <h2 className="text-sm font-semibold">ProdLine CoPilot</h2>
        {messages.length > 0 && <span className="text-xs text-muted-foreground">{messages.length} messages</span>}
        <DataSourceBadge
          sources={['MES', 'SCADA', 'SAP MM', 'LIMS', 'CMMS']}
          details={CHAT_SOURCE_DETAILS}
          className="ml-auto"
        />
      </div>

      {/* Messages */}
      <div className="flex-1 overflow-y-auto px-4 py-3 space-y-4">
        {messages.length === 0 && !isLoading && (
          <div className="flex flex-col items-center justify-center h-full gap-3 py-6">
            <svg
              width="36"
              height="36"
              viewBox="0 0 24 24"
              fill="none"
              stroke="currentColor"
              strokeWidth="1.5"
              className="text-muted-foreground/30"
            >
              <path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z" />
            </svg>
            <p className="text-sm text-muted-foreground">Ask about the shift, or pick a suggestion below</p>
            <button
              type="button"
              onClick={() => {
                void submitQuery(SHIFT_BRIEFING_QUERY);
              }}
              className={cn(
                'rounded-lg bg-primary px-4 py-2 text-sm font-semibold text-primary-foreground',
                'hover:bg-primary/90 transition-colors shadow-sm'
              )}
            >
              Shift Briefing
            </button>
          </div>
        )}

        {messages.map((msg) => (
          <div key={msg.id} className={cn('flex', msg.role === 'user' ? 'justify-end' : 'justify-start')}>
            <div
              className={cn(
                'max-w-[80%] rounded-xl px-4 py-3',
                msg.role === 'user'
                  ? 'bg-primary text-primary-foreground'
                  : msg.role === 'snapshot'
                    ? 'bg-blue-500/10 border border-blue-500/30'
                    : 'bg-muted/60'
              )}
            >
              {msg.role === 'snapshot' && (
                <div className="mb-1.5 flex items-center gap-1.5">
                  <span className="inline-block rounded-full bg-blue-500/20 px-2 py-0.5 text-[11px] font-semibold text-blue-700 dark:text-blue-300">
                    Quick Snapshot
                  </span>
                  {isLoading && (
                    <span className="text-[11px] text-muted-foreground animate-pulse">
                      Deep analysis in progress...
                    </span>
                  )}
                </div>
              )}
              {msg.role === 'assistant' && msg.specialistUsed && (
                <div className="mb-2 flex flex-wrap items-center gap-1.5">
                  <span
                    className={cn(
                      'inline-block rounded-full px-2.5 py-0.5 text-[11px] font-semibold',
                      SPECIALIST_COLORS[msg.specialistUsed] ?? 'bg-muted text-muted-foreground'
                    )}
                  >
                    {SPECIALIST_LABELS[msg.specialistUsed] ?? msg.specialistUsed}
                  </span>
                  {/* Which vendor's model answered. Routing to a different one
                      per question is the whole point, and it is invisible
                      unless the answer says so. */}
                  {(msg.modelDisplay || msg.modelUsed) && (
                    <span
                      className="inline-block rounded-full border border-border px-2 py-0.5 text-[10px] font-medium text-muted-foreground"
                      title={msg.modelUsed}
                    >
                      {modelLabel(msg.modelUsed, msg.modelDisplay, msg.modelVendor)}
                    </span>
                  )}
                </div>
              )}
              {msg.role === 'user' ? (
                <p className="text-sm whitespace-pre-wrap">{msg.content}</p>
              ) : msg.role === 'assistant' ? (
                <div className="prose prose-sm dark:prose-invert max-w-none text-sm [&_p]:my-1 [&_ul]:my-1 [&_ol]:my-1 [&_li]:my-0.5 [&_code]:text-xs [&_pre]:text-xs [&_h3]:text-sm [&_h4]:text-xs">
                  <StructuredResponse content={msg.content} />
                </div>
              ) : (
                <div className="prose prose-sm dark:prose-invert max-w-none text-sm [&_p]:my-1 [&_ul]:my-1 [&_ol]:my-1 [&_li]:my-0.5">
                  <Markdown>{msg.content}</Markdown>
                </div>
              )}
              {msg.role === 'assistant' && msg.actions && msg.actions.length > 0 && (
                <ActionPanel actions={msg.actions} />
              )}
              {msg.role === 'assistant' && msg.specialistUsed && (
                <details className="mt-2 border-t border-border/40 pt-1.5">
                  <summary className="cursor-pointer text-[11px] font-semibold text-muted-foreground hover:text-foreground select-none">
                    How this decision was made{' '}
                    {msg.responseTimeMs != null ? `(${(msg.responseTimeMs / 1000).toFixed(1)}s)` : ''}
                  </summary>
                  <div className="mt-1.5 text-[11px] text-muted-foreground">
                    {msg.reasoningSteps && msg.reasoningSteps.length > 0 ? (
                      <div className="space-y-1">
                        {msg.reasoningSteps.map((step, i) => (
                          // Index in the key: a specialist can call the same tool
                          // twice, and those steps differ only by their evidence.
                          // The list is append-only and never reordered or
                          // filtered, which is the case the rule guards against.
                          // eslint-disable-next-line react/no-array-index-key
                          <div key={`${i}-${step.label}`} className="flex items-start gap-1.5">
                            <span
                              className={cn(
                                'mt-px shrink-0',
                                step.label.includes('LLM') ? 'text-violet-500' : 'text-green-500'
                              )}
                            >
                              {step.label.includes('LLM') ? '◆' : '✓'}
                            </span>
                            <div className="min-w-0 flex-1">
                              <span className="font-semibold">{step.label}</span>
                              <span className="ml-1 text-muted-foreground/70">— {step.detail}</span>
                              {hasEvidence(step.evidence) && <EvidenceTrail evidence={step.evidence} />}
                            </div>
                          </div>
                        ))}
                      </div>
                    ) : (
                      <div className="space-y-1">
                        <div className="flex items-center gap-1.5">
                          <span className="text-violet-500">◆</span>
                          <span>
                            <span className="font-semibold">LLM reasoning</span> —{' '}
                            {SPECIALIST_LABELS[msg.specialistUsed] ?? msg.specialistUsed} via{' '}
                            {modelLabel(msg.modelUsed, msg.modelDisplay, msg.modelVendor)}
                          </span>
                        </div>
                        <div className="flex items-center gap-1.5">
                          <span className="text-green-500">✓</span>
                          <span>
                            <span className="font-semibold">Context data</span> — Used pre-loaded shift KPIs, events,
                            and machine states
                          </span>
                        </div>
                      </div>
                    )}
                    {/* Under the prose explanation, the raw article: spans,
                        prompts, tokens, latency. Absent when the server has no
                        experiment to point at, and a dead end for anyone
                        without workspace access — which is why it sits in the
                        collapsed expander rather than on the message. */}
                    {msg.traceUrl && (
                      <a
                        href={msg.traceUrl}
                        target="_blank"
                        rel="noopener noreferrer"
                        title="Opens this exact call in the Databricks MLflow UI. Requires workspace access."
                        className="mt-1.5 inline-flex items-center gap-1 font-semibold text-muted-foreground/80 underline decoration-dotted underline-offset-2 hover:text-foreground"
                      >
                        Full trace in MLflow
                        <span aria-hidden="true">↗</span>
                      </a>
                    )}
                  </div>
                </details>
              )}
            </div>
          </div>
        ))}

        {isLoading && <ThinkingIndicator statusText={statusText} elapsed={elapsed} toolSteps={toolSteps} />}
        <div ref={messagesEndRef} />
      </div>

      {/* Persistent suggestion chips */}
      {availableSuggestions.length > 0 && !isLoading && (
        <div className="shrink-0 border-t border-border/50 px-4 py-1.5 flex flex-wrap gap-1.5 overflow-x-auto">
          {availableSuggestions.map((q) => (
            <button
              key={q}
              type="button"
              onClick={() => {
                void submitQuery(q);
              }}
              className={cn(
                'rounded-full border border-border bg-background px-2.5 py-1 text-[11px] leading-tight whitespace-nowrap',
                'hover:bg-accent hover:text-accent-foreground transition-colors'
              )}
            >
              {q}
            </button>
          ))}
        </div>
      )}

      {/* Input */}
      <form onSubmit={handleSubmit} className="flex shrink-0 items-end gap-2 border-t border-border px-4 py-3">
        <textarea
          ref={textareaRef}
          value={input}
          onChange={(e) => {
            setInput(e.target.value);
            autoResize();
          }}
          onKeyDown={handleKeyDown}
          placeholder="Ask about the shift... (Shift+Enter for newline)"
          rows={1}
          className={cn(
            'flex-1 resize-none rounded-lg border border-input bg-background px-3 py-2 text-sm',
            'ring-offset-background placeholder:text-muted-foreground',
            'focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2',
            'disabled:cursor-not-allowed disabled:opacity-50',
            'max-h-[120px]'
          )}
          disabled={isLoading}
        />
        <button
          type="submit"
          disabled={isLoading || !input.trim()}
          className={cn(
            'shrink-0 rounded-lg bg-primary px-4 py-2 text-sm font-medium text-primary-foreground',
            'hover:bg-primary/90 disabled:cursor-not-allowed disabled:opacity-50',
            'transition-colors'
          )}
        >
          Send
        </button>
      </form>
    </div>
  );
}
