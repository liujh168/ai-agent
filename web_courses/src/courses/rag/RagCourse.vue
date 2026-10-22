<script setup lang="ts">
import { computed, ref } from 'vue'
import CourseTabs from '../../components/CourseTabs.vue'
import CodeBlock from '../../components/CodeBlock.vue'
import Icon from '../../components/Icon.vue'
import ragDemo from '../../demos/context_rag_demo.py?raw'
import ragReal from '../../real/rag.py?raw'
import compressReal from '../../real/app_core_compress_excerpt.py?raw'
import RealCode from '../../components/RealCode.vue'

const tab = ref('sim')

// ═══════════════ A. 上下文压缩模拟器 ═══════════════
const rounds = ref(9)
const threshold = ref(6000)
const TOKENS_PER_ROUND = 400

const totalTokens = computed(() => rounds.value * TOKENS_PER_ROUND)
const needCompress = computed(() => totalTokens.value > threshold.value)
const keepRounds = 4
const compressedTokens = computed(() => {
  if (!needCompress.value) return totalTokens.value
  const kept = Math.min(rounds.value, keepRounds) * TOKENS_PER_ROUND
  const summary = 500 // 摘要本身也要占 token
  return kept + summary
})
const barPct = computed(() => Math.min(100, (totalTokens.value / 16000) * 100))
const keptPct = computed(() => Math.min(100, (compressedTokens.value / 16000) * 100))

// ═══════════════ B. RAG 检索模拟器 ═══════════════
// 8 个主题维度上的示意权重（向量的教学化简，非真实 embedding）
const TOPICS = ['压缩', 'RAG', '工具', '安全', '会话', '评测', 'MCP', '部署'] as const

interface Chunk {
  id: number
  text: string
  vec: number[]
  source: string
}

const CHUNKS: Chunk[] = [
  { id: 1, text: '上下文压缩：当历史消息的 token 数超过阈值时，调用 LLM 把旧对话总结成一段摘要，只保留最近几轮原文，防止请求超长。', vec: [1.0, 0.15, 0, 0, 0.5, 0, 0, 0], source: 'docs/project/上下文管理与历史压缩.md' },
  { id: 2, text: 'RAG 检索：文档先按规则切块（chunk），每块调 embedding 模型变成向量；提问时也变成向量，按余弦相似度取 top-k 块塞进上下文。', vec: [0.2, 1.0, 0.3, 0, 0, 0, 0, 0], source: 'docs/project/RAG实现本质原理.md' },
  { id: 3, text: '工具调用安全：ToolExecutor 对每次工具调用做五级风险分类，L1 自动执行，L3 写操作必须人工确认，L5 代码执行直接拒绝。', vec: [0, 0, 0.8, 1.0, 0, 0.1, 0, 0], source: 'docs/blog/04-Agent安全三层防御.md' },
  { id: 4, text: '多 Agent 协作：Planner 拆任务、Executor 逐条执行、Critic 评审打回，三者构成一条线性流水线，评审不通过则重新规划。', vec: [0, 0, 0.4, 0, 0, 0.3, 0, 0.1], source: 'docs/project/多Agent协作拆解.md' },
  { id: 5, text: 'MCP 接入：通过 MCPClient 连接外部工具服务器，支持 Streamable HTTP 与 stdio 两种传输，工具自动带 mcp_ 前缀注册。', vec: [0, 0.1, 0.5, 0, 0, 0, 1.0, 0], source: 'docs/project/MCP客户端协议拆解.md' },
  { id: 6, text: '会话持久化：所有历史消息按 session_id 存入 SQLite，切换会话即恢复上下文；数据库文件在项目根目录。', vec: [0.3, 0, 0, 0, 1.0, 0, 0, 0.2], source: 'docs/project/架构总览.md' },
]

const LEXICON: Record<string, number> = {
  压缩: 0, 摘要: 0, 历史: 0, token: 0, 阈值: 0,
  检索: 1, rag: 1, embedding: 1, 文档: 1, 切块: 1, 向量: 1,
  工具: 2, 调用: 2, fc: 2,
  安全: 3, 风险: 3, 确认: 3, 拒绝: 3,
  会话: 4, 持久化: 4, 存储: 4,
  评测: 5, 测试: 5, gold: 5,
  mcp: 6, 服务器: 6, 传输: 6,
  部署: 7, 发布: 7, wordpress: 7,
}

const query = ref('上下文压缩什么时候触发？')
const scores = computed(() => {
  const q = Array(TOPICS.length).fill(0) as number[]
  const lower = query.value.toLowerCase()
  for (const [kw, dim] of Object.entries(LEXICON)) {
    if (lower.includes(kw.toLowerCase())) q[dim] += 1
  }
  // 全部命中关键词时按单位向量归一（cosine 只看方向）
  const norm = Math.hypot(...q) || 1
  return CHUNKS.map((c) => {
    const dot = c.vec.reduce((s, v, i) => s + v * q[i], 0)
    const cNorm = Math.hypot(...c.vec) || 1
    return { chunk: c, score: dot / (cNorm * norm), hits: q }
  }).sort((a, b) => b.score - a.score)
})

