/**
 * Boots the React application into the DOM root.
 * Used once by Vite as the client entrypoint.
 * Wraps `App` in strict mode + error boundary so startup/runtime failures fail safely.
 */
import { StrictMode } from 'react';
import { createRoot } from 'react-dom/client';
import './index.css';
import App from './App.tsx';
import { ErrorBoundary } from './ErrorBoundary.tsx';

// ── Diagnostic: page lifecycle beacon ───────────────────────────────────
// Users report intermittent "blank page with loading sign" reloads during
// active demo use. We cannot tell from server logs whether the browser is
// reloading the page, the Databricks Apps platform is bouncing through an
// interstitial, or React is remounting after a connection loss. The
// Navigation Timing API records the navigation type definitively — a
// best-effort POST to /api/_client_beacon dumps it to stdout so we can
// pair page_exit with the next page_load on the same tab. Remove this
// block once root cause is identified.
(() => {
  try {
    const SESSION_KEY = 'lmak.diag.tab_id';
    let tabId: string;
    try {
      tabId = sessionStorage.getItem(SESSION_KEY) ?? '';
      if (!tabId) {
        tabId =
          typeof crypto !== 'undefined' && crypto.randomUUID
            ? crypto.randomUUID()
            : `${Date.now()}-${Math.random().toString(36).slice(2)}`;
        sessionStorage.setItem(SESSION_KEY, tabId);
      }
    } catch {
      tabId = `${Date.now()}-${Math.random().toString(36).slice(2)}`;
    }

    const navEntry = performance.getEntriesByType('navigation')[0] as PerformanceNavigationTiming | undefined;

    const send = (event: string, extra: Record<string, unknown> = {}) => {
      const payload = JSON.stringify({
        event,
        tab_id: tabId,
        ts: Date.now(),
        path: window.location.pathname + window.location.search,
        visibility: typeof document !== 'undefined' ? document.visibilityState : null,
        ...extra,
      });
      try {
        if (navigator.sendBeacon) {
          navigator.sendBeacon('/api/_client_beacon', new Blob([payload], { type: 'application/json' }));
        } else {
          fetch('/api/_client_beacon', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: payload,
            keepalive: true,
          }).catch(() => {});
        }
      } catch {
        // best-effort; never throw from telemetry
      }
    };

    send('page_load', {
      navigation_type: navEntry?.type ?? 'unknown',
      referrer: document.referrer || null,
      response_end_ms: navEntry ? Math.round(navEntry.responseEnd) : null,
      dom_content_loaded_ms: navEntry ? Math.round(navEntry.domContentLoadedEventEnd) : null,
      user_agent: navigator.userAgent,
    });

    let exitSent = false;
    const onExit = (reason: string) => {
      if (exitSent) return;
      exitSent = true;
      send('page_exit', { reason });
    };
    window.addEventListener('beforeunload', () => onExit('beforeunload'));
    window.addEventListener('pagehide', (e) => onExit(e.persisted ? 'pagehide_persisted' : 'pagehide'));
  } catch (err) {
    console.warn('[lifecycle-beacon] init failed', err);
  }
})();

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <ErrorBoundary>
      <App />
    </ErrorBoundary>
  </StrictMode>
);
