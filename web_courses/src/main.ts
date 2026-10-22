import { createApp } from 'vue'
import { createRouter, createWebHashHistory } from 'vue-router'
import App from './App.vue'
import McpCourse from './courses/mcp/McpCourse.vue'
import FcCourse from './courses/fc/FcCourse.vue'
import RagCourse from './courses/rag/RagCourse.vue'
import ReactCourse from './courses/react/ReactCourse.vue'
import './style.css'

// Hash 路由：挂在任意子路径（Gradio iframe 的 /courses/）下都能正常工作
const router = createRouter({
  history: createWebHashHistory(),
  routes: [
    { path: '/', redirect: '/mcp' },
    { path: '/mcp', component: McpCourse, meta: { title: 'MCP 交互演示' } },
    { path: '/fc', component: FcCourse, meta: { title: 'Function Calling 与安全门' } },
    { path: '/rag', component: RagCourse, meta: { title: '上下文压缩 × RAG 实验' } },
    { path: '/react', component: ReactCourse, meta: { title: 'Skill 路由与 ReAct 对比' } },
  ],
})

createApp(App).use(router).mount('#app')
