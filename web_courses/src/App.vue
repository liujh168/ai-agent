<script setup lang="ts">
import Icon from './components/Icon.vue'

const courses = [
  {
    to: '/mcp',
    icon: 'radio',
    name: 'MCP 交互演示',
    desc: '客户端 ↔ 服务端 JSON-RPC 全会话',
    color: 'var(--emerald)',
  },
  {
    to: '/fc',
    icon: 'shield',
    name: 'FC 循环与安全门',
    desc: '工具调用循环 + 五级风险人工确认',
    color: 'var(--sky)',
  },
  {
    to: '/rag',
    icon: 'flask',
    name: '压缩 × RAG 实验',
    desc: '可动手调参的交互模拟器',
    color: 'var(--amber)',
  },
  {
    to: '/react',
    icon: 'split',
    name: 'Skill 路由 / ReAct 对比',
    desc: '同一道题，两种模式的 token 级对照',
    color: 'var(--fuchsia)',
  },
]
</script>

<template>
  <div class="layout">
    <!-- 左侧课程目录 -->
    <aside class="sidebar">
      <div class="brand">
        <div class="brand-title">AI Agent<br />交互课件中心</div>
        <div class="brand-sub">对照 docs/ 与源码学习</div>
      </div>
      <nav>
        <router-link
          v-for="c in courses"
          :key="c.to"
          :to="c.to"
          class="course-link"
          :class="{ active: $route.path === c.to }"
        >
          <span class="course-icon" :style="{ color: c.color }"><Icon :name="c.icon" :size="17" /></span>
          <span>
            <span class="course-name">{{ c.name }}</span>
            <span class="course-desc">{{ c.desc }}</span>
          </span>
        </router-link>
      </nav>
      <div class="sidebar-foot">
        嵌入式课件 · 数据源 /courses/<br />Gradio 页签内运行
      </div>
    </aside>

    <!-- 内容区 -->
    <main class="content">
      <router-view v-slot="{ Component }">
        <component :is="Component" />
      </router-view>
    </main>
  </div>
</template>

<style scoped>
.layout { display: flex; min-height: 100vh; }
.sidebar {
  width: 230px; flex-shrink: 0;
  background: var(--bg-soft);
  border-right: 1px solid var(--panel-border);
  display: flex; flex-direction: column;
  position: sticky; top: 0; height: 100vh;
}
.brand { padding: 20px 18px 14px; border-bottom: 1px solid var(--panel-border); }
.brand-title { font-size: 17px; font-weight: 800; line-height: 1.35; color: #fff; }
.brand-sub { font-size: 11px; color: var(--text-faint); margin-top: 6px; }
nav { padding: 12px 10px; display: flex; flex-direction: column; gap: 4px; flex: 1; }
.course-link {
  display: flex; gap: 10px; align-items: flex-start;
  padding: 9px 10px; border-radius: 8px; color: var(--text-dim);
  border: 1px solid transparent;
}
.course-link:hover { background: rgba(51, 65, 85, 0.4); text-decoration: none; color: var(--text); }
.course-link.active {
  background: rgba(56, 189, 248, 0.1); border-color: rgba(56, 189, 248, 0.35); color: #fff;
}
.course-icon { margin-top: 2px; }
.course-name { display: block; font-size: 13px; font-weight: 600; }
.course-desc { display: block; font-size: 10.5px; color: var(--text-faint); margin-top: 1px; }
.sidebar-foot { padding: 12px 18px; font-size: 10px; color: var(--text-faint); border-top: 1px solid var(--panel-border); line-height: 1.6; }
.content { flex: 1; min-width: 0; }

@media (max-width: 760px) {
  .layout { flex-direction: column; }
  .sidebar { width: 100%; height: auto; position: static; }
  nav { flex-direction: row; flex-wrap: wrap; }
}
</style>
