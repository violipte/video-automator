import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// Vite config: dev server proxies /api → FastAPI local (8500)
// Build → ../static-frontend2/ servido pelo FastAPI em prod
export default defineConfig({
  plugins: [react()],
  // base: '/v2/' pois servido em /v2 pelo FastAPI (durante migração)
  // Quando virar UI principal, mudar pra '/'
  base: '/v2/',
  server: {
    port: 3000,
    strictPort: true,
    proxy: {
      '/api': {
        target: 'http://localhost:8500',
        changeOrigin: true,
      },
    },
  },
  build: {
    outDir: '../static-frontend2',
    emptyOutDir: true,
    sourcemap: true,
  },
})