const topIds = computed(() => new Set(scores.value.slice(0, 2).filter((s) => s.score > 0.01).map((s) => s.chunk.id)))
</script>

<template>
  <div class="course-wrap">
    <header class="course-header">
      <h1>上下文压缩 <span class="grad">×</span> RAG 检索实验</h1>
      <p class="intro">
        两个最"隐形"的机制，做成可以动手调参的模拟器。对照阅读：
        <code>docs/blog/06-对照实验-上下文压缩与RAG检索.md</code>、<code>ai_agent/rag.py</code>。
      </p>
    </header>

    <CourseTabs
      v-model="tab"
      :tabs="[
        { key: 'sim', label: '交互模拟器' },
        { key: 'concept', label: '机制要点' },
        { key: 'code', label: '真实代码' },
      ]"
    >
      <template #tab-sim>
        <!-- ─────── A. 压缩模拟器 ─────── -->
        <h2 class="section-title"><Icon name="layers" :size="16" /> 实验一：上下文压缩 —— 拖过阈值，看看历史会怎样</h2>
        <div class="card">
          <div class="sim-row">
            <label>对话轮数 <b>{{ rounds }}</b> 轮（每轮 ≈ {{ TOKENS_PER_ROUND }} tokens）</label>
            <input v-model.number="rounds" type="range" min="1" max="30" />
          </div>
          <div class="sim-row">
            <label>压缩阈值 <b>{{ threshold }}</b> tokens（对应 Config 里的触发线）</label>
            <input v-model.number="threshold" type="range" min="2000" max="14000" step="500" />
          </div>

          <div class="token-bars">
            <div class="tbar">
              <span class="tbar-label">压缩前</span>
              <div class="tbar-track">
                <div class="tbar-fill" :class="{ over: needCompress }" :style="{ width: barPct + '%' }"></div>
                <span class="tbar-num">{{ totalTokens }}</span>
              </div>
            </div>
            <div class="tbar" v-if="needCompress">
              <span class="tbar-label">压缩后</span>
              <div class="tbar-track">
                <div class="tbar-fill kept" :style="{ width: keptPct + '%' }"></div>
                <span class="tbar-num">{{ compressedTokens }}</span>
              </div>
            </div>
          </div>

          <div v-if="needCompress" class="compress-result">
            <p>
              ⚡ 触发压缩：第 <b>1 ~ {{ rounds - keepRounds }}</b> 轮被 LLM 总结成
              <b>1 条摘要（≈500 tokens）</b>，仅保留最近 <b>{{ keepRounds }}</b> 轮原文。
            </p>
            <div class="history-visual">
              <div class="msg-chip summary">
                📦 摘要消息：「用户先要求复制文件，随后询问了 2+3 的计算，模型调用 add 工具得到 5…」（顶替 {{ rounds - keepRounds }} 轮原文）
              </div>
              <div v-for="r in Math.min(rounds, keepRounds)" :key="r" class="msg-chip">
                💬 第 {{ rounds - keepRounds + r }} 轮原文（user + assistant，≈400 tokens）
              </div>
            </div>
            <p class="hint">
              代价与收益：token 从 {{ totalTokens }} 降到 {{ compressedTokens }}（省 {{ Math.round((1 - compressedTokens / totalTokens) * 100) }}%），
              但早期细节被「摘要」二次概括，可能失真——这就是 RAG 要解决的问题（见实验二）。
            </p>
          </div>
          <div v-else class="compress-result ok">
            <p>✅ 未触发压缩：{{ totalTokens }} tokens 还在 {{ threshold }} 阈值之内，历史原样发送。把轮数或阈值调过线试试。</p>
          </div>
        </div>

        <!-- ─────── B. RAG 检索模拟器 ─────── -->
        <h2 class="section-title" style="margin-top: 26px"><Icon name="flask" :size="16" /> 实验二：RAG 检索 —— 输入问题，看哪块 chunk 胜出</h2>
        <div class="card">
          <div class="sim-row">
            <label>用户问题</label>
            <input v-model="query" class="query-input" placeholder="试试：压缩 / 检索 / 安全 / MCP / 会话…" />
          </div>
          <div class="presets">
            <button v-for="p in ['上下文压缩什么时候触发？', 'RAG 怎么检索文档？', '工具调用的安全怎么保证？', 'MCP 怎么接入服务器？', '会话历史存在哪里？']"
              :key="p" class="btn preset" @click="query = p">{{ p }}</button>
          </div>

          <div class="dim-row">
            <span class="hint">问题在 8 个主题维度上的分量（示意）：</span>
            <span v-for="(t, i) in TOPICS" :key="t" class="dim-chip" :class="{ on: scores[0] && scores[0].hits[i] > 0 }">{{ t }}</span>
          </div>

          <div class="chunk-list">
            <div v-for="s in scores" :key="s.chunk.id" class="chunk" :class="{ top: topIds.has(s.chunk.id) }">
              <div class="chunk-head">
                <span class="chunk-id">chunk_{{ s.chunk.id }}</span>
                <span class="chunk-score" :class="{ top: topIds.has(s.chunk.id) }">
                  {{ s.score.toFixed(3) }}<template v-if="topIds.has(s.chunk.id)"> → 入选 top-2 ✓</template>
                </span>
              </div>
              <div class="chunk-bar"><div class="chunk-bar-fill" :style="{ width: Math.min(100, s.score * 130) + '%' }"></div></div>
              <p class="chunk-text">{{ s.chunk.text }}</p>
              <p class="chunk-src">{{ s.chunk.source }}</p>
            </div>
          </div>
          <p class="hint" style="margin-top: 10px">
            向量是 8 个主题维度上的<b>示意权重</b>（真实实现是高维 embedding，见 <code>ai_agent/rag.py</code> 的 numpy 余弦检索）；
            排序逻辑一致：点积 / 模长 = 余弦相似度，只关心方向不关心大小。
          </p>
        </div>
      </template>

      <template #tab-concept>
        <div class="grid cols-2">
          <div class="card">
            <h3>压缩：用「遗忘」换「装得下」</h3>
            <div class="sub">_maybe_compress() 在每次发送前检查</div>
            <p>触发条件：历史 token 估算超过阈值。动作：旧轮次 → LLM 摘要（1 条 system 可见的占位消息），保留最近 N 轮原文 + 摘要失败时硬截断兜底。</p>
            <p>本质矛盾：上下文窗口是有限的「工作记忆」，压缩是取舍，不是免费午餐。</p>
          </div>
          <div class="card">
            <h3>RAG：用「外置记忆」换「不遗忘」</h3>
            <div class="sub">rag.py：切块 → embedding → 余弦 top-k</div>
            <p>文档切块后向量化入库；提问时检索最相关的 k 块塞进上下文。早期细节没有丢，只是放在了「硬盘」里按需取回——代价是每次检索的延迟和 embedding 成本。</p>
          </div>
          <div class="card">
            <h3>为什么是对照实验</h3>
            <div class="sub">blog/06 的核心论点</div>
            <p>压缩丢信息但零延迟；RAG 保信息但有成本。项目里的选择：短会话靠压缩，长文档知识靠 RAG，两者互补而非替代。</p>
          </div>
          <div class="card">
            <h3>动手改改看</h3>
            <div class="sub">把模拟器结论带回项目</div>
            <p>模拟器里的阈值、保留轮数、top-k 都能在项目配置中找到对应项（<code>Config</code> / <code>.env</code>）。改小阈值再跑一轮长对话，到「API 调用检查器」里能直接看到 messages 突然变短的那一刻。</p>
          </div>
        </div>
      </template>

      <template #tab-code>
        <div class="hint-box" style="margin-bottom: 14px">
          <span class="icon"><Icon name="info" :size="16" /></span>
          <span>
            零依赖可运行：<code>python context_rag_demo.py</code>，打印压缩前后的历史结构 + 与网页一致的向量检索打分。
            文件位于 AI-Agent 项目 <code>ai_agent/demos/context_rag_demo.py</code>。
          </span>
        </div>
        <CodeBlock :code="ragDemo" lang="python" :max-height="560" />

        <h3 style="margin: 18px 0 4px; font-size: 14px; color: #fff">对照真实源码</h3>
        <p class="hint" style="margin: 0 0 10px">
          模拟器用的是 8 维示意向量；项目里是真实 embedding + 余弦检索，且已实现 FAISS 加速分支。
          压缩部分在 <code>app_core.py</code>（CLI / Web 共用的压缩持久化函数）。
        </p>
        <RealCode
          title="RAGKnowledgeBase —— 真实检索实现（含 FAISS 分支）"
          file="ai_agent/rag.py"
          :code="ragReal"
          :max-height="460"
          :mapping="[
            { step: '实验二：切块 + embedding', loc: 'RAGKnowledgeBase.build() / _embed()' },
            { step: '实验二：余弦相似度打分', loc: 'RAGKnowledgeBase._cosine()' },
            { step: '实验二：top-k 排序取块', loc: 'search_detailed()（默认 numpy 路径）' },
            { step: '进阶：chunk 超 2000 走 FAISS 加速', loc: '_build_faiss_index() / _search_faiss()' },
            { step: '缓存：embedding 结果落盘免重算', loc: '_load_cache() / _save_cache()' },
          ]"
        />
        <RealCode
          title="compress_and_persist_history —— 真实压缩流程（app_core.py 摘录）"
          file="ai_agent/app_core.py:780-844"
          :code="compressReal"
          :max-height="380"
          :mapping="[
            { step: '实验一：触发压缩 + 生成摘要', loc: 'make_summarize_history() 返回的压缩函数' },
            { step: '实验一：摘要回写历史并持久化', loc: 'compress_and_persist_history()' },
            { step: '兜底：摘要失败时的硬截断', loc: '压缩函数的 except 回退分支' },
          ]"
        />
      </template>
    </CourseTabs>
  </div>
