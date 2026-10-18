// Research events retained in compact per-message execution records.
// 后端事件定义见 backend/ai_system/graph（EventSink.emit / GraphContext.set_stage）
import type { ResearchSnapshot } from '@/types/research'

export interface PipelineBlock {
  type: string
  content: unknown
}

export type SubproblemStatus = 'pending' | 'in_progress' | 'completed' | 'failed'

export interface SubproblemProgress {
  id: string
  text: string
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
  const current = [...blocks].reverse().find((b) => b.type === 'research_state')?.content
  if (current && typeof current === 'object') {
    const snapshot = current as Pick<ResearchSnapshot, 'tasks' | 'issues'>
    for (const task of snapshot.tasks || []) {
      const item: SubproblemProgress = byId.get(task.task_id) ?? {
        id: task.task_id,
        text: task.objective,
        status: 'pending',
        runs: 0,
        fails: 0,
      }
      const blocked = snapshot.issues?.some(
        (i) =>
          i.task_id === task.task_id && i.blocking && !['resolved', 'dismissed'].includes(i.status),
      )
      item.status =
        blocked ||
        task.execution_status === 'failed' ||
        ['rejected', 'inconclusive'].includes(task.acceptance)
          ? 'failed'
          : task.acceptance === 'accepted' && task.validity === 'current'
            ? 'completed'
            : task.execution_status === 'running'
              ? 'in_progress'
              : 'pending'
      byId.set(task.task_id, item)
    }
  }
  return [...byId.values()].sort((a, b) => a.id.localeCompare(b.id, undefined, { numeric: true }))
}
