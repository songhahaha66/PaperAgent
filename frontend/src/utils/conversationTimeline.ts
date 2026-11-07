/** Agent protocol. A Turn owns ordered, typed Items; events replace one Item by ID. */
export type ItemStatus = 'running' | 'completed' | 'failed' | 'interrupted'
export interface ItemBase {
  id: string
  thread_id: string
  turn_id: string
  sequence: number
  status: ItemStatus
  started_at: number
  finished_at?: number
  updated_at?: number
}
export interface MessageItem extends ItemBase {
  type: 'userMessage' | 'agentMessage'
  text: string
  phase: 'commentary' | 'final_answer'
}
export interface ToolItem extends ItemBase {
  type: 'toolCall' | 'commandExecution' | 'fileChange' | 'collabToolCall'
  tool_name: string
  call_id: string
  label: string
  detail?: string
  message?: string
  error_type?: string
  phase?: string
  research_id?: string
  action_id?: string
  observation_id?: string
  question_id?: string
  experiment_id?: string
  task_id?: string
  subject?: string
  run_id?: string
  child_thread_id?: string
  child_turn_id?: string
  file?: string
  output_file?: string
  script_file?: string
  error_file?: string
  files?: string[]
  result_ids?: string[]
  warnings?: string[]
  output_tail?: string
}
export interface PlanItem extends ItemBase {
  type: 'plan'
  explanation: string
  steps: {
    step: string
    status: 'pending' | 'in_progress' | 'completed' | 'blocked'
    note: string
  }[]
}
export interface NoticeItem extends ItemBase {
  type: 'notice' | 'inputRequest'
  name: string
  content: string | Record<string, unknown>
}
export type ConversationItem = MessageItem | ToolItem | PlanItem | NoticeItem
export interface TurnMetadata {
  reply_id?: string
  chat_task_id?: string
  thread_id?: string
  turn_id?: string
  event_seq?: number
  status?: string
}
export interface TurnMessage {
  id: string
  content: string
  items?: ConversationItem[]
  metadata?: TurnMetadata
}
export interface AgentEvent {
  seq: number
  method:
    | 'thread/started'
    | 'turn/started'
    | 'turn/completed'
    | 'item/started'
    | 'item/updated'
    | 'item/completed'
    | 'item/agentMessage/delta'
  thread_id: string
  turn_id?: string
  item?: ConversationItem
  turn?: { id: string; thread_id: string; root_turn_id: string; status: ItemStatus }
  thread?: { id: string; parent_thread_id?: string; parent_item_id?: string }
}
export interface AgentUpdate {
  reply_id: string
  chat_task_id: string
  thread_id: string
  turn_id: string
  event: AgentEvent
}
export function isToolItem(item: ConversationItem): item is ToolItem {
  return ['toolCall', 'commandExecution', 'fileChange', 'collabToolCall'].includes(item.type)
}
export function applyAgentEvent<T extends TurnMessage>(message: T, update: AgentUpdate): T {
  const event = update.event
  if (
    message.metadata?.turn_id === update.turn_id &&
    (message.metadata.event_seq || 0) >= event.seq
  )
    return message
  if (message.metadata?.turn_id && message.metadata.turn_id !== update.turn_id) return message
  const items = [...(message.items || [])]
  if (event.item) {
    const index = items.findIndex((item) => item.id === event.item!.id)
    if (index === -1) items.push(event.item)
    else if (items[index]!.status === 'running' || event.item.status !== 'running')
      items[index] = event.item
  }
  const status =
    event.method === 'turn/completed' && event.turn_id === update.turn_id
      ? event.turn!.status
      : message.metadata?.status || 'running'
  return {
    ...message,
    id: update.reply_id,
    items,
    content: items
      .filter(
        (item): item is MessageItem =>
          item.type === 'agentMessage' && item.thread_id === update.thread_id,
      )
      .map((item) => item.text)
      .join('\n\n'),
    metadata: {
      ...message.metadata,
      reply_id: update.reply_id,
      chat_task_id: update.chat_task_id,
      thread_id: update.thread_id,
      turn_id: update.turn_id,
      event_seq: event.seq,
      status,
    },
  }
}
export function visibleItems(message: TurnMessage) {
  return (message.items || [])
    .filter((item) => item.thread_id === message.metadata?.thread_id && item.type !== 'userMessage')
    .sort((a, b) => a.sequence - b.sequence)
}

export type OperationItem = ToolItem | PlanItem
/** A display group only: its first Item ID stays stable as streaming adds tools. */
export interface OperationGroup {
  type: 'operationGroup'
  id: string
  operations: OperationItem[]
}
export type ConversationEntry = Exclude<ConversationItem, OperationItem> | OperationGroup
export function groupAdjacentOperations(items: ConversationItem[]): ConversationEntry[] {
  const entries: ConversationEntry[] = []
  for (const item of items) {
    if (isToolItem(item) || item.type === 'plan') {
      const previous = entries.at(-1)
      const first = previous?.type === 'operationGroup' ? previous.operations[0] : undefined
      if (
        previous?.type === 'operationGroup' &&
        first?.thread_id === item.thread_id &&
        first.turn_id === item.turn_id
      ) {
        previous.operations.push(item)
      } else entries.push({ type: 'operationGroup', id: item.id, operations: [item] })
    } else entries.push(item)
  }
  return entries
}
export function conversationEntries(message: TurnMessage) {
  return groupAdjacentOperations(visibleItems(message))
}
export function operationGroupTitle(items: OperationItem[]): string {
  const tools = items.filter(isToolItem)
  const names = new Set(tools.map((item) => item.tool_name))
  const writing = tools.some((item) => item.type === 'fileChange')
  const preview = names.has('writing.preview')
  const exporting = names.has('writing.export') || names.has('research.deliver')
  if (writing && preview && exporting) return '编写、检查与导出论文'
  if (writing && preview) return '编写与检查论文'
  if (writing && exporting) return '编写与导出论文'
  if (preview && exporting) return '检查与导出论文'
  if (exporting) return '导出论文'
  if (writing) return '编写论文'
  if (preview) return '检查论文'
  if (tools.some((item) => item.type === 'commandExecution')) return '计算与分析'
  if (tools.some((item) => item.type === 'collabToolCall')) {
    return tools.some((item) =>
      ['read_file', 'research.read_artifact', 'research.list_files'].includes(item.tool_name),
    )
      ? '查阅资料与委派任务'
      : '执行独立子任务'
  }
  if (!tools.length) return '更新研究计划'
  if (
    tools.every((item) =>
      /^(read_file|search_literature|literature\.|research\.(read_|list_|search_))/.test(
        item.tool_name,
      ),
    )
  )
    return '查阅研究资料'
  return '推进研究'
}
export function progressText(text: string) {
  return text
    .replace(/(?:子问题|实验)\s*E\d+\b/g, '当前实验')
    .replace(/question:[a-f\d]+/g, '当前问题')
    .replace(/登记(这些)?结果/g, '保存$1计算结果')
}