</template>

<style scoped>
.course-header h1 { font-size: 21px; color: #fff; margin: 6px 0 4px; }
.grad { color: var(--amber); }
.intro { font-size: 12.5px; color: var(--text-dim); margin: 0 0 16px; }
.section-title { font-size: 15px; color: #fff; display: flex; align-items: center; gap: 8px; margin: 18px 0 10px; }
.sim-row { margin-bottom: 12px; }
.sim-row label { display: block; font-size: 12.5px; color: var(--text); margin-bottom: 4px; }
.query-input {
  width: 100%; background: rgba(2, 6, 23, 0.7); border: 1px solid var(--panel-border);
  border-radius: 8px; color: var(--text); padding: 8px 12px; font-size: 13px; outline: none;
}
.query-input:focus { border-color: var(--sky); }
.token-bars { margin: 14px 0; display: flex; flex-direction: column; gap: 8px; }
.tbar { display: flex; align-items: center; gap: 10px; }
.tbar-label { font-size: 11px; color: var(--text-dim); width: 52px; text-align: right; }
.tbar-track { flex: 1; position: relative; background: rgba(2, 6, 23, 0.7); border-radius: 6px; height: 22px; overflow: hidden; }
.tbar-fill { height: 100%; background: var(--sky); border-radius: 6px; transition: width 0.3s; }
.tbar-fill.over { background: var(--red); }
.tbar-fill.kept { background: var(--emerald); }
.tbar-num { position: absolute; right: 8px; top: 50%; transform: translateY(-50%); font-size: 11px; color: var(--text); font-family: Consolas, monospace; }
.compress-result { border-top: 1px dashed var(--panel-border); padding-top: 12px; font-size: 13px; }
.compress-result.ok p { color: var(--emerald); margin: 0; }
.history-visual { display: flex; flex-direction: column; gap: 6px; margin: 10px 0; }
.msg-chip { background: rgba(51, 65, 85, 0.4); border: 1px solid var(--panel-border); border-radius: 8px; padding: 7px 11px; font-size: 12px; color: var(--text); }
.msg-chip.summary { border-color: rgba(251, 191, 36, 0.5); color: var(--amber); background: rgba(251, 191, 36, 0.07); }
.presets { display: flex; flex-wrap: wrap; gap: 6px; margin-bottom: 12px; }
.preset { font-size: 11px; padding: 5px 10px; }
.dim-row { display: flex; flex-wrap: wrap; gap: 6px; align-items: center; margin-bottom: 12px; }
.dim-chip { font-size: 11px; border: 1px solid var(--panel-border); border-radius: 20px; padding: 2px 10px; color: var(--text-faint); }
.dim-chip.on { color: var(--sky); border-color: rgba(56, 189, 248, 0.5); background: rgba(56, 189, 248, 0.1); }
.chunk-list { display: flex; flex-direction: column; gap: 10px; }
.chunk { border: 1px solid var(--panel-border); border-radius: 8px; padding: 10px 12px; background: rgba(2, 6, 23, 0.4); }
.chunk.top { border-color: rgba(52, 211, 153, 0.5); background: rgba(52, 211, 153, 0.05); }
.chunk-head { display: flex; justify-content: space-between; font-size: 12px; margin-bottom: 5px; }
.chunk-id { color: var(--text-dim); font-family: Consolas, monospace; }
.chunk-score { color: var(--text-dim); font-family: Consolas, monospace; }
.chunk-score.top { color: var(--emerald); font-weight: 700; }
.chunk-bar { height: 5px; background: rgba(51, 65, 85, 0.5); border-radius: 3px; overflow: hidden; margin-bottom: 7px; }
.chunk-bar-fill { height: 100%; background: var(--sky); border-radius: 3px; transition: width 0.3s; }
.chunk.top .chunk-bar-fill { background: var(--emerald); }
.chunk-text { font-size: 12.5px; margin: 0; }
.chunk-src { font-size: 10.5px; color: var(--text-faint); margin: 5px 0 0; font-family: Consolas, monospace; }
</style>
