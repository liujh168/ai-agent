export type Direction = 'c2s' | 's2c' | 'internal'
export type MsgType = 'request' | 'response' | 'notification' | 'decision'

export interface Choice {
  label: string
  hint?: string
  next: string
}

export interface StepNode {
  id: string
  phase: string
  title: string
  direction: Direction
  msgType: MsgType
  method?: string
  payload: object | string
  payloadLang?: 'json' | 'python' | 'bash'
  explain: string
  note?: string
  /** 线性后续节点 id；null/缺省表示路径终点 */
  next?: string | null
  /** 决策分支（与 next 互斥）：展示选项等待用户选择 */
  choices?: Choice[]
  /** 自动播放时在决策点默认选中的选项下标（默认 0） */
  defaultChoice?: number
}

export const fmt = (p: object | string) =>
  typeof p === 'string' ? p : JSON.stringify(p, null, 2)
