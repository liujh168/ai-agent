<script setup lang="ts">
import { ref } from 'vue'
import CourseTabs from '../../components/CourseTabs.vue'
import StepPlayer from '../../components/StepPlayer.vue'
import CodeBlock from '../../components/CodeBlock.vue'
import Icon from '../../components/Icon.vue'
import { MCP_NODES, MCP_START } from './data'
import serverPy from '../../demos/mcp_server.py?raw'
import clientPy from '../../demos/mcp_client.py?raw'
import mcpClientReal from '../../real/mcp_client.py?raw'
import RealCode from '../../components/RealCode.vue'

const tab = ref('play')

const jsonAnatomy: [string, string][] = [
  ['{', ''],
  ['  "jsonrpc": "2.0",', '协议版本，固定值'],
  ['  "id": 3,', '请求/响应配对的唯一编号'],
  ['  "method": "tools/call",', '要调用的方法名'],
  ['  "params": { ... }', '方法参数'],
  ['}', ''],
]
</script>

<template>
  <div class="course-wrap">
    <header class="course-header">
      <h1>MCP 客户端 ↔ 服务端 <span class="grad">工作原理</span></h1>
      <p class="intro">
        Model Context Protocol · 基于 JSON-RPC 2.0 · 以「加法计算器」为例逐条拆解完整会话。
        对照阅读：<code>docs/project/MCP客户端协议拆解.md</code> 与 <code>ai_agent/mcp_client.py</code>。
      </p>
    </header>

    <CourseTabs
      v-model="tab"
      :tabs="[
        { key: 'play', label: '交互演示' },
        { key: 'concept', label: '概念速览' },
        { key: 'code', label: '真实代码' },
      ]"
    >
      <template #tab-play>
        <StepPlayer
          :nodes="MCP_NODES"
          :start="MCP_START"
          role-left="MCP Client（客户端）"
          role-right="MCP Server（服务端）"
          left-desc="运行在 Host 内，1 个 Server 对应 1 个 Client 实例"
          right-desc="提供工具/资源/提示词，本地子进程或远程 HTTP 服务"
        />
      </template>

      <template #tab-concept>
        <div class="grid cols-3">
          <div class="card">
            <h3>三个角色</h3>
            <div class="sub">Host · Client · Server</div>
            <p><b>Host</b> 是用户真正使用的应用（Kimi、Claude Desktop、VS Code）。Host 内每个 Server 对应一个 <b>Client</b> 实例；模型只和 Host 对话，从不直接碰 Server。</p>
            <p class="mono">用户/LLM ↔ Host ↔ Client → Server ↔ 数据/系统</p>
          </div>
          <div class="card">
            <h3>两种传输</h3>
            <div class="sub">stdio · Streamable HTTP</div>
            <p><b>stdio</b>：本地子进程，换行分隔 JSON，桌面 Agent 默认方式。</p>
            <p><b>Streamable HTTP</b>：POST + SSE 流式响应，适合远程 Server，支持有状态会话（Mcp-Session-Id 头）。</p>
          </div>
          <div class="card">
            <h3>三类原语</h3>
            <div class="sub">Tools · Resources · Prompts</div>
            <p><b>Tools</b>：模型可调用的函数（带 inputSchema）；<b>Resources</b>：可读取的数据（URI 定位）；<b>Prompts</b>：预设提示词模板。全部通过 list / get / call 统一接口发现与使用。</p>
          </div>
        </div>

        <div class="grid cols-2" style="margin-top: 14px">
          <div class="card">
            <h3>消息格式：JSON-RPC 2.0</h3>
            <div class="sub">MCP 不发明新协议，站在 JSON-RPC 上</div>
            <div class="anatomy">
              <div v-for="([line, note], i) in jsonAnatomy" :key="i" class="anatomy-line">
                <span class="mono">{{ line }}</span>
                <span v-if="note" class="note">← {{ note }}</span>
              </div>
            </div>
            <p class="hint" style="margin-top: 8px">
              响应把 method/params 换成 <b style="color: var(--emerald)">result</b>（成功）或
              <b style="color: var(--red)">error</b>（协议级失败）；<b style="color: var(--amber)">没有 id 的是通知</b>。
            </p>
          </div>
          <div class="card">
            <h3>请求 ≠ 通知</h3>
            <div class="sub">MCP 交互的两个基本形态</div>
            <p><b style="color: var(--sky)">请求（Request）</b>：带 id，必须收到同 id 响应——initialize、tools/list、tools/call、ping。</p>
            <p><b style="color: var(--amber)">通知（Notification）</b>：不带 id，发完不等回应——initialized、exit、tools/list_changed。方向可以是 C→S 也可以是 S→C。</p>
            <p class="hint">判断口诀：看有没有 "id" 字段。</p>
          </div>
        </div>

        <div class="card" style="margin-top: 14px">
          <h3>一次 MCP 会话的标准时序</h3>
          <div class="flow">
            <span v-for="(t, i) in ['启动子进程','initialize 请求','initialize 响应','initialized 通知','tools/list','工具清单','tools/call','执行 + 结果','ping / shutdown','exit']" :key="i" class="flow-item">
              {{ t }}<span v-if="i < 9" class="arrow">→</span>
            </span>
          </div>
          <p class="hint" style="margin-top: 8px">
            关键规则：<b>initialize 完成前 Server 不得处理其他请求</b>；任何时刻双方都可发通知；Server 还能反向发请求（如 sampling/createMessage 让 Host 代为调 LLM）。
          </p>
        </div>
      </template>

      <template #tab-code>
        <div class="hint-box" style="margin-bottom: 14px">
          <span class="icon"><Icon name="info" :size="16" /></span>
          <span>
            这是真正能跑的代码，不是伪代码。两个文件均为<b>零依赖</b>纯标准库实现（Python 3.8+），
            运行命令：<code>python mcp_client.py</code>（Client 会自动拉起 Server）。
            文件位于 AI-Agent 项目 <code>ai_agent/demos/mcp_stdio_demo/</code>。
          </span>
        </div>
        <h3 class="code-title">mcp_server.py —— 服务端</h3>
        <CodeBlock :code="serverPy" lang="python" :max-height="480" />
        <h3 class="code-title">mcp_client.py —— 客户端</h3>
        <CodeBlock :code="clientPy" lang="python" :max-height="480" />

        <h3 class="code-title">对照真实源码</h3>
        <p class="hint" style="margin: 0 0 10px">
          下面是项目里真正的 MCP 客户端（<code>ai_agent/mcp_client.py</code>，经 MCPManager 装配进 Agent）。
          上面 demo 教的每一个报文，在真实代码里都有对应实现：
        </p>
        <RealCode
          title="项目真实的 MCPClient（Streamable HTTP）与 MCPClientSTDIO（本地子进程）"
          file="ai_agent/mcp_client.py"
          :code="mcpClientReal"
          :max-height="480"
          :mapping="[
            { step: '步骤 0：以子进程方式拉起 Server', loc: 'MCPClientSTDIO._ensure_proc()' },
            { step: '步骤 1-2：initialize 握手协商', loc: 'MCPClient.initialize()' },
            { step: '步骤 3：initialized 通知（无 id 不等响应）', loc: 'MCPClient._notify()' },
            { step: '步骤 4-5：tools/list 能力发现', loc: 'MCPClient.list_tools()' },
            { step: '步骤 6-7：tools/call 调用与 isError 处理', loc: 'MCPClient.call_tool()' },
            { step: 'STDIO 的读循环：逐行读 JSON、容忍非 JSON 行', loc: 'MCPClientSTDIO._read_loop()' },
          ]"
        />
      </template>
    </CourseTabs>
  </div>
</template>

<style scoped>
.course-header h1 { font-size: 21px; color: #fff; margin: 6px 0 4px; }
.grad { background: linear-gradient(90deg, var(--sky), var(--emerald)); -webkit-background-clip: text; background-clip: text; color: transparent; }
.intro { font-size: 12.5px; color: var(--text-dim); margin: 0 0 16px; }
.mono { font-family: Consolas, monospace; font-size: 11.5px; color: var(--text-dim); }
.anatomy-line { display: flex; gap: 10px; align-items: baseline; }
.anatomy .note { font-size: 11px; color: var(--text-faint); }
.flow { display: flex; flex-wrap: wrap; gap: 6px; align-items: center; font-family: Consolas, monospace; font-size: 12px; }
.flow-item { background: rgba(2, 6, 23, 0.6); border: 1px solid var(--panel-border); border-radius: 6px; padding: 4px 9px; }
.arrow { color: var(--text-faint); margin-left: 6px; }
.code-title { font-size: 14px; color: #fff; margin: 16px 0 6px; }
</style>
