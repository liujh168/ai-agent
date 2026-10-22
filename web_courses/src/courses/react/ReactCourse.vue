<script setup lang="ts">
import { computed, onBeforeUnmount, ref } from 'vue'
import CourseTabs from '../../components/CourseTabs.vue'
import CodeBlock from '../../components/CodeBlock.vue'
import Icon from '../../components/Icon.vue'
import { fmt } from '../../types'
import reactDemo from '../../demos/react_vs_fc_demo.py?raw'
import skillReal from '../../real/skill.py?raw'
import reactReal from '../../real/react.py?raw'
import RealCode from '../../components/RealCode.vue'

const tab = ref('play')

// ═══════ Skill 路由（静态示意） ═══════
const skillFlow = [
  { title: '用户输入', body: '「帮我把这篇文章发布到 WordPress」', color: 'var(--text)' },
  { title: 'SkillRegistry 关键词匹配', body: '命中 wordpress-publish（keywords: wordpress / 发布 / 发文）', color: 'var(--amber)' },
  { title: '工具集收窄', body: '22 种 → 5 种：read_file / write_file / http_request / wordpress_publish / get_weather', color: 'var(--sky)' },
  { title: '注入提示词', body: 'system prompt 追加 Skill 工作流：读草稿 → 排版 → 发布 → 回报链接', color: 'var(--emerald)' },
]

// ═══════ FC vs ReAct 同步对照 ═══════
interface Beat {
  title: string
  payload: object | string
  lang?: 'json' | 'python'
  note?: string
}

const TASK = '查一下北京天气，再算 3×17'

const fcBeats: Beat[] = [
  {
    title: '请求 #1：messages + tools（结构化 schema）',
    payload: {
      messages: [
        { role: 'system', content: '你是 AI Agent…' },
        { role: 'user', content: TASK },
      ],
      tools: [{ type: 'function', function: { name: 'get_weather', ...{} } }],
      tool_choice: 'auto',
    },
  },
  {
    title: '响应 #1：finish_reason=tool_calls，机器可直接读',
    payload: { choices: [{ message: { tool_calls: [{ id: 'c1', function: { name: 'get_weather', arguments: '{"city": "北京"}' } }] }, finish_reason: 'tool_calls' }] },
    note: 'arguments 是 JSON 字符串，json.loads 即用，零解析风险',
  },
  {
    title: '执行回写：role=tool 消息进历史',
    payload: { role: 'tool', tool_call_id: 'c1', content: '北京：晴，26°C' },
  },
  {
    title: '请求 #2',
    payload: { messages: ['…（历史 + tool 结果）'], tools: ['…'] },
  },
  {
    title: '响应 #2：第 2 个 tool_call',
    payload: { choices: [{ message: { tool_calls: [{ id: 'c2', function: { name: 'multiply', arguments: '{"a": 3, "b": 17}' } }] }, finish_reason: 'tool_calls' }] },
  },
  {
    title: '执行回写',
    payload: { role: 'tool', tool_call_id: 'c2', content: '51' },
  },
  {
    title: '请求 #3',
    payload: { messages: ['…（全部历史）'], tools: ['…'] },
  },
  {
    title: '响应 #3：finish_reason=stop，最终回答',
    payload: { choices: [{ message: { content: '北京：晴，26°C；3×17=51。' }, finish_reason: 'stop' }] },
    note: '循环共 3 轮，2 次工具调用',
  },
]

