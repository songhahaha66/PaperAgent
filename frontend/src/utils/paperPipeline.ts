// 状态图流水线（AI_PIPELINE=v2）推送的 paper_* 事件 → 聊天里的阶段卡片
// 后端事件定义见 backend/ai_system/graph（EventSink.emit / GraphContext.set_stage）

export interface PipelineBlock {
  type: string
  content: unknown
}

export type PhaseStatus = 'pending' | 'in_progress' | 'completed' | 'blocked'

export interface Phase {
  key: string
  label: string
  status: PhaseStatus
  note?: string
}

export type SubproblemStatus = 'in_progress' | 'completed' | 'failed'

export interface SubproblemProgress {
  id: string
  text: string
  status: SubproblemStatus
  runs: number
  fails: number
}

// 与 backend/ai_system/graph/context.py 的 STAGES 顺序一致
const PHASES: Array<{ key: string; label: string }> = [
  { key: 'analyze', label: '题目分析' },
  { key: 'design', label: '方案设计' },
  { key: 'confirm', label: '方案确认' },
  { key: 'solve', label: '编码求解' },
  { key: 'write', label: '论文撰写' },
  { key: 'validate', label: '校验修复' },
  { key: 'render', label: '生成论文' },
  { key: 'export', label: '排版导出' },
]

// 只在 latex / word 输出模式出现的阶段，没有收到它的事件时不显示
const OPTIONAL_PHASES = new Set(['export'])

// 没有 paper_phase 事件的旧记录，按事件类型推断阶段
const TYPE_TO_PHASE: Record<string, string> = {
  paper_analysis: 'analyze',
  paper_design: 'design',
  paper_confirm: 'confirm',
  paper_explore: 'solve',
  paper_code: 'solve',
  paper_review: 'solve',
  paper_solved: 'solve',
  paper_consistency: 'write',
  paper_validation: 'validate',
  paper_export: 'export',
}

const SUBPROBLEM_TYPES = new Set([
  'paper_design',
  'paper_explore',
  'paper_code',
  'paper_review',
  'paper_solved',
  'paper_stage',
])

// 子问题编号可以带字母后缀（Q1a、Q1b）
const SUBPROBLEM_PREFIX = /^(Q\d+[a-z]?)\s*[：:·\s]\s*/

export const isPaperEvent = (block: PipelineBlock | undefined): boolean =>
  !!block && typeof block.type === 'string' && block.type.startsWith('paper_')

export const isPipelineMessage = (blocks: PipelineBlock[] | undefined): boolean =>
  !!blocks && blocks.some(isPaperEvent)

export const asText = (content: unknown): string => {
  if (content === undefined || content === null) return ''
  if (typeof content === 'string') return content
  try {
    return JSON.stringify(content)
  } catch {
    return String(content)
  }
}

interface PhaseUpdate {
  stage: string
  status: PhaseStatus
  note?: string
}

const parsePhaseUpdate = (content: unknown): PhaseUpdate | null => {
  let value = content
  if (typeof value === 'string') {
    try {
      value = JSON.parse(value)
    } catch {
      return null
    }
  }
  if (!value || typeof value !== 'object') return null
  const { stage, status, note } = value as Record<string, unknown>
  if (typeof stage !== 'string' || typeof status !== 'string') return null
  return { stage, status: status as PhaseStatus, note: typeof note === 'string' ? note : undefined }
}

export const derivePhases = (
  blocks: PipelineBlock[],
  opts: { done?: boolean; failed?: boolean } = {},
): Phase[] => {
  const status: Record<string, PhaseStatus> = {}
  const notes: Record<string, string> = {}
  const updates = blocks
    .filter((b) => b.type === 'paper_phase')
    .map((b) => parsePhaseUpdate(b.content))

  if (updates.some(Boolean)) {
    for (const update of updates) {
      if (!update) continue
      status[update.stage] = update.status
      if (update.note) notes[update.stage] = update.note
    }
  } else {
    // 旧记录：出现过事件的阶段里，最后一个算进行中，之前的算完成
    const seen = new Set(blocks.map((b) => TYPE_TO_PHASE[b.type]).filter(Boolean))
    const order = PHASES.map((p) => p.key)
    const lastIndex = Math.max(-1, ...[...seen].map((key) => order.indexOf(key as string)))
    order.forEach((key, i) => {
      if (i < lastIndex) status[key] = 'completed'
      else if (i === lastIndex) status[key] = opts.done ? 'completed' : 'in_progress'
    })
    if (opts.done && lastIndex >= 0) {
      order.slice(0, order.indexOf('render') + 1).forEach((key) => (status[key] = 'completed'))
    }
  }

  // 默认按需设计时，设计在求解分支里进行，后面的阶段开始了它还没被标记；排在已开始阶段之前的都算完成
  const shown = PHASES.filter((p) => !OPTIONAL_PHASES.has(p.key) || status[p.key])
  return shown.map((p) => ({
    ...p,
    status:
      opts.failed && !opts.done && status[p.key] === 'in_progress'
        ? 'blocked'
        : (status[p.key] ?? 'pending'),
    note: notes[p.key],
  }))
}

export const deriveSubproblems = (blocks: PipelineBlock[]): SubproblemProgress[] => {
  const byId = new Map<string, SubproblemProgress>()
  for (const block of blocks) {
    if (!SUBPROBLEM_TYPES.has(block.type)) continue
    const text = asText(block.content).trim()
    const match = SUBPROBLEM_PREFIX.exec(text)
    if (!match) continue
    const id = match[1] as string
    const item = byId.get(id) ?? {
      id,
      text: '',
      status: 'in_progress' as SubproblemStatus,
      runs: 0,
      fails: 0,
    }
    byId.set(id, item)

    if (block.type === 'paper_code') {
      // "Q1 · run_xxx · 成功/失败"
      item.runs += 1
      if (text.endsWith('失败')) item.fails += 1
      continue
    }
    item.text = text.slice(match[0].length)
    if (block.type === 'paper_solved') {
      item.status = text.includes('未完成') ? 'failed' : 'completed'
    } else {
      item.status = 'in_progress'
    }
  }
  return [...byId.values()].sort((a, b) => a.id.localeCompare(b.id, undefined, { numeric: true }))
}
