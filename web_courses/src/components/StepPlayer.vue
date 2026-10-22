<script setup lang="ts">
import { computed, onBeforeUnmount, ref, watch, nextTick } from 'vue'
import Icon from './Icon.vue'
import CodeBlock from './CodeBlock.vue'
import { fmt, type StepNode } from '../types'

const props = withDefaults(
  defineProps<{
    nodes: Record<string, StepNode>
    start: string
    roleLeft?: string
    roleRight?: string
    leftDesc?: string
    rightDesc?: string
    intervalMs?: number
  }>(),
  {
    roleLeft: 'MCP Client（客户端）',
    roleRight: 'MCP Server（服务端）',
    leftDesc: '发起请求的一方',
    rightDesc: '响应请求的一方',
    intervalMs: 2600,
  }
)

// —— 路径状态机：path 为已展示的节点 id 序列，当前节点 = path 末尾 ——
const path = ref<string[]>([props.start])
const playing = ref(false)
let timer: number | null = null
const bottomRef = ref<HTMLElement>()

const currentId = computed(() => path.value[path.value.length - 1])
const current = computed(() => props.nodes[currentId.value])
const isEnd = computed(() => {
  const n = current.value
  return !n.next && !n.choices?.length
})
const shownNodes = computed(() => path.value.map((id) => props.nodes[id]))

function clearTimer() {
  if (timer !== null) {
    clearInterval(timer)
    timer = null
  }
}

function advance(defaultPick = false) {
  const n = current.value
  if (n.choices?.length) {
    if (!defaultPick) return false // 决策点：手动模式下等待用户选择
    path.value.push(n.choices[n.defaultChoice ?? 0].next)
    return true
  }
  if (n.next) {
    path.value.push(n.next)
    return true
  }
  return false
}

function onNext() {
  if (isEnd.value) return
  if (!advance(false) && current.value.choices) return
  if (isEnd.value) playing.value = false
}

function onChoose(index: number) {
  const n = current.value
  if (!n.choices?.[index]) return
  path.value.push(n.choices[index].next)
  if (isEnd.value) playing.value = false
}

function onPrev() {
  if (path.value.length > 1) path.value.pop()
  if (isEnd.value) playing.value = false
}

function onReset() {
  clearTimer()
  playing.value = false
  path.value = [props.start]
}

function togglePlay() {
  if (playing.value) {
    clearTimer()
    playing.value = false
    return
  }
  if (isEnd.value) path.value = [props.start]
  playing.value = true
  timer = window.setInterval(() => {
    if (!advance(true)) {
      clearTimer()
      playing.value = false
    }
  }, props.intervalMs)
}

watch(path, async () => {
  await nextTick()
  bottomRef.value?.scrollIntoView({ behavior: 'smooth', block: 'nearest' })
})

onBeforeUnmount(clearTimer)

const badgeText: Record<string, string> = {
  request: 'REQUEST 请求',
  response: 'RESPONSE 响应',
  notification: 'NOTIFICATION 通知',
  decision: 'DECISION 决策点',
}
</script>

<template>
  <div>
    <!-- 控制条 -->
    <div class="controls">
      <button class="btn" @click="onReset"><Icon name="reset" :size="13" /> 重置</button>
      <button class="btn" :disabled="path.length <= 1" @click="onPrev"><Icon name="prev" :size="13" /> 上一步</button>
      <button class="btn primary" @click="togglePlay">
        <Icon :name="playing ? 'pause' : 'play'" :size="13" />
        {{ playing ? '暂停' : isEnd && path.length > 1 ? '重新播放' : '自动播放' }}
      </button>
      <button class="btn" :disabled="isEnd || !!current.choices?.length" @click="onNext">
        <Icon name="next" :size="13" /> 下一步
      </button>
      <span class="counter">{{ path.length }} 步<template v-if="isEnd"> · 已结束</template></span>
    </div>

    <!-- 角色标识 -->
    <div class="roles">
      <div class="role left">
        <Icon name="monitor" :size="22" />
        <div>
          <div class="role-name">{{ roleLeft }}</div>
          <div class="role-desc">{{ leftDesc }}</div>
        </div>
      </div>
      <div class="role right">
        <Icon name="server" :size="22" />
        <div>
          <div class="role-name">{{ roleRight }}</div>
          <div class="role-desc">{{ rightDesc }}</div>
        </div>
      </div>
    </div>

    <!-- 消息时间线 -->
    <div class="timeline">
      <div v-if="shownNodes.length === 1 && shownNodes[0].id === start" class="empty-hint">
        点击「自动播放」或「下一步」，开始逐步观察交互过程
      </div>

      <template v-for="(n, i) in shownNodes" :key="n.id + i">
        <!-- 决策节点（必须先判：决策节点 direction 也是 internal） -->
        <div v-if="n.msgType === 'decision'" class="msg decision">
          <div class="msg-head">
            <span class="msg-step">步骤 {{ i + 1 }} · {{ n.phase }}</span>
            <span class="badge decision">DECISION 决策点</span>
          </div>
          <div class="msg-title"><Icon name="split" :size="15" /> {{ n.title }}</div>
          <CodeBlock v-if="typeof n.payload === 'object'" :code="fmt(n.payload)" lang="json" :title="n.method" />
          <p class="msg-explain">{{ n.explain }}</p>
          <div class="choices">
            <button
              v-for="(c, ci) in n.choices"
              :key="ci"
              class="btn choice-btn"
              :disabled="currentId !== n.id"
              @click="onChoose(ci)"
            >
              <span class="choice-label">{{ c.label }}</span>
              <span v-if="c.hint" class="choice-hint">{{ c.hint }}</span>
            </button>
          </div>
          <p v-if="currentId !== n.id" class="hint" style="margin:6px 0 0">（分支已选择，可「上一步」回退重新决策）</p>
        </div>

        <!-- 内部行为节点 -->
        <div v-else-if="n.direction === 'internal'" class="msg internal">
          <div class="msg-head">
            <span class="msg-step">步骤 {{ i + 1 }} · {{ n.phase }}</span>
            <span class="badge" :class="n.msgType">{{ badgeText[n.msgType] }}</span>
          </div>
          <div class="msg-title">{{ n.title }}</div>
          <CodeBlock :code="fmt(n.payload)" :lang="n.payloadLang ?? 'python'" :title="n.method" />
          <p class="msg-explain">{{ n.explain }}</p>
          <p v-if="n.note" class="msg-note">{{ n.note }}</p>
        </div>

        <!-- 方向性消息 -->
        <div v-else class="msg" :class="n.direction === 'c2s' ? 'to-right' : 'to-left'">
          <div class="msg-head">
            <span class="msg-step">步骤 {{ i + 1 }} · {{ n.phase }}</span>
            <span class="badge" :class="n.msgType">{{ badgeText[n.msgType] }}</span>
          </div>
          <div class="msg-title">
            <Icon :name="n.direction === 'c2s' ? 'down' : 'up'" :size="15" />
            {{ n.title }}
          </div>
          <CodeBlock :code="fmt(n.payload)" :lang="n.payloadLang ?? 'json'" :title="n.method" />
          <p class="msg-explain">{{ n.explain }}</p>
          <p v-if="n.note" class="msg-note">{{ n.note }}</p>
        </div>
      </template>

      <div ref="bottomRef" />
    </div>

    <div v-if="isEnd && path.length > 1" class="end-banner">
      本课程演示结束。切换到「概念」或「真实代码」页签巩固，或从左侧课程目录学习下一课。
    </div>
  </div>