const reactBeats: Beat[] = [
  {
    title: '请求 #1：messages（没有 tools 参数！）',
    payload: {
      messages: [
        { role: 'system', content: '按以下格式回答：Thought:/Action:/Action Input: 或 Final Answer:' },
        { role: 'user', content: TASK },
      ],
      tools: '❌ 不存在 —— 协议全靠 prompt 约定',
    },
    note: '工具说明挤在 system prompt 里，每次都重复消耗 token',
  },
  {
    title: '响应 #1：一段要「猜格式」的纯文本',
    payload:
      'Thought: 需要先查北京天气\nAction: get_weather\nAction Input: {"city": "北京"}',
    lang: 'python',
    note: 'Agent 侧用正则提取 Action / Action Input —— 模型写错格式就解析失败',
  },
  {
    title: '解析执行，Observation 以 user 消息拼回',
    payload: { role: 'user', content: 'Observation: 北京：晴，26°C' },
    note: '结果冒充 user 消息，历史里人和机器的话混在一起',
  },
  {
    title: '请求 #2',
    payload: { messages: ['…（Observation 已混入历史）'] },
  },
  {
    title: '响应 #2：又来一段文本',
    payload:
      'Thought: 再算 3×17\nAction: multiply\nAction Input: {"a": 3, "b": 17}',
    lang: 'python',
  },
  {
    title: '解析执行，Observation 拼回',
    payload: { role: 'user', content: 'Observation: 51' },
  },
  {
    title: '请求 #3',
    payload: { messages: ['…'] },
  },
  {
    title: '响应 #3：Final Answer 收尾',
    payload:
      'Thought: 信息齐全，可以总结\nFinal Answer: 北京：晴，26°C；3×17=51。',
    lang: 'python',
    note: 'Agent 还要再解析一次「Final Answer:」才知道该停了',
  },
]

// —— 同步播放器 ——
const idx = ref(0) // 已展示的 beat 数
const playing = ref(false)
let timer: number | null = null
const total = fcBeats.length
const isEnd = computed(() => idx.value >= total)

function clearTimer() {
  if (timer !== null) { clearInterval(timer); timer = null }
}
function next() {
  if (isEnd.value) { playing.value = false; clearTimer(); return }
  idx.value++
  if (idx.value >= total) { playing.value = false; clearTimer() }
}
function prev() { idx.value = Math.max(0, idx.value - 1) }
function reset() { clearTimer(); playing.value = false; idx.value = 0 }
function toggle() {
  if (playing.value) { clearTimer(); playing.value = false; return }
  if (isEnd.value) idx.value = 0
  playing.value = true
  timer = window.setInterval(next, 2400)
}
onBeforeUnmount(clearTimer)
</script>

