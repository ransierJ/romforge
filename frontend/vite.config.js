import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// Build output goes straight into the backend so one container serves both.
// In dev, /api is proxied to the FastAPI server on :8096.
export default defineConfig({
  plugins: [react()],
  build: {
    outDir: '../backend/static',
    emptyOutDir: true,
  },
  server: {
    port: 5173,
    proxy: {
      '/api': 'http://localhost:8096',
    },
  },
})
