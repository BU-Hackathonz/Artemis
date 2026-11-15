import { defineConfig, loadEnv } from 'vite'
import react from '@vitejs/plugin-react'
import tailwindcss from '@tailwindcss/vite'
import { claudeDevRoute } from './dev/claudeRoute.ts'

// Plain Vite page for now; the Tauri shell wraps this same dev server later.
// loadEnv with the '' prefix reads every variable from .env.local into this Node process only;
// the browser bundle still sees VITE_-prefixed variables alone, so the API key never reaches it.
export default defineConfig(({ mode }) => ({
  plugins: [react(), tailwindcss(), claudeDevRoute(loadEnv(mode, process.cwd(), ''))],
  server: { port: 5173, strictPort: true },
  clearScreen: false,
}))
