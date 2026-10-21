import react from '@vitejs/plugin-react';
import { defineConfig } from 'vitest/config';

// The dev server proxies REST and WebSocket traffic to Django, so the browser
// talks to a single origin (no CORS, and ws:// URLs derive from location.host).
const backend = process.env.BACKEND_URL ?? 'http://localhost:8000';

export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: {
      '/api': { target: backend, changeOrigin: true },
      '/ws': { target: backend.replace(/^http/, 'ws'), ws: true, changeOrigin: true },
    },
  },
  test: {
    environment: 'node',
    include: ['src/**/*.test.ts'],
  },
});
