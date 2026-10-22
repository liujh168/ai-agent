<script setup lang="ts">
import { ref } from 'vue'
import Icon from './Icon.vue'

const props = defineProps<{
  code: string
  lang?: string
  title?: string
  maxHeight?: number
}>()

const copied = ref(false)

async function copy() {
  try {
    await navigator.clipboard.writeText(props.code)
  } catch {
    const ta = document.createElement('textarea')
    ta.value = props.code
    document.body.appendChild(ta)
    ta.select()
    document.execCommand('copy')
    document.body.removeChild(ta)
  }
  copied.value = true
  setTimeout(() => (copied.value = false), 1500)
}

// —— 轻量语法高亮：JSON 按 key/string/number/bool 着色；Python 按关键字/字符串/注释 ——
function highlightLine(line: string, lang: string): string {
  const esc = (s: string) =>
    s.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
  if (lang === 'json') {
    const re =
      /("(?:\\u[a-fA-F0-9]{4}|\\[^u]|[^\\"])*")(\s*:)?|\b(true|false|null)\b|-?\d+(?:\.\d+)?(?:[eE][+-]?\d+)?/g
    return esc(line).replace(re, (m, str, colon) => {
      if (str !== undefined) {
        if (colon) return `<span style="color:#7dd3fc">${str}</span><span style="color:#64748b">${colon}</span>`
        return `<span style="color:#6ee7b7">${str}</span>`
      }
      if (/^(true|false|null)$/.test(m)) return `<span style="color:#e879f9">${m}</span>`
      return `<span style="color:#fcd34d">${m}</span>`
    })
  }
  if (lang === 'python' || lang === 'bash') {
    let out = esc(line)
    // 注释
    const ci = out.indexOf('#')
    let comment = ''
    if (ci >= 0) {
      comment = `<span style="color:#64748b;font-style:italic">${out.slice(ci)}</span>`
      out = out.slice(0, ci)
    }
    out = out
      .replace(/('(?:[^'\\]|\\.)*'|"(?:[^"\\]|\\.)*")/g, '<span style="color:#6ee7b7">$1</span>')
      .replace(
        /\b(def|class|import|from|return|if|elif|else|for|while|try|except|finally|with|as|not|and|or|None|True|False|lambda|pass|break|continue|raise|yield|global|in|is|assert|print|elif)\b/g,
        '<span style="color:#e879f9">$1</span>'
      )
    return out + comment
  }
  return esc(line)
}

const lines = props.code.replace(/\n$/, '').split('\n')
</script>

<template>
  <div class="codeblock">
    <div class="cb-head">
      <span class="dots"><i></i><i></i><i></i></span>
      <span class="cb-title">{{ title ?? lang?.toUpperCase() }}</span>
      <button class="cb-copy" @click="copy">
        <Icon :name="copied ? 'check' : 'copy'" :size="12" />
        {{ copied ? '已复制' : '复制' }}
      </button>
    </div>
    <pre :style="maxHeight ? `max-height:${maxHeight}px` : ''"><code><span v-for="(ln, i) in lines" :key="i" class="ln" v-html="highlightLine(ln, lang ?? 'json')" /></code></pre>
  </div>
</template>

<style scoped>
.codeblock {
  border: 1px solid var(--panel-border);
  border-radius: 8px;
  background: #0d1117;
  overflow: hidden;
}
.cb-head {
  display: flex; align-items: center; gap: 8px;
  background: rgba(51, 65, 85, 0.5);
  border-bottom: 1px solid var(--panel-border);
  padding: 5px 10px;
}
.dots { display: inline-flex; gap: 4px; }
.dots i { width: 9px; height: 9px; border-radius: 50%; }
.dots i:nth-child(1) { background: rgba(239, 68, 68, 0.7); }
.dots i:nth-child(2) { background: rgba(245, 158, 11, 0.7); }
.dots i:nth-child(3) { background: rgba(16, 185, 129, 0.7); }
.cb-title { font-size: 11px; color: var(--text-dim); margin-left: 4px; }
.cb-copy {
  margin-left: auto; display: inline-flex; align-items: center; gap: 4px;
  background: none; border: none; color: var(--text-dim); font-size: 11px;
  cursor: pointer; padding: 2px 6px; border-radius: 5px;
}
.cb-copy:hover { background: var(--panel); color: var(--text); }
pre {
  margin: 0; padding: 10px 12px; overflow: auto;
  font-size: 12px; line-height: 1.6; color: #cbd5e1;
  font-family: Consolas, "Courier New", monospace;
}
.ln { display: block; white-space: pre; }
</style>
