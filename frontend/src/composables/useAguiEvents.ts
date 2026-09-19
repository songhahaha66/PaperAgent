import type { Ref } from 'vue'
import type { ChatMessage } from '@/api/chat'
import type { PlanData } from '@/api/workspace'
import { markdownPlanToData } from '@/composables/useWorkPlan'

export interface ValidationIssuePreview {
  code: string
  slot_id?: string | null
  severity?: string
  detail?: string
}

export interface AguiEventOptions {
  chatMessages: Ref<ChatMessage[]>
  planData: Ref<PlanData | null>
  loadWorkspaceFiles: () => void
  awaitingConfirmation: Ref<boolean>
  confirmationIssues: Ref<ValidationIssuePreview[]>
  isStreaming: Ref<boolean>
  reconnectMessageId: Ref<string | null>
  scrollToBottom: () => void
}

export const CONFIRM_DRAFT_MESSAGE = '确认采用当前稿'

const STEP_LABELS: Record<string, string> = {
  load_context: '读取上下文',
  classify_intent: '识别意图',
  ensure_template_spec: '解析模板',
  gather: '准备图表数据',
  draft: '起草',
  revise: '按反馈修订',
  render: '渲染文档',
  validate: '校验文档',
}

/** Human-readable progress note for a STEP_STARTED event; empty for silent steps. */
export const describeStep = (payload: any): string => {
  const label = STEP_LABELS[payload?.node]
  if (!label) return ''
  const target = payload?.slot_title || payload?.slot_id
  return target ? `${label}：${target}` : label
}

/** Keep only the events of the most recent run so reloads don't replay stale runs. */
export const latestRunEvents = <T extends { run_id?: string }>(events: T[]): T[] => {
  const last = events.length ? events[events.length - 1]?.run_id : undefined
  if (!last) return events
  return events.filter((event) => event.run_id === last)
}

export function useAguiEvents(options: AguiEventOptions) {
  const handleAguiEvent = (event: any, messageId: string) => {
    if (!event) return
    const type = event.event_type
    const payload = event.payload || {}
    if (type === 'RUN_STARTED') {
      options.awaitingConfirmation.value = false
      return
    }
    if (type === 'CUSTOM' && payload.type === 'confirmation_resolved') {
      options.awaitingConfirmation.value = false
      options.confirmationIssues.value = []
      return
    }
    if (type === 'STATE_DELTA' && payload.type === 'plan_updated' && payload.content) {
      options.planData.value = payload.content
      return
    }
    if (type === 'CUSTOM' && payload.type === 'render_done') {
      setTimeout(() => options.loadWorkspaceFiles(), 300)
      return
    }
    if (type === 'CUSTOM' && payload.type === 'awaiting_confirmation') {
      options.awaitingConfirmation.value = true
      options.confirmationIssues.value = payload.issues || []
      return
    }
    if (type === 'RUN_FINISHED' && String(payload.summary || '').includes('已确认')) {
      options.awaitingConfirmation.value = false
      options.confirmationIssues.value = []
      return
    }
    if (type === 'TEXT_MESSAGE_CONTENT' && payload.delta) {
      const messageIndex = options.chatMessages.value.findIndex((m) => m.id === messageId)
      if (messageIndex !== -1) {
        const currentMessage = options.chatMessages.value[messageIndex]
        options.chatMessages.value[messageIndex] = {
          ...currentMessage,
          content: currentMessage.content + payload.delta,
        }
      }
      return
    }
    if (type === 'STEP_STARTED' && payload.node) {
      const messageIndex = options.chatMessages.value.findIndex((m) => m.id === messageId)
      const label = describeStep(payload)
      if (messageIndex !== -1 && label) {
        const currentMessage = options.chatMessages.value[messageIndex]
        // Blank line after the quote so streamed answer text is not swallowed into it.
        const note = `\n> ${label}\n\n`
        if (!currentMessage.content.includes(note.trim())) {
          options.chatMessages.value[messageIndex] = {
            ...currentMessage,
            content: currentMessage.content + note,
          }
        }
      }
    }
  }

  const handleStreamMessage = (data: any, messageId: string) => {
    const messageIndex = options.chatMessages.value.findIndex((m) => m.id === messageId)
    if (messageIndex === -1) return

    const currentMessage = options.chatMessages.value[messageIndex]
    if (!currentMessage) return

    switch (data.type) {
      case 'content':
        options.chatMessages.value[messageIndex] = {
          ...currentMessage,
          content: currentMessage.content + data.content,
        }
        break

      case 'event':
        handleAguiEvent(data.event, messageId)
        break

      case 'json_block': {
        const block = data.block
        if (block?.type === 'file_changed') {
          setTimeout(() => options.loadWorkspaceFiles(), 500)
        } else if (block?.type === 'plan_updated') {
          if (block.content && typeof block.content === 'object') {
            options.planData.value = block.content as PlanData
          } else {
            options.planData.value = markdownPlanToData(String(block.content || ''))
          }
        } else {
          options.chatMessages.value[messageIndex] = {
            ...currentMessage,
            json_blocks: [...(currentMessage.json_blocks || []), block],
            message_type: 'json_card' as const,
          }
        }
        break
      }

      case 'complete':
        options.chatMessages.value[messageIndex] = {
          ...currentMessage,
          isStreaming: false,
        }
        options.isStreaming.value = false
        options.reconnectMessageId.value = null
        options.loadWorkspaceFiles()
        break

      case 'error':
        options.chatMessages.value[messageIndex] = {
          ...currentMessage,
          content: currentMessage.content || `错误: ${data.message}`,
          isStreaming: false,
        }
        options.isStreaming.value = false
        options.reconnectMessageId.value = null
        break
    }

    options.scrollToBottom()
  }

  return { handleAguiEvent, handleStreamMessage }
}
