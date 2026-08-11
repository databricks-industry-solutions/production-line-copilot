/**
 * The suggestion chips under the chat box, and which of them this deployment can
 * actually answer.
 *
 * Split out of `ChatPanel` because the second part is a decision rather than
 * markup: the plant-documents assistant is opt-in and off by default, so a
 * document question offered in a deployment without one is answered "the
 * documents could not be read" — a broken feature to anyone who did not know the
 * feature was optional.
 */

export interface QuickQueryGroup {
  category: string;
  queries: string[];
}

/** Answerable everywhere: these read telemetry, the plan and the event history. */
export const QUICK_QUERIES: QuickQueryGroup[] = [
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

/**
 * Answered from the plant's written record instead of its telemetry, so they
 * need `query_plant_docs` and the Knowledge Assistant behind it.
 *
 * The first is deliberately a conflict: a governing specification and an
 * unapproved bulletin disagree about the pasteurisation window, and a good answer
 * says which one governs. That is the difference between retrieving a document
 * and reading it, and it is the one worth showing.
 */
export const DOCUMENT_QUERIES: string[] = [
  'What zone 2 temperature and PU window should the pasteurizer run at, and is there conflicting guidance?',
  'What is the capper lockout isolation sequence before I clear a jam?',
  'Per the changeover matrix, how long does a 0.5 L to 1.0 L changeover take and what is the line critical path?',
];

/**
 * Chips still worth offering: everything this deployment can answer, less what
 * has already been asked in this conversation.
 *
 * `plantDocs` comes from `/api/ops/topology`, which reads the app's own env
 * rather than trusting a build-time flag — the assistant can be provisioned, or
 * torn down, without rebuilding the client.
 */
export function availableSuggestions(asked: Set<string>, plantDocs = false): string[] {
  const all = QUICK_QUERIES.flatMap((g) => g.queries);
  if (plantDocs) all.push(...DOCUMENT_QUERIES);
  return all.filter((q) => !asked.has(q));
}
