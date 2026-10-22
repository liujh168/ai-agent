import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'

// base './'：构建产物以相对路径引用资源，可挂载到 Gradio 的任意子路径（/courses/）
export default defineConfig({
  base: './',
  plugins: [vue()],
  build: {
    chunkSizeWarningLimit: 900,
  },
})
