/**
 * Renders `SystemMap` on its own so it can be screenshotted for the README.
 *
 * The real component, not a copy: it fetches `/api/ops/topology` and
 * `/api/ops/models` exactly as it does in the About tab, and the preview server
 * answers those two routes from fixtures captured off the deployed app (see
 * `scripts/dev/capture_system_map.mjs`). That way the picture in the README
 * shows a real deployment's wiring — four provisioned domains, the models that
 * actually answered — rather than numbers someone typed into a diagram.
 *
 * Not part of the app bundle: reachable only through `client/preview.html`,
 * which `client/vite.config.ts` never builds.
 */
import { createRoot } from 'react-dom/client';

import './index.css';
import SystemMap from './components/SystemMap';

createRoot(document.getElementById('root')!).render(
  // Screenshot target. The padding is the margin the capture crops to, and the
  // width matches the About tab's content column so the layout breaks where it
  // breaks in the app.
  <div id="capture" style={{ width: 1280, padding: 24 }}>
    <SystemMap />
  </div>
);
