import type { ResearchActivity } from '@/types/research'
export interface ExecutionDetail extends ResearchActivity {
  detail?: string
  script_file?: string | null
  error_file?: string | null
  research_id?: string
  file?: string
  output_file?: string | null
  result_ids?: string[]
  segment_id?: string
}
export type ConversationSegment =
  | { id: string; type: 'text'; text: string }
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
  if (message.segments) return message.segments
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
// Fold progress receipts with their calls. Actual prose, questions and final answers
// remain boundaries: execution never moves across something the agent said.
const progressEvents = new Set([
  'paper_stage',
  'paper_phase',
  'paper_code',
  'paper_solved',
  'paper_export',
  'paper_validation',
])
export function timelineGroups(message: TimelineMessage) {
  const segments = messageSegments(message)
  const hasCalls = segments.some((s) => s.type === 'execution')
  const groups: { id: string; segments: ConversationSegment[]; execution: boolean }[] = []
  for (const segment of segments) {
    if (segment.type === 'event' && segment.block.type === 'main_agent_start') continue
    const execution =
      segment.type === 'execution' ||
      (hasCalls && segment.type === 'event' && progressEvents.has(segment.block.type))
    const last = groups.at(-1)
    if (execution && last?.execution) last.segments.push(segment)
    else groups.push({ id: segment.id, segments: [segment], execution })
  }
  return groups
}
