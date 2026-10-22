import type { StepNode } from '../../types'

/**
 * FC 循环与安全门 —— 分支图
 * 主线：用户输入 → LLM#1 → 安全门决策(确认/拒绝/看风险表) → 回写 → LLM#2 → 自动放行 → LLM#3 → 结束
 * 角色：左侧 Agent 运行时（runtime.py + tooling.py），右侧 LLM API（LM Studio）
 */
export const FC_NODES: Record<string, StepNode> = {
  f0: {
    id: 'f0',
    phase: '循环开始',
    title: '用户输入进入 AgentRuntime.run()：「把 report.txt 复制为 report_backup.txt，再算一下 2+3」',
    direction: 'internal',
    msgType: 'request',
    method: 'runtime.py',
    payload:
      '# AgentRuntime.run() 的 function calling 循环\ndef run(self, user_text):\n    messages = [system, {"role": "user", ...}]\n    for i in range(max_iterations):   # 循环上限防死循环\n        resp = llm.chat(messages, tools=TOOL_SCHEMAS)\n        if resp.finish_reason == "stop":\n            return resp.content        # 模型直接作答 → 退出循环\n        for call in resp.tool_calls:\n            result = executor.run(call)      # ← 安全门在这里\n            messages.append(tool_result(call, result))  # 结果回写历史',
    explain:
      'FC 循环的本质：不断把「历史 + 工具 schema」发给模型，模型要么直接回答（finish_reason=stop，循环结束），要么发起 tool_calls（执行后把结果回写进历史，再循环）。max_iterations 是防死循环的保险丝。',
    next: 'f1',
  },
  f1: {
    id: 'f1',
    phase: '第 1 轮循环',
    title: 'Agent → LLM：第 1 次 API 请求（system + user + 22 种工具 schema）',
    direction: 'c2s',
    msgType: 'request',
    method: 'POST /v1/chat/completions',
    payload: {
      model: 'local-model',
      messages: [
        { role: 'system', content: '你是一个 AI Agent，可用工具完成任务…' },
        { role: 'user', content: '把 report.txt 复制为 report_backup.txt，再算一下 2+3' },
      ],
      tools: [
        { type: 'function', function: { name: 'write_file', description: '写入文件…' } },
        { type: 'function', function: { name: 'add', description: '计算两数之和…' } },
        { type: 'function', function: { name: '…（共 22 种能力）', description: '…' } },
      ],
      tool_choice: 'auto',
    },
    explain:
      '注意：每次请求都把完整历史 + 全部工具 schema 一起发过去。这就是「API 调用检查器」页签里能看到的输入——模型是在这些 schema 中选择调用哪个工具。',
    next: 'f2',
  },
  f2: {
    id: 'f2',
    phase: '第 1 轮循环',
    title: 'LLM 返回 tool_calls：模型决定先调 write_file（结构化 JSON，不是文字）',
    direction: 's2c',
    msgType: 'response',
    payload: {
      choices: [
        {
          index: 0,
          message: {
            role: 'assistant',
            content: null,
            tool_calls: [
              {
                id: 'call_01',
                type: 'function',
                function: {
                  name: 'write_file',
                  arguments: '{"path": "report_backup.txt", "content": "<report.txt 的内容>"}',
                },
              },
            ],
          },
          finish_reason: 'tool_calls',
        },
      ],
    },
    explain:
      'content 为 null、finish_reason=tool_calls —— 模型没有直接回答，而是给出了结构化的调用指令。arguments 是符合 write_file schema 的 JSON 字符串。接下来进入工具执行，而执行前的最后一道关卡就是安全门。',
    next: 'f3',
  },
  f3: {
    id: 'f3',
    phase: '安全门',
    title: 'ToolExecutor 风险分类：write_file 是「写操作」→ L3，必须人工确认',
    direction: 'internal',
    msgType: 'decision',
    method: 'tooling.py',
    payload: {
      tool: 'write_file',
      arguments: { path: 'report_backup.txt', content: '...' },
      风险分类: 'L3 写操作（文件系统写入）',
      策略: '人工确认后执行',
      依据: '教学化简的五级表（真实实现见 tooling.py：RiskKind 五类 + ToolPolicy 开关）',
    },
    explain:
      '安全门不在模型侧，而在执行侧：ToolExecutor 根据工具名和参数做五级风险分类。write_file 属于 L3——即便模型被注入恶意指令要求覆盖系统文件，也会在这里被拦下等待人工确认。这个决策点交给你：',
    choices: [
      { label: '✅ 用户确认执行', hint: '正常路径：放行 write_file', next: 'f4a' },
      { label: '🚫 用户拒绝', hint: '观察拒绝后模型如何收拾残局', next: 'f4b' },
      { label: '📋 先看一下五级风险表', hint: '了解 L1~L5 的完整分类', next: 'f_levels' },
    ],
    defaultChoice: 0,
  },
  f_levels: {
    id: 'f_levels',
    phase: '安全门',
    title: '五级风险分类表（tooling.py 的核心设计）',
    direction: 'internal',
    msgType: 'request',
    method: 'RISK_LEVELS',
    payload: `L1  只读·低风险   add / get_weather            → 自动执行
L2  只读·敏感     read_file / sql_query        → 自动执行 + 留痕
L3  写操作        write_file / db_insert       → ⏸ 人工确认
L4  外发网络      http_request / wordpress     → ⏸ 人工确认 + 审计
L5  高危执行      run_python / run_shell       → 🚫 Web UI 直接禁用

边界之外还有：文件路径必须落在工作区内（防目录穿越）、SQL 只读连接。`,
    payloadLang: 'python',
    explain:
      '分级的好处是「常态高效、异常兜底」：绝大多数调用是 L1/L2 全自动，用户无感；危险操作才打断人。Web UI 模式下 L5 直接被禁用（页面上的「安全边界」说明写的就是这件事）。',
    next: 'f3',
  },
  f4a: {
    id: 'f4a',
    phase: '第 1 轮循环',
    title: '确认 → 执行 write_file → 结果以 role:tool 消息回写历史',
    direction: 'internal',
    msgType: 'response',
    method: 'tool result',
    payload: {
      role: 'tool',
      tool_call_id: 'call_01',
      name: 'write_file',
      content: 'ok: 已写入 report_backup.txt（1280 字节）',
    },
    payloadLang: 'json',
    explain:
      '这是循环的关键一步：工具的执行结果不是打印给人看，而是包装成 role=tool 消息、带着 tool_call_id 回写进 messages。模型在下一轮据此「看到」执行结果，决定下一步做什么。',
    next: 'f5',
  },
  f4b: {
    id: 'f4b',
    phase: '第 1 轮循环',
    title: '拒绝 → 回写一个「操作被拒绝」的 tool 结果',
    direction: 'internal',
    msgType: 'response',
    method: 'tool result',
    payload: {
      role: 'tool',
      tool_call_id: 'call_01',
      name: 'write_file',
      content: 'Error: 操作被用户拒绝（安全确认未通过）',
      isError: true,
    },
    payloadLang: 'json',
    explain:
      '拒绝同样要回写成 tool 消息——模型需要知道「刚才那个调用没执行」。诚实地告诉它原因，模型通常会道歉并给出替代方案（比如只列出建议的操作步骤）。这就是安全层的正确姿态：拦截但不撒谎。',
    next: 'f5',
  },
  f5: {
    id: 'f5',
    phase: '第 2 轮循环',
    title: 'Agent → LLM：第 2 次请求（历史里已经多了那条 tool 结果）',
    direction: 'c2s',
    msgType: 'request',
    method: 'POST /v1/chat/completions',
    payload: {
      model: 'local-model',
      messages: [
        { role: 'system', content: '…' },
        { role: 'user', content: '把 report.txt 复制为 report_backup.txt，再算一下 2+3' },
        { role: 'assistant', tool_calls: ['call_01 → write_file(...)'] },
        { role: 'tool', tool_call_id: 'call_01', content: 'ok / Error: 被拒绝' },
      ],
      tools: ['…（完整 schema 再次携带）'],
    },
    explain:
      '对照第 1 轮请求：messages 变长了——assistant 的 tool_calls 和 tool 的结果都进了历史。模型现在知道复制已完成（或被拒绝），还剩「算 2+3」没做。',
    next: 'f6',
  },
  f6: {
    id: 'f6',
    phase: '第 2 轮循环',
    title: 'LLM 返回第 2 个 tool_call：add —— L1 低风险，安全门直接放行',
    direction: 's2c',
    msgType: 'response',
    payload: {
      choices: [
        {
          message: {
            role: 'assistant',
            tool_calls: [
              { id: 'call_02', type: 'function', function: { name: 'add', arguments: '{"a": 2, "b": 3}' } },
            ],
          },
          finish_reason: 'tool_calls',
          安全门标注: 'add → L1 只读低风险 → 自动执行，无人工确认',
        },
      ],
    },
    explain:
      '和 write_file 形成对照：add 是纯内存计算，L1 直接执行。安全门的设计目标是「不打断常态」——22 种能力里大部分是 L1/L2，用户只在真正危险时才被询问。',
    next: 'f7',
  },
  f7: {
    id: 'f7',
    phase: '第 2 轮循环',
    title: '自动执行 add → 结果 "5" 回写历史',
    direction: 'internal',
    msgType: 'response',
    method: 'tool result',
    payload: { role: 'tool', tool_call_id: 'call_02', name: 'add', content: '5' },
    payloadLang: 'json',
    explain: 'L1 自动放行：无确认弹窗、无中断，循环继续。',
    next: 'f8',
  },
  f8: {
    id: 'f8',
    phase: '第 3 轮循环',
    title: 'Agent → LLM：第 3 次请求 —— 任务信息已齐，模型直接作答',
    direction: 'c2s',
    msgType: 'request',
    method: 'POST /v1/chat/completions',
    payload: {
      model: 'local-model',
      messages: ['…（前两轮完整历史）', { role: 'user', content: '…' }],
      tools: ['…'],
      期望: 'finish_reason = stop',
    },
    explain: '两个工具的结果都在历史里了，模型拥有的信息足够给出最终回答。',
    next: 'f9',
  },
  f9: {
    id: 'f9',
    phase: '循环结束',
    title: 'LLM 返回 finish_reason=stop —— 最终回答，循环退出',
    direction: 's2c',
    msgType: 'response',
    payload: {
      choices: [
        {
          message: { role: 'assistant', content: '已完成：report.txt 已复制为 report_backup.txt；2+3=5。' },
          finish_reason: 'stop',
        },
      ],
    },
    explain:
      'finish_reason=stop 是唯一的正常出口。另外三种出口：max_iterations 耗尽（保险丝熔断）、上下文超长触发压缩、模型报错。可以回到决策点试试「拒绝」分支，看看模型的收尾有何不同。',
    next: null,
  },
}

export const FC_START = 'f0'