<template>
  <div class="course-wrap">
    <header class="course-header">
      <h1>Skill 路由 <span class="grad">与</span> ReAct 模式对比</h1>
      <p class="intro">
        同一道题，两种模式的 token 级对照；外加 Skill 如何先把工具集收窄。对照阅读：
        <code>docs/blog/03-Skill做成Markdown.md</code>、<code>docs/project/深度理解agent模式选择.md</code>、<code>ai_agent/skill.py</code>。
      </p>
    </header>

    <CourseTabs
      v-model="tab"
      :tabs="[
        { key: 'play', label: '交互演示' },
        { key: 'concept', label: '机制要点' },
        { key: 'code', label: '真实代码' },
      ]"
    >
      <template #tab-play>
        <!-- Skill 路由示意 -->
        <h2 class="section-title"><Icon name="bolt" :size="16" /> 第一段：Skill 路由——动手之前先收窄战场</h2>
        <div class="skill-strip">
          <template v-for="(s, i) in skillFlow" :key="i">
            <div class="skill-step card">
              <div class="skill-title" :style="{ color: s.color }">{{ s.title }}</div>
              <p>{{ s.body }}</p>
            </div>
            <span v-if="i < skillFlow.length - 1" class="skill-arrow">→</span>
          </template>
        </div>
        <p class="hint" style="margin: 8px 0 0">
          效果：模型可选项从 22 种收窄到 5 种——选择越少，选错越少，prompt 也越短。这就是 Skill「把领域工作流写成 Markdown」的运行时价值。
        </p>

        <!-- FC vs ReAct 对照 -->
        <h2 class="section-title" style="margin-top: 24px"><Icon name="split" :size="16" /> 第二段：同一道题 {{ '「' + TASK + '」' }}，左右同步播放</h2>
        <div class="controls">
          <button class="btn" @click="reset"><Icon name="reset" :size="13" /> 重置</button>
          <button class="btn" :disabled="idx <= 0" @click="prev"><Icon name="prev" :size="13" /> 上一步</button>
          <button class="btn primary" @click="toggle"><Icon :name="playing ? 'pause' : 'play'" :size="13" /> {{ playing ? '暂停' : isEnd ? '重新播放' : '自动播放' }}</button>
          <button class="btn" :disabled="isEnd" @click="next"><Icon name="next" :size="13" /> 下一步</button>
          <span class="counter">{{ idx }} / {{ total }} 拍</span>
        </div>

        <div class="vs-grid">
          <div class="vs-col fc">
            <div class="vs-head">Function Calling <span class="badge response">结构化协议</span></div>
            <div v-if="idx === 0" class="vs-empty">同一拍的内容会同步出现在这里</div>
            <div v-for="i in idx" :key="'f' + i" class="vs-beat">
              <div class="vs-beat-title">{{ i }}. {{ fcBeats[i - 1].title }}</div>
              <CodeBlock :code="fmt(fcBeats[i - 1].payload)" :lang="fcBeats[i - 1].lang ?? 'json'" />
              <p v-if="fcBeats[i - 1].note" class="vs-note">{{ fcBeats[i - 1].note }}</p>
            </div>
          </div>
          <div class="vs-col react">
            <div class="vs-head">ReAct <span class="badge notification">文本协议</span></div>
            <div v-if="idx === 0" class="vs-empty">同一拍的内容会同步出现在这里</div>
            <div v-for="i in idx" :key="'r' + i" class="vs-beat">
              <div class="vs-beat-title">{{ i }}. {{ reactBeats[i - 1].title }}</div>
              <CodeBlock :code="fmt(reactBeats[i - 1].payload)" :lang="reactBeats[i - 1].lang ?? 'json'" />
              <p v-if="reactBeats[i - 1].note" class="vs-note">{{ reactBeats[i - 1].note }}</p>
            </div>
          </div>
        </div>
        <div v-if="isEnd" class="end-banner">播完 8 拍：注意 ReAct 把协议写进 prompt、用正则解析、Observation 冒充 user 消息的三处"妥协"。</div>
      </template>

      <template #tab-concept>
        <div class="grid cols-2">
          <div class="card">
            <h3>ReAct 的三处妥协</h3>
            <div class="sub">为什么教学项目里它是"演示模式"</div>
            <p>① <b>协议靠 prompt</b>：工具说明、格式要求全塞 system prompt，每次请求重复付费；模型写错一个空格，正则就解析失败。</p>
            <p>② <b>结果混入 user 消息</b>：Observation 以 user 角色回写，历史里人机混杂，后续轮次容易把 Observation 当成人的指令。</p>
            <p>③ <b>结束也要解析</b>：得靠 "Final Answer:" 字符串判断循环结束，比 finish_reason=stop 脆得多。</p>
          </div>
          <div class="card">
            <h3>FC 为什么成为主流</h3>
            <div class="sub">结构化是协议的进化</div>
            <p>tool_calls 是模型输出里的<b>独立结构化字段</b>：不用猜格式、不占 content、失败有 finish_reason 兜底。模型厂商在训练期就专门优化了工具调用能力，可靠性远超文本协议。</p>
          </div>
          <div class="card">
            <h3>ReAct 并未过时</h3>
            <div class="sub">它教会的两件事</div>
            <p>一是「显式推理轨迹」的价值——Thought 让模型把中间步骤写出来，错误更容易定位（后来的思维链、o1 式推理都受益于此）；二是多步规划的思想。项目保留 ReAct 模式正是为了对照学习。</p>
          </div>
          <div class="card">
            <h3>Skill 路由的本质</h3>
            <div class="sub">路由在前，循环在后</div>
            <p>Skill 不改变 FC 循环本身，只改变循环的<b>输入</b>：更窄的工具 schema + 专门的 system prompt。命中 wordpress-publish 后，模型在 5 个工具里做选择题，而不是 22 个——这是「先收窄战场，再开战」。</p>
          </div>
        </div>
      </template>

      <template #tab-code>
        <div class="hint-box" style="margin-bottom: 14px">
          <span class="icon"><Icon name="info" :size="16" /></span>
          <span>
            零依赖可运行：<code>python react_vs_fc_demo.py</code>，依次打印 Skill 路由 → ReAct 文本协议全程 → FC 结构化协议全程。
            文件位于 AI-Agent 项目 <code>ai_agent/demos/react_vs_fc_demo.py</code>。
          </span>
        </div>
        <CodeBlock :code="reactDemo" lang="python" :max-height="560" />

        <h3 style="margin: 18px 0 4px; font-size: 14px; color: #fff">对照真实源码</h3>
        <p class="hint" style="margin: 0 0 10px">
          Skill 路由在 <code>skill.py</code>（加载 Markdown  frontmatter、关键词匹配、收窄工具集），
          ReAct 模式在 <code>ai_agent/demos/react.py</code>（教学版实现，对照左栏文本协议逐拍看）。
        </p>
        <RealCode
          title="SkillRegistry —— 真实的关键词路由"
          file="ai_agent/skill.py"
          :code="skillReal"
          :max-height="460"
          :mapping="[
            { step: '第一段：加载 Skill（Markdown → frontmatter 解析）', loc: '_parse_frontmatter() / create_skill_registry()' },
            { step: '第一段：关键词命中 wordpress-publish', loc: 'SkillRegistry.match()' },
            { step: '第一段：工具集收窄 22 → 5', loc: 'SkillDef.tools 过滤 schema 的逻辑' },
            { step: '第一段：注入 system prompt', loc: 'SkillDef.prompt 拼入系统消息' },
          ]"
        />
        <RealCode
          title="run_react —— 真实的 ReAct 文本协议解析"
          file="ai_agent/demos/react.py"
          :code="reactReal"
          :max-height="440"
          :mapping="[
            { step: '右栏第 2 拍：Thought/Action/Input 文本解析', loc: 'parse_react_output()' },
            { step: '右栏第 3 拍：执行并以 Observation 拼回历史', loc: 'run_react() 的 action 执行分支' },
            { step: '右栏第 8 拍：Final Answer 判定循环结束', loc: 'parse_react_output() 的 finish 分支' },
          ]"
        />
      </template>
    </CourseTabs>
  </div>
