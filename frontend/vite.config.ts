import { defineConfig } from 'vitest/config'
import { loadEnv } from 'vite'
import react from '@vitejs/plugin-react'

const normalizeBasePath = (value: string | undefined) => {
  const segment = (value || '').trim().replace(/^\/+|\/+$/g, '')
  return segment ? `/${segment}/` : '/'
}

export default defineConfig(({ mode }) => {
  const env = loadEnv(mode, '.', '')
  return {
    base: normalizeBasePath(env.VITE_BASE_PATH),
    plugins: [react()],
    test: {
      environment: 'jsdom',
      setupFiles: './src/test/setup.ts',
    },
    server: {
      host: '127.0.0.1',
      port: 4173,
    },
    preview: {
      host: '127.0.0.1',
      port: 4173,
    },
  }
})
