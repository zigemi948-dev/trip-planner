import { defineConfig } from 'vite';
import vue from '@vitejs/plugin-vue';

export default defineConfig({
  plugins: [vue()],
  server: {
    port: 5173,
    // Keep frontend code using relative /api and /ws URLs in development.
    proxy: {
      '/api': 'http://localhost:8000',
      '/health': 'http://localhost:8000',
      '/ws': {
        target: 'ws://localhost:8000',
        ws: true
      }
    }
  },
  // Skip source map processing for mapbox-gl to avoid esbuild parsing failures
  // with the large mapbox-gl.js.map file (6.5 MB).
  build: {
    rollupOptions: {
      onLog(level, log) {
        if (log.code === 'SOURCEMAP_ERROR') return;
      }
    }
  }
});