</template>

<style scoped>
.course-header h1 { font-size: 21px; color: #fff; margin: 6px 0 4px; }
.grad { color: var(--fuchsia); }
.intro { font-size: 12.5px; color: var(--text-dim); margin: 0 0 16px; }
.section-title { font-size: 15px; color: #fff; display: flex; align-items: center; gap: 8px; margin: 18px 0 10px; }
.skill-strip { display: flex; align-items: stretch; gap: 8px; flex-wrap: wrap; }
.skill-step { flex: 1; min-width: 190px; padding: 12px 14px; }
.skill-title { font-size: 12.5px; font-weight: 700; margin-bottom: 4px; }
.skill-step p { font-size: 12px; margin: 0; }
.skill-arrow { align-self: center; color: var(--text-faint); }
.controls {
  display: flex; flex-wrap: wrap; align-items: center; gap: 8px;
  background: rgba(2, 6, 23, 0.92); border: 1px solid var(--panel-border);
  border-radius: var(--radius); padding: 10px 12px; margin-bottom: 12px;
  position: sticky; top: 0; z-index: 20;
}
.counter { margin-left: auto; font-size: 12px; color: var(--text-dim); }
.vs-grid { display: grid; grid-template-columns: 1fr 1fr; gap: 14px; }
@media (max-width: 900px) { .vs-grid { grid-template-columns: 1fr; } }
.vs-col { border: 1px solid var(--panel-border); border-radius: var(--radius); padding: 12px; background: rgba(30, 41, 59, 0.35); }
.vs-col.fc { border-top: 2px solid var(--emerald); }
.vs-col.react { border-top: 2px solid var(--amber); }
.vs-head { font-size: 14px; font-weight: 700; color: #fff; margin-bottom: 10px; display: flex; align-items: center; gap: 8px; }
.vs-empty { color: var(--text-faint); font-size: 12px; text-align: center; padding: 24px 0; border: 1px dashed var(--panel-border); border-radius: 8px; }
.vs-beat { margin-bottom: 12px; }
.vs-beat-title { font-size: 12px; font-weight: 600; color: var(--text); margin-bottom: 5px; }
.vs-note { font-size: 11.5px; color: var(--amber); margin: 6px 0 0; }
.end-banner {
  margin-top: 14px; border: 1px solid rgba(52, 211, 153, 0.35);
  background: rgba(52, 211, 153, 0.08); border-radius: var(--radius);
  padding: 12px; text-align: center; font-size: 13px; color: var(--emerald);
}
</style>
