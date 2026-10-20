import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'
import tailwindcss from '@tailwindcss/vite'
import vueDevTools from 'vite-plugin-vue-devtools'

export default defineConfig(({ command }) => ({
  plugins: [
    vue(),
    tailwindcss(),
    ...(command === 'serve' ? [vueDevTools()] : []),
  ],
  build: {
    target: ['es2020', 'safari15'],
    cssCodeSplit: false,
    modulePreload: false,
  },
  server: {
    proxy: {
      // Локальная разработка: фронт и бэк на разных портах (в проде — один домен через nginx).
      // Проксирует относительные fetch('/api/...') на локальный бэкенд.
      '/api': {
        target: process.env.VITE_API_BASE || 'http://127.0.0.1:8001',
        changeOrigin: true,
      },
      '/uploads': {
        target: process.env.VITE_API_BASE || 'http://127.0.0.1:8001',
        changeOrigin: true,
      },
    },
  },
}))