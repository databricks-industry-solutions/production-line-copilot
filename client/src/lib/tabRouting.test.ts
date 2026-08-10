import { describe, it, expect } from 'vitest';

import {
  DEFAULT_TAB,
  TAB_IDS,
  hrefForTab,
  isTabId,
  searchWithTab,
  tabFromSearch,
  type TabId,
} from './tabRouting';

// Tab state used to be component state, so no view in the app could be linked
// to. The Demo Guide's walkthrough buttons are the first thing that depends on
// a tab being addressable, and a deep link that breaks on reload breaks in
// front of an audience — hence the reload case being pinned explicitly.

describe('reading a tab out of a URL', () => {
  it('finds the tab a link asks for', () => {
    expect(tabFromSearch('?tab=guide')).toBe('guide');
    expect(tabFromSearch('?tab=multiai')).toBe('multiai');
  });

  it('reports no opinion when the URL has none', () => {
    // Null rather than the default, so a bare '/' is not rewritten to
    // '/?tab=tower' the moment the app loads.
    expect(tabFromSearch('')).toBeNull();
    expect(tabFromSearch('?other=1')).toBeNull();
  });

  it('ignores a tab it does not have', () => {
    // A stale or hand-edited link should land on the default, not a blank page.
    expect(tabFromSearch('?tab=wharrgarbl')).toBeNull();
    expect(tabFromSearch('?tab=')).toBeNull();
  });

  it('survives the other parameters being present', () => {
    expect(tabFromSearch('?foo=1&tab=ops&bar=2')).toBe('ops');
  });
});

describe('writing a tab into a URL', () => {
  it('sets the tab', () => {
    expect(searchWithTab('', 'guide')).toBe('?tab=guide');
  });

  it('expresses the default tab by leaving it out', () => {
    // Otherwise every visit rewrites a clean '/' into '/?tab=tower'.
    expect(searchWithTab('?tab=guide', DEFAULT_TAB)).toBe('');
    expect(searchWithTab('', DEFAULT_TAB)).toBe('');
  });

  it('keeps parameters that belong to someone else', () => {
    const next = searchWithTab('?foo=1', 'ops');
    expect(next).toContain('foo=1');
    expect(next).toContain('tab=ops');
  });

  it('keeps other parameters even when clearing the tab', () => {
    expect(searchWithTab('?foo=1&tab=ops', DEFAULT_TAB)).toBe('?foo=1');
  });

  it('replaces rather than appends an existing tab', () => {
    const next = searchWithTab('?tab=guide', 'about');
    expect(next).toBe('?tab=about');
  });
});

describe('a link round-trips', () => {
  it.each(TAB_IDS)('%s survives being written and read back', (tab: TabId) => {
    // This is the reload path: what the button wrote into the address bar has
    // to be what the app reads on the way back in.
    const search = searchWithTab('', tab);
    expect(tabFromSearch(search) ?? DEFAULT_TAB).toBe(tab);
  });
});

describe('building a pasteable href', () => {
  it('hangs the tab off the current path', () => {
    expect(hrefForTab('ops', { pathname: '/', search: '' })).toBe('/?tab=ops');
  });

  it('does not invent a query for the default tab', () => {
    expect(hrefForTab(DEFAULT_TAB, { pathname: '/', search: '' })).toBe('/');
  });
});

describe('the tab list itself', () => {
  it('recognises exactly its own members', () => {
    for (const tab of TAB_IDS) expect(isTabId(tab)).toBe(true);
    expect(isTabId('admin')).toBe(false);
    expect(isTabId(undefined)).toBe(false);
    expect(isTabId(7)).toBe(false);
  });

  it('leads with the guide and defaults to the line', () => {
    // Order is a product decision: a presenter should find the guide first,
    // while someone opening the app cold should see a plant rather than
    // instructions about a plant.
    expect(TAB_IDS[0]).toBe('guide');
    expect(DEFAULT_TAB).toBe('tower');
  });

  it('has no duplicates', () => {
    expect(new Set(TAB_IDS).size).toBe(TAB_IDS.length);
  });
});