</template>

<style scoped>
.controls {
  position: sticky; top: 0; z-index: 20;
  display: flex; flex-wrap: wrap; align-items: center; gap: 8px;
  background: rgba(2, 6, 23, 0.92); backdrop-filter: blur(6px);
  border: 1px solid var(--panel-border); border-radius: var(--radius);
  padding: 10px 12px; margin-bottom: 14px;
}
.counter { margin-left: auto; font-size: 12px; color: var(--text-dim); }

.roles { display: flex; gap: 12px; margin-bottom: 4px; }
.role {
  flex: 1; display: flex; gap: 10px; align-items: center;
  border-radius: var(--radius); padding: 10px 14px;
}
.role.left { border: 1px solid rgba(56, 189, 248, 0.3); background: rgba(56, 189, 248, 0.06); color: var(--sky); }
.role.right { border: 1px solid rgba(52, 211, 153, 0.3); background: rgba(52, 211, 153, 0.06); color: var(--emerald); }
.role-name { font-size: 13px; font-weight: 700; }
.role-desc { font-size: 11px; color: var(--text-dim); }

.timeline { position: relative; margin-top: 10px; }
.empty-hint {
  border: 1px dashed var(--panel-border); border-radius: var(--radius);
  text-align: center; color: var(--text-faint); padding: 40px 0; font-size: 13px;
}
.msg {
  max-width: 720px; border-radius: var(--radius); padding: 12px 14px;
  background: rgba(30, 41, 59, 0.6); margin: 10px 0;
}
.msg.to-right { border: 1px solid rgba(56, 189, 248, 0.25); margin-right: auto; }
.msg.to-left { border: 1px solid rgba(52, 211, 153, 0.25); margin-left: auto; }
.msg.internal, .msg.decision {
  border: 1px dashed var(--panel-border); margin-left: auto; margin-right: auto;
}
.msg.decision { border-color: rgba(232, 121, 249, 0.45); }
.msg-head { display: flex; justify-content: space-between; align-items: center; gap: 8px; margin-bottom: 6px; flex-wrap: wrap; }
.msg-step { font-size: 11px; font-weight: 600; color: var(--text-dim); }
.msg-title { font-size: 13.5px; font-weight: 600; color: #fff; margin-bottom: 8px; display: flex; align-items: center; gap: 7px; }
.msg-explain { font-size: 12.5px; color: var(--text); margin: 10px 0 0; }
.msg-note {
  margin: 8px 0 0; padding: 6px 10px; border-radius: 6px;
  background: rgba(2, 6, 23, 0.7); font-size: 11px; color: var(--amber);
  font-family: Consolas, monospace;
}
.choices { display: flex; flex-direction: column; gap: 8px; margin-top: 10px; }
.choice-btn { justify-content: flex-start; text-align: left; padding: 9px 13px; }
.choice-btn:not(:disabled):hover { border-color: var(--fuchsia); }
.choice-label { font-weight: 600; }
.choice-hint { color: var(--text-dim); font-size: 11px; }
.end-banner {
  margin-top: 14px; border: 1px solid rgba(52, 211, 153, 0.35);
  background: rgba(52, 211, 153, 0.08); border-radius: var(--radius);
  padding: 12px; text-align: center; font-size: 13px; color: var(--emerald);
}
</style>
