// Research events retained in compact per-message execution records.
// 后端事件定义见 backend/ai_system/graph（EventSink.emit / GraphContext.set_stage）
import type { ResearchSnapshot } from '@/types/research'

export interface PipelineBlock {
  type: string
  content: unknown
}

export type SubproblemStatus = 'in_progress' | 'completed' | 'failed'

export interface SubproblemProgress {
  id: string
  name: string
  status: SubproblemStatus
  runs: number
  fails: number
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

const PAPER_EVENT_TYPES = new Set([
  'paper_analysis',
  'paper_design',
  'paper_explore',
  'paper_code',
  'paper_review',
  'paper_solved',
  'paper_stage',
  'paper_phase',
  'paper_consistency',
  'paper_validation',
  'paper_export',
  'paper_confirm',
])
export const isPaperEvent = (block: PipelineBlock | undefined): boolean =>
  !!block && PAPER_EVENT_TYPES.has(block.type)

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

export const deriveSubproblems = (
  blocks: PipelineBlock[],
  tasks: Pick<NonNullable<ResearchSnapshot['tasks']>[number], 'task_id' | 'objective'>[] = [],
): SubproblemProgress[] => {
  const byId = new Map<string, SubproblemProgress>()
  const names = new Map(
    tasks.filter((t) => t.objective?.trim()).map((t) => [t.task_id, t.objective.trim()]),
  )
  for (const block of blocks) {
    if (block.type === 'paper_analysis') {
      for (const line of asText(block.content).split('\n')) {
        const match = /^-\s+(Q\d+[a-z]?)\s+(.+)$/.exec(line.trim())
        if (match) names.set(match[1]!, match[2]!.trim())
      }
    }
  }
  for (const block of blocks) {
    if (!SUBPROBLEM_TYPES.has(block.type)) continue
    const text = asText(block.content).trim()
    const match = SUBPROBLEM_PREFIX.exec(text)
    if (!match) continue
    const id = match[1] as string
    const item = byId.get(id) ?? {
      id,
      name: names.get(id) || id,
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
    if (block.type === 'paper_solved') {
      item.status = text.includes('未完成') ? 'failed' : 'completed'
    } else {
      item.status = 'in_progress'
    }
  }
  return [...byId.values()].sort((a, b) => a.id.localeCompare(b.id, undefined, { numeric: true }))
}
