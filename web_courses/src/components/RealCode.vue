<script setup lang="ts">
import CodeBlock from './CodeBlock.vue'
import Icon from './Icon.vue'

interface Mapping {
  step: string
  loc: string
}

withDefaults(
  defineProps<{
    title: string
    file: string
    code: string
    mapping: Mapping[]
    maxHeight?: number
  }>(),
  { maxHeight: 420 }
)
</script>

<template>
  <details class="real-code">
    <summary>
      <Icon name="code" :size="14" />
      <span class="rc-title">{{ title }}</span>
      <span class="rc-file">{{ file }}</span>
      <span class="rc-toggle">展开对照</span>
    </summary>
    <div class="rc-body">
      <table class="rc-map">
        <thead>
          <tr><th>课件里讲的</th><th>项目里跑的</th></tr>
        </thead>
        <tbody>
          <tr v-for="(m, i) in mapping" :key="i">
            <td>{{ m.step }}</td>
            <td><code>{{ m.loc }}</code></td>
          </tr>
        </tbody>
      </table>
      <CodeBlock :code="code" lang="python" :title="file" :max-height="maxHeight" />
    </div>
  </details>
</template>

<style scoped>
.real-code {
  border: 1px solid rgba(52, 211, 153, 0.3);
  border-radius: var(--radius);
  background: rgba(52, 211, 153, 0.04);
  margin-bottom: 12px;
  overflow: hidden;
}
summary {
  display: flex; align-items: center; gap: 8px;
  padding: 11px 14px; cursor: pointer; list-style: none;
  color: var(--emerald); font-size: 13px; user-select: none;
}
summary::-webkit-details-marker { display: none; }
summary:hover { background: rgba(52, 211, 153, 0.06); }
.rc-title { font-weight: 700; color: var(--text); }
.rc-file { font-size: 11px; color: var(--text-faint); font-family: Consolas, monospace; }
.rc-toggle { margin-left: auto; font-size: 11px; color: var(--emerald); }
details[open] .rc-toggle { display: none; }
.rc-body { padding: 0 14px 14px; }
.rc-map {
  width: 100%; border-collapse: collapse; margin-bottom: 10px; font-size: 12px;
}
.rc-map th, .rc-map td {
  border: 1px solid var(--panel-border); padding: 6px 10px; text-align: left;
}
.rc-map th { background: rgba(51, 65, 85, 0.4); color: var(--text-dim); font-weight: 600; }
.rc-map td:first-child { color: var(--text); width: 55%; }
.rc-map td code { color: var(--emerald); }
</style>
