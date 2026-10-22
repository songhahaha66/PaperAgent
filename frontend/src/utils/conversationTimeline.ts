import type { ResearchActivity } from '@/types/research'
export interface ExecutionDetail extends ResearchActivity {
  recovered?: boolean
  detail?: string
  script_file?: string | null
  error_file?: string | null
  research_id?: string
  file?: string
  output_file?: string | null
  output_tail?: string
  result_ids?: string[]
  segment_id?: string
}
export type ConversationSegment =
  | { id: string; type: 'text'; text: string; phase?: 'commentary'; scope?: string }
  | { id: string; type: 'event'; block: { type: string; content: unknown } }
  | {
      id: string
      type: 'execution'
      ref: { research_id: string; kind: string; id: string }
      execution: ExecutionDetail
    }
export interface TimelineMessage {
  id: string
  content: string
  segments?: ConversationSegment[]
  json_blocks?: { type: string; content: unknown }[]
  metadata?: { reply_id?: string; chat_task_id?: string; revision?: number; status?: string }
}
export interface ReplyUpdate {
  reply_id: string
  chat_task_id: string
  revision: number
  status: string
  segments: ConversationSegment[]
}
const hidden = new Set([
  'plan_updated',
  'file_changed',
  'research_snapshot',
  'research_state',
  'research_activity',
])
// Legacy formats never recorded interleaving: text first, then explicit blocks.
// JSON-looking prose is always Markdown.
export function messageSegments(message: TimelineMessage): ConversationSegment[] {
  if (message.segments)
    return message.segments.filter((s) => s.type !== 'execution' || s.ref.kind !== 'model')
  return [
    ...(message.content
      ? [{ id: 'legacy-text', type: 'text' as const, text: message.content }]
      : []),
    ...(message.json_blocks || [])
      .filter((block) => block && !hidden.has(block.type))
      .map((block, index) => ({ id: `legacy-${index}`, type: 'event' as const, block })),
  ]
}
export function applyReplyUpdate<T extends TimelineMessage>(message: T, update: ReplyUpdate): T {
  if (
    message.metadata?.reply_id === update.reply_id &&
    (message.metadata.revision || 0) >= update.revision
  )
    return message
  const segments = [...(message.segments || [])]
  for (const segment of update.segments) {
    const index = segments.findIndex((row) => row.id === segment.id)
    if (index === -1) segments.push(segment)
    else segments[index] = segment
  }
  return {
    ...message,
    id: update.reply_id,
    segments,
    content: segments
      .filter((s) => s.type === 'text')
      .map((s) => s.text)
      .join(''),
    metadata: {
      ...message.metadata,
      reply_id: update.reply_id,
      chat_task_id: update.chat_task_id,
      revision: update.revision,
      status: update.status,
    },
  }
}
// Keep prose as a boundary; adjacent visible operations share an execution row.
const progressEvents = new Set([
  'paper_stage',
  'paper_phase',
  'paper_code',
  'paper_solved',
  'paper_export',
  'paper_validation',
])
const maintenance = new Set([
  'experience.candidates',
  'research.observe',
  'research.update_question',
  'research.deliver',
  'experiments.execute',
])
const failureStates = new Set(['failed', 'error', 'interrupted', 'cancelled'])
// Presentation only: retain original text and execution status in stored history.
export function progressText(text: string) {
  return text
    .replace(/(?:子问题|实验)\s*E\d+\b/g, '当前实验')
    .replace(/question:[a-f\d]+/g, '当前问题')
    .replace(/登记(这些)?结果/g, '保存$1计算结果')
}
export function timelineGroups(message: TimelineMessage) {
  const original = messageSegments(message)
  const segments = original.map((segment, index) => {
    if (segment.type !== 'execution') return segment
    const row = segment.execution
    const recoverable = row.capability === 'experiments.execute' || row.capability?.startsWith('writing.')
    const recovered = failureStates.has(row.status) && recoverable && original.slice(index + 1).some(later =>
      later.type === 'execution' && later.execution.capability === row.capability &&
      later.execution.experiment_id === row.experiment_id && later.ref.research_id === segment.ref.research_id &&
      later.execution.status === 'completed' && !!later.execution.finished_at)
    return { ...segment, execution: { ...row, recovered: !!recovered,
      label: row.capability === 'record_results' ? '保存计算结果'
        : row.capability === 'research.read_artifact' && /(?:^|\/)report-[\w-]+\.json$/.test(row.file || '')
          ? '读取实验报告' : row.label } }
  })
  const hasCalls = segments.some((s) => s.type === 'execution')
  const groups: {
    id: string
    segments: ConversationSegment[]
    execution: boolean
    label?: string
  }[] = []
  for (const segment of segments) {
    if (segment.type === 'event' && segment.block.type === 'main_agent_start') continue
    if (
      segment.type === 'execution' &&
      maintenance.has(segment.execution.capability || '') &&
      !failureStates.has(segment.execution.status)
    ) {
      continue
    }
    if (hasCalls && segment.type === 'event' && progressEvents.has(segment.block.type)) {
      // Stage receipts often repeat tools' status or the agent's explanation.
      // The durable timeline retains them; research details expose actual activities.
      continue
    }
    const last = groups.at(-1)
    const execution = segment.type === 'execution'
    if (execution && last?.execution) {
      last.segments.push(segment)
      last.label = '执行记录'
    } else {
      groups.push({ id: segment.id, segments: [segment], execution })
    }
  }
  return groups
}
