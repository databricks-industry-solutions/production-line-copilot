/**
 * Tab identity, and the URL that addresses it.
 *
 * Tab state used to live only in component state, which meant nothing in the
 * app was linkable. That was tolerable while the tabs were only ever reached
 * by clicking, and stopped being tolerable with the Demo Guide: a walkthrough
 * step that describes a view has to be able to send you to it, and a reviewer
 * asking "look at this" has to have something to paste.
 *
 * The tab travels in the query string rather than the path. A path like
 * `/events` would need the server to fall back to index.html for unknown
 * routes, and the app is served by AppKit's `createApp` rather than by
 * anything in this repo, so that fallback is not ours to guarantee. `?tab=`
 * resolves at `/` however the server is configured, which is the difference
 * between a deep link that survives a hard reload and one that 404s in front
 * of an audience. `/admin` keeps the path it already had; it is a different
 * screen rather than a tab, and it already works.
 */

/** Tab bar order. The guide leads because a presenter should not hunt for it. */
export const TAB_IDS = ['guide', 'tower', 'alerts', 'ops', 'quality', 'multiai', 'about'] as const;

export type TabId = (typeof TAB_IDS)[number];

/**
 * Where the app opens with no tab in the URL.
 *
 * Deliberately the line and not the guide, even though the guide sits first in
 * the bar: someone opening the app cold should see a plant, not a set of
 * instructions about a plant. A presenter reaches the guide in one click, or
 * links straight to it.
 */
export const DEFAULT_TAB: TabId = 'tower';

/** The query parameter carrying the tab. */
const TAB_PARAM = 'tab';

export function isTabId(value: unknown): value is TabId {
  return typeof value === 'string' && (TAB_IDS as readonly string[]).includes(value);
}

/**
 * The tab a URL asks for, or null when it does not ask for one.
 *
 * Null rather than the default so callers can tell "no opinion" from "open the
 * line", which matters for whether a redundant `?tab=tower` gets written back
 * into the address bar.
 */
export function tabFromSearch(search: string): TabId | null {
  const raw = new URLSearchParams(search).get(TAB_PARAM);
  return isTabId(raw) ? raw : null;
}

/**
 * The search string that selects ``tab``, preserving every other parameter.
 *
 * The default tab is expressed by absence, so the common case stays a bare
 * `/` and a copied link carries a parameter only when it says something.
 */
export function searchWithTab(search: string, tab: TabId): string {
  const params = new URLSearchParams(search);
  if (tab === DEFAULT_TAB) params.delete(TAB_PARAM);
  else params.set(TAB_PARAM, tab);
  const query = params.toString();
  return query ? `?${query}` : '';
}

/**
 * A pasteable link to ``tab`` from the current location.
 *
 * Built from the live location rather than a stored base so it stays correct
 * under the workspace's app hostname, which nothing in the client knows.
 */
export function hrefForTab(tab: TabId, location: { pathname: string; search: string }): string {
  return `${location.pathname}${searchWithTab(location.search, tab)}`;
}
