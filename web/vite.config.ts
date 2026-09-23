import { defineConfig, type Plugin } from "vite";
import react from "@vitejs/plugin-react";
import tailwindcss from "@tailwindcss/vite";
import path from "path";

const BACKEND = process.env.BASSERA_DASHBOARD_URL ?? "http://127.0.0.1:9119";

/**
 * In production the Python `bassera dashboard` server injects a one-shot
 * session token into `index.html` (see `bassera_cli/web_server.py`). The
 * Vite dev server serves its own `index.html`, so unless we forward that
 * token, every protected `/api/*` call 401s.
 *
 * This plugin fetches the running dashboard's `index.html` on each dev page
 * load, scrapes the `window.__BASSERA_SESSION_TOKEN__` assignment, and
 * re-injects it into the dev HTML. No-op in production builds.
 */
function basseraDevToken(): Plugin {
  const TOKEN_RE = /window\.__BASSERA_SESSION_TOKEN__\s*=\s*"([^"]+)"/;

  return {
    name: "bassera:dev-session-token",
    apply: "serve",
    async transformIndexHtml() {
      try {
        const res = await fetch(BACKEND, { headers: { accept: "text/html" } });
        const html = await res.text();
        const match = html.match(TOKEN_RE);
        if (!match) {
          console.warn(
            `[bassera] Could not find session token in ${BACKEND} — ` +
              `is \`bassera dashboard\` running? /api calls will 401.`,
          );
          return;
        }
        return [
          {
            tag: "script",
            injectTo: "head",
            children: `window.__BASSERA_SESSION_TOKEN__="${match[1]}";`,
          },
        ];
      } catch (err) {
        console.warn(
          `[bassera] Dashboard at ${BACKEND} unreachable — ` +
            `start it with \`bassera dashboard\` or set BASSERA_DASHBOARD_URL. ` +
            `(${(err as Error).message})`,
        );
      }
    },
  };
}

export default defineConfig({
  plugins: [react(), tailwindcss(), basseraDevToken()],
  resolve: {
    alias: {
      "@": path.resolve(__dirname, "./src"),
    },
  },
  build: {
    outDir: "../bassera_cli/web_dist",
    emptyOutDir: true,
  },
  server: {
    proxy: {
      "/api": BACKEND,
    },
  },
});
