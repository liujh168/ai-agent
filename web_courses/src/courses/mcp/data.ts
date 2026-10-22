import type { StepNode } from '../../types'

export const MCP_NODES: Record<string, StepNode> = {
  s0: {
    id: 's0',
    phase: '连接建立',
    title: 'Host 启动 Client，Client 以子进程方式拉起 Server（stdio 传输）',
    direction: 'internal',
    msgType: 'request',
    payload:
      '$ python mcp_client.py\n# Client 内部执行：\nsubprocess.Popen(\n  ["python", "mcp_server.py"],\n  stdin=PIPE,   # Client 写 → Server 读\n  stdout=PIPE,  # Server 写 → Client 读\n  stderr=PIPE,  # Server 日志\n)',
    explain:
      'MCP 中最常见的传输方式是 stdio：Server 是本地子进程，双方通过标准输入/输出用「换行分隔的 JSON」通信。也可以走 Streamable HTTP（SSE + POST）。Client 负责进程的整个生命周期。',
    note: 'stdio：每条消息一行 JSON，以 \n 分隔',
    next: 's1',
  },
  s1: {
    id: 's1',
    phase: '握手初始化',
    title: 'Client → Server：initialize 请求',
    direction: 'c2s',
    msgType: 'request',
    method: 'initialize',
    payload: {
      jsonrpc: '2.0',
      id: 1,
      method: 'initialize',
      params: {
        protocolVersion: '2025-06-18',
        capabilities: { roots: { listChanged: true }, sampling: {} },
        clientInfo: { name: 'demo-host', version: '1.0.0' },
      },
    },
    explain:
      '第一条消息必须是 initialize。params 说明：协议版本、Client 支持的能力（roots=文件根目录、sampling=支持让 Server 请求 LLM 采样）、Client 身份。这是 JSON-RPC 2.0 请求：带 id，期待同 id 的响应。',
    next: 's2',
  },
  s2: {
    id: 's2',
    phase: '握手初始化',
    title: 'Server → Client：initialize 响应',
    direction: 's2c',
    msgType: 'response',
    payload: {
      jsonrpc: '2.0',
      id: 1,
      result: {
        protocolVersion: '2025-06-18',
        capabilities: { tools: { listChanged: true } },
        serverInfo: { name: 'demo-calculator-server', version: '0.1.0' },
      },
    },
    explain:
      'Server 回同一个 id=1，宣告自己支持的能力：这里提供 tools（工具）。双方按「共同支持的最高版本」协商 protocolVersion。如果 Server 不认识 2025-06-18，可以回退到旧版本。',
    next: 's3',
  },
  s3: {
    id: 's3',
    phase: '握手初始化',
    title: 'Client → Server：notifications/initialized 通知',
    direction: 'c2s',
    msgType: 'notification',
    method: 'notifications/initialized',
    payload: { jsonrpc: '2.0', method: 'notifications/initialized' },
    explain:
      '注意：这条没有 id！JSON-RPC 中不带 id 的就是通知（Notification）——发送方不期待任何响应，也不能重试。它告诉 Server「握手完成，可以开始正常服务了」。在此之前 Server 不应处理其他请求。',
    next: 's4',
  },
  s4: {
    id: 's4',
    phase: '能力发现',
    title: 'Client → Server：tools/list 请求',
    direction: 'c2s',
    msgType: 'request',
    method: 'tools/list',
    payload: { jsonrpc: '2.0', id: 2, method: 'tools/list', params: {} },
    explain:
      '握手完成后，Client 先「发现」Server 能提供什么。tools/list 返回工具清单。类似的还有 resources/list（资源）、prompts/list（提示词模板），它们共享同一套机制。',
    next: 's5',
  },
  s5: {
    id: 's5',
    phase: '能力发现',
    title: 'Server → Client：工具清单（含 JSON Schema）',
    direction: 's2c',
    msgType: 'response',
    payload: {
      jsonrpc: '2.0',
      id: 2,
      result: {
        tools: [
          {
            name: 'add',
            description: '计算两个数之和',
            inputSchema: {
              type: 'object',
              properties: {
                a: { type: 'number', description: '加数 a' },
                b: { type: 'number', description: '加数 b' },
              },
              required: ['a', 'b'],
            },
          },
          {
            name: 'get_weather',
            description: '查询城市天气',
            inputSchema: {
              type: 'object',
              properties: { city: { type: 'string', description: '城市名' } },
              required: ['city'],
            },
          },
        ],
      },
    },
    explain:
      '每个工具都有 name、description 和 inputSchema（JSON Schema）。这就是 LLM 能「自动学会」调工具的秘密：Client 把这份 Schema 塞进模型上下文，模型生成符合 Schema 的 arguments。',
    next: 's6',
  },
  s6: {
    id: 's6',
    phase: '工具调用',
    title: 'Client → Server：tools/call 请求',
    direction: 'c2s',
    msgType: 'request',
    method: 'tools/call',
    payload: {
      jsonrpc: '2.0',
      id: 3,
      method: 'tools/call',
      params: { name: 'add', arguments: { a: 2, b: 3 } },
    },
    explain:
      '用户说「帮我算 2+3」，LLM 根据第 5 步的 Schema 生成 arguments，Client 代为发起调用。这是 MCP 的核心：模型不直接执行代码，而是 Client 拿着结构化参数去调 Server。',
    next: 's7',
  },
  s7: {
    id: 's7',
    phase: '工具调用',
    title: 'Server → Client：调用结果',
    direction: 's2c',
    msgType: 'response',
    payload: {
      jsonrpc: '2.0',
      id: 3,
      result: {
        content: [{ type: 'text', text: '5' }],
        structuredContent: { sum: 5 },
        isError: false,
      },
    },
    explain:
      'result.content 是给 LLM 看的文本内容数组（还可有 image、resource 等类型）；structuredContent 是机器可读的结构化结果（可选）；isError 标记业务失败。Client 把它转给模型，模型继续对话。',
    next: 's8',
  },
  s8: {
    id: 's8',
    phase: '异常与边界',
    title: '反例：参数不合法时 Server 怎么处理',
    direction: 's2c',
    msgType: 'response',
    payload: {
      jsonrpc: '2.0',
      id: 4,
      result: {
        content: [{ type: 'text', text: 'Error: missing required argument "b"' }],
        isError: true,
      },
    },
    explain:
      '比如调用 add 只传了 {a: 2}。注意 MCP 的做法：JSON-RPC 层面仍是成功响应（有 result），但 isError: true 表示业务失败——这样 Client 能把错误文本喂回给 LLM，让它自我纠正后重试。只有协议级错误（方法不存在、解析失败）才用 JSON-RPC error 字段。',
    note: '对比 JSON-RPC 协议级错误：{"jsonrpc":"2.0","id":4,"error":{"code":-32601,"message":"Method not found"}}',
    next: 's9',
  },
  s9: {
    id: 's9',
    phase: '异常与边界',
    title: 'Server 主动推送：notifications/tools/list_changed',
    direction: 's2c',
    msgType: 'notification',
    method: 'notifications/tools/list_changed',
    payload: { jsonrpc: '2.0', method: 'notifications/tools/list_changed' },
    explain:
      '能力不是静态的：Server 可以在运行时增删工具，然后推一条通知（同样没有 id）。Client 收到后应重新 tools/list。这是 MCP 里 Server→Client 方向的典型消息，此外还有 resources/updated、采样请求 sampling/createMessage 等。',
    next: 's10',
  },
  s10: {
    id: 's10',
    phase: '会话结束',
    title: 'Client → Server：ping 保活 + 正常关闭',
    direction: 'c2s',
    msgType: 'request',
    method: 'ping / shutdown',
    payload: { jsonrpc: '2.0', id: 5, method: 'ping', params: {} },
    explain:
      'ping 用来检测连接健康（也可由 Server 发起）。关闭会话时 Client 依次发 shutdown 请求 → 收到响应后发 notifications/exit 通知 → 终止子进程。整个生命周期结束。',
    note: '随后：{"jsonrpc":"2.0","method":"notifications/exit"} → Server 进程退出',
    next: null,
  },
}

export const MCP_START = 's0'
