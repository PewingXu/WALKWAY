import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// Electron 打包后通过 file:// 加载，需要相对路径 base
export default defineConfig({
  base: './',
  plugins: [react()],
  server: {
    port: 5273,
    host: true,
  },
  build: {
    outDir: 'dist',
  },
})
