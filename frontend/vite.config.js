import { defineConfig, loadEnv } from "vite";
import react from "@vitejs/plugin-react";
import { fileURLToPath } from "node:url";

export default defineConfig(({ mode }) => {
  // Read server-only credentials from the existing backend/.env.
  // These values are NOT exposed to browser code.
  const backendEnvDir = fileURLToPath(
    new URL("../backend/", import.meta.url)
  );

  const env = loadEnv(mode, backendEnvDir, "");
  const candidateToken = env.CANDIDATE_INGEST_TOKEN;
  const reviewToken = env.CANDIDATE_REVIEW_TOKEN;

  if (!reviewToken) {
    throw new Error(
      "Missing CANDIDATE_REVIEW_TOKEN in backend/.env"
    );
  }

  if (!candidateToken) {
    throw new Error(
      "Missing CANDIDATE_INGEST_TOKEN in backend/.env"
    );
  }

  return {
    plugins: [react()],

    server: {
      host: "127.0.0.1",

      proxy: {
        // Analysis needs no credentials; never attach tokens here.
        "/api/analysis": {
          target: "http://127.0.0.1:8000",
          changeOrigin: true,
        },

        "/api/candidates": {
          target: "http://127.0.0.1:8000",
          changeOrigin: true,

          
          configure(proxy) {
            proxy.on("proxyReq", (proxyReq, req) => {
              const pathname = (req.url ?? "").split("?")[0];

              // Existing GET access for listing candidates.
              if (
                req.method === "GET" &&
                pathname === "/api/candidates"
              ) {
                proxyReq.setHeader(
                  "X-Ingest-Token",
                  candidateToken
                );
              }

              // Development-only decision endpoint.
              const isDecisionRequest =
                req.method === "PATCH" &&
                /^\/api\/candidates\/[^/]+\/decision$/.test(pathname);

              // Only allow requests originating from our local Vite app.
              const allowedOrigins = new Set([
                "http://127.0.0.1:5173",
                "http://localhost:5173",
              ]);

              const origin = req.headers.origin;

              const isLocalApp =
                !origin || allowedOrigins.has(origin);

              if (isDecisionRequest && isLocalApp) {
                proxyReq.setHeader(
                  "X-Review-Token",
                  reviewToken
                );
              }
            });
          }
        },
      },
    },
  };
});
