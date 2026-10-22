<template>
  <div class="json-chat-renderer">
    <div class="chat-messages">
      <article
        v-for="(message, index) in messages"
        :key="message.id"
        class="chat-message-wrapper"
        :class="message.role === 'user' ? 'message-user' : 'message-assistant'"
      >
        <time v-if="message.datetime" class="message-time" :datetime="message.datetime">{{
          formatDateTime(message.datetime)
        }}</time>
        <div class="chat-markdown">
          <template v-for="group in timelineGroups(message)" :key="group.id">
            <ConversationExecution
              v-if="group.execution"
              :reply-id="message.id"
              :group-id="group.id"
              :segments="group.segments"
              :work-id="workId"
              :reply-status="message.metadata?.status"
              @file="$emit('open-file', $event)"
              @result="$emit('open-result', $event)"
            />
            <template v-else v-for="segment in group.segments" :key="segment.id">
              <MarkdownRenderer v-if="segment.type === 'text'" :content="segment.text" />
              <template v-else-if="segment.type === 'event'">
                <div v-if="segment.block.type === 'research_context'" class="answer-sources">
                  <span>参考材料</span>
                  <button
                    v-for="path in sourceFiles(segment.block)"
                    :key="path"
                    @click="
                      isResearchSource(path) ? $emit('open-progress') : $emit('open-file', path)
                    "
                  >
                    {{ researchSourceLabel(path, sourceFiles(segment.block)) }}
                  </button>
                  <button
                    v-for="id in sourceResults(segment.block)"
                    :key="id"
                    @click="$emit('open-result', id)"
                  >
                    {{ snapshot?.results.find((r) => r.id === id)?.name || '查看实测结果' }}
                  </button>
                </div>
                <template v-else-if="segment.block.type === 'main_agent_complete'">
                  <MarkdownRenderer :content="completionText(segment.block).summary" />
                  <details
                    v-if="completionText(segment.block).diagnostic"
                    class="completion-diagnostic"
                  >
                    <summary>编译详情</summary>
                    <pre>{{ completionText(segment.block).diagnostic }}</pre>
                  </details>
                </template>
                <div v-else class="json-block" :class="`event-${eventKind(segment.block)}`">
                  <button
                    type="button"
                    class="json-block-header"
                    :aria-expanded="!isBlockCollapsed(message.id, segment.id, segment.block)"
                    :aria-controls="`event-${message.id}-${segment.id}`"
                    @click="toggleBlock(message.id, segment.id, segment.block)"
                  >
                    <span class="header-text"
                      >{{ eventTitle(segment.block)
                      }}<span v-if="eventKind(segment.block) === 'error'"> · 需要处理</span></span
                    >
                    <t-icon
                      :name="
                        isBlockCollapsed(message.id, segment.id, segment.block)
                          ? 'chevron-down'
                          : 'chevron-up'
                      "
                    />
                  </button>
                  <div
                    v-if="!isBlockCollapsed(message.id, segment.id, segment.block)"
                    :id="`event-${message.id}-${segment.id}`"
                    class="json-block-content"
                  >
                    <MarkdownRenderer
                      v-if="readableEvent(segment.block)"
                      :content="readableEvent(segment.block)"
                    />
                    <p v-else>操作记录已保存。</p>
                  </div>
                  <div v-if="eventKind(segment.block) !== 'normal'" class="event-actions">
                    <button
                      v-if="
                        eventKind(segment.block) === 'confirm' &&
                        index === messages.length - 1 &&
                        workStatus === 'waiting' &&
                        !message.isStreaming &&
                        !confirmBusy
                      "
                      type="button"
                      @click="$emit('confirm-plan')"
                    >
                      继续研究
                    </button>
                    <button type="button" @click="$emit('open-progress')">查看运行状态</button>
                  </div>
                </div>
              </template>
            </template>
          </template>
          <p v-if="message.metadata?.status === 'interrupted'" class="event-error">
            本次回复已中断，已保存的内容保留。
          </p>
        </div>
      </article>
      <slot />
    </div>
  </div>
</template>

<script setup lang="ts">
import { ref } from 'vue'
import ConversationExecution from './ConversationExecution.vue'
import { timelineGroups, type ConversationSegment } from '@/utils/conversationTimeline'
import type { ResearchSnapshot } from '@/types/research'
import MarkdownRenderer from '@/components/MarkdownRenderer.vue'
type PipelineBlock = { type: string; content: unknown }

// 定义消息类型
interface ChatMessage {
  id: string
  role: 'user' | 'assistant' | 'error' | 'model-change' | 'system'
  content: string
  datetime: string
  avatar: string
  isStreaming?: boolean
  json_blocks?: any[]
  message_type?: string
  segments?: ConversationSegment[]
  metadata?: { status?: string }
}

// Props
interface Props {
  messages: ChatMessage[]
  workId?: string
  confirmBusy?: boolean
  workStatus?: string
  tasks?: ResearchSnapshot['experiments']
  snapshot?: ResearchSnapshot | null
}

defineProps<Props>()
defineEmits<{
  'open-file': [path: string]
  'open-progress': []
  'confirm-plan': []
  'open-result': [id: string]
}>()
const isResearchSource = (path: string) =>
  ['research:feedback', 'research:consistency'].includes(path)
const researchSourceLabel = (path: string, files: string[]) => {
  if (path === 'research:feedback') return '校验问题'
  if (path === 'research:consistency') return '结论审查'
  if (path === '.system/validation.json') return '校验问题'
  if (path === '.system/consistency.json') return '结论审查'
  if (path.startsWith('runs/'))
    return '执行输出 ' + (files.filter((p) => p.startsWith('runs/')).indexOf(path) + 1)
  return path.split('/').pop()
}
const sourceFiles = (block: PipelineBlock): string[] =>
  (block.content as { files?: string[] })?.files || []
const sourceResults = (block: PipelineBlock): string[] =>
  (block.content as { result_ids?: string[] })?.result_ids || []
const eventLabels: Record<string, string> = {
  paper_confirm: '需要补充信息',
  paper_analysis: '研究分析',
  paper_stage: '执行记录',
  paper_phase: '阶段记录',
  main: '研究说明',
  main_agent_complete: '研究结果',
  main_agent_error: '研究遇到问题',
  call_code_agent: '准备分析代码',
  code_agent: '编写分析代码',
  call_exec_py: '执行分析代码',
  exec_py: '代码输出',
  call_writing_agent: '准备撰写论文',
  writing_agent: '撰写论文',
  writemd_result: '论文更新',
  tree_result: '工作文件',
  word_tool_call: '编辑 Word 文档',
  word_tool_result: '文档更新',
  word_tool_error: '文档编辑失败',
  generate_figure: '生成图表',
  confirm: '需要补充信息',
}
const eventKind = (block: PipelineBlock): 'normal' | 'error' | 'confirm' => {
  const content =
    block.content && typeof block.content === 'object'
      ? (block.content as Record<string, unknown>)
      : null
  if (
    /error|failed/.test(block.type) ||
    content?.ok === false ||
    content?.success === false ||
    ['failed', 'error', 'blocked'].includes(String(content?.status)) ||
    content?.error
  )
    return 'error'
  if (
    ['confirm', 'paper_confirm'].includes(block.type) ||
    ['waiting', 'needs_confirmation'].includes(String(content?.status))
  )
    return 'confirm'
  return 'normal'
}
const readableEvent = (block: PipelineBlock): string => {
  if (typeof block.content === 'string') return block.content
  if (block.content && typeof block.content === 'object') {
    const c = block.content as Record<string, unknown>
    return ['message', 'summary', 'reason', 'error', 'note']
      .map((k) => c[k])
      .filter((v) => typeof v === 'string')
      .join('\n\n')
  }
  return ''
}
// Older compiler replies include a source excerpt after the error summary.
// Keep the full diagnostic available without presenting source code as prose.
const completionText = (block: PipelineBlock) => {
  const text = readableEvent(block)
  const split = text.indexOf('\n\n')
  return text.startsWith('论文源码') && text.includes('PDF 编译未完成') && split !== -1
    ? { summary: text.slice(0, split), diagnostic: text.slice(split + 2) }
    : { summary: text, diagnostic: '' }
}
const eventTitle = (block: PipelineBlock) => eventLabels[block.type] || '执行记录'
// Keep user choices keyed to message and event position while streaming appends content.
const collapsedBlocks = ref<Map<string, boolean>>(new Map())
const isBlockCollapsed = (messageId: string, blockIndex: string, block: PipelineBlock) =>
  collapsedBlocks.value.get(`${messageId}-${blockIndex}`) ?? eventKind(block) === 'normal'
const toggleBlock = (messageId: string, blockIndex: string, block: PipelineBlock) =>
  collapsedBlocks.value.set(
    `${messageId}-${blockIndex}`,
    !isBlockCollapsed(messageId, blockIndex, block),
  )
// 格式化时间为人类可读格式
const formatDateTime = (datetime: string) => {
  if (!datetime) return ''

  try {
    const date = new Date(datetime)

    // 检查是否是今天
    const today = new Date()
    const isToday =
      date.getDate() === today.getDate() &&
      date.getMonth() === today.getMonth() &&
      date.getFullYear() === today.getFullYear()

    if (isToday) {
      // 如果是今天，只显示时间
      return `今天 ${date.getHours().toString().padStart(2, '0')}:${date.getMinutes().toString().padStart(2, '0')}`
    } else {
      // 如果是昨天
      const yesterday = new Date(today)
      yesterday.setDate(yesterday.getDate() - 1)
      const isYesterday =
        date.getDate() === yesterday.getDate() &&
        date.getMonth() === yesterday.getMonth() &&
        date.getFullYear() === yesterday.getFullYear()

      if (isYesterday) {
        return `昨天 ${date.getHours().toString().padStart(2, '0')}:${date.getMinutes().toString().padStart(2, '0')}`
      } else {
        // 其他日期显示完整日期和时间
        return `${date.getFullYear()}-${(date.getMonth() + 1).toString().padStart(2, '0')}-${date.getDate().toString().padStart(2, '0')} ${date.getHours().toString().padStart(2, '0')}:${date.getMinutes().toString().padStart(2, '0')}`
      }
    }
  } catch (e) {
    // 如果解析失败，返回原始字符串
    return datetime
  }
}
</script>

<style scoped>
.json-chat-renderer {
  height: 100%;
  display: flex;
  flex-direction: column;
}

.chat-messages {
  flex: 1;
  overflow-y: auto;
  overflow-x: hidden;
  padding: 16px;
}

.chat-message-wrapper {
  min-width: 0;
  margin-bottom: 28px;
}
.message-time {
  display: block;
  margin-bottom: 8px;
  color: var(--pa-muted);
  font-size: 11px;
}
.chat-markdown {
  width: 100%;
  min-width: 0;
  line-height: 1.75;
}
.message-user {
  display: flex;
  flex-direction: column;
  align-items: flex-end;
}
.message-user > .chat-markdown {
  width: fit-content;
  max-width: 85%;
  box-sizing: border-box;
  padding: 12px 16px;
  border-radius: var(--pa-radius-message);
  background: var(--pa-message-user-bg);
  color: var(--pa-message-user-ink);
}
.chat-markdown > :deep(.markdown-content) {
  padding: 0;
  background: transparent;
  color: inherit;
  overflow-wrap: anywhere;
}
.chat-markdown > :deep(.markdown-content > :first-child) {
  margin-top: 0;
}
.chat-markdown > :deep(.markdown-content > :last-child) {
  margin-bottom: 0;
}
.chat-markdown :deep(.katex-display) {
  margin: 12px 0;
  overflow-x: auto;
}

.completion-diagnostic {
  margin: 10px 0;
  color: var(--pa-muted);
  font-size: 13px;
}
.completion-diagnostic summary {
  cursor: pointer;
}

/* JSON块样式 */
:deep(.json-block) {
  width: 100%;
  box-sizing: border-box;
  margin: 8px 0;
  overflow: hidden;
}

:deep(.json-block-header) {
  width: 100%;
  border: 0;
  font-family: inherit;
  text-align: left;
  background: transparent;
  padding: 5px 0;
  font-weight: 600;
  font-size: 14px;
  color: var(--pa-muted);
  cursor: pointer;
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
  transition: background-color 0.2s ease;
}

:deep(.json-block-header:hover) {
  background-color: var(--pa-surface-hover);
}

:deep(.json-block-header.collapsed) {
  border-bottom: none;
}

:deep(.json-block-header .header-text) {
  flex-shrink: 0;
}

:deep(.json-block-header .toggle-icon) {
  flex-shrink: 0;
  transition: transform 0.2s ease;
}

:deep(.json-block-content) {
  padding: 8px 0 8px 14px;
  border-left: 1px solid var(--pa-line);
  font-size: 13px;
  line-height: 1.5;
  white-space: pre-wrap;
  word-break: break-word;
  background-color: var(--pa-surface);
  overflow: visible;
}

/* 确保json-block内的列表数字不被截断 */
:deep(.json-block-content .markdown-content) {
  overflow: visible;
}

:deep(.json-block-content ol),
:deep(.json-block-content ul) {
  padding-left: 2.5em;
  list-style-position: outside;
}

/* 不同type的JSON块样式 */
.event-error {
  border-color: var(--pa-danger);
}
.event-confirm {
  border-color: var(--pa-warning);
}
.event-actions {
  padding: 8px 12px;
  display: flex;
  flex-wrap: wrap;
  gap: 12px;
}
.event-actions button {
  border: 0;
  background: transparent;
  color: var(--pa-accent);
  font: inherit;
  cursor: pointer;
}
.json-block-header:focus-visible,
.event-actions button:focus-visible {
  outline: 2px solid var(--pa-accent);
  outline-offset: -2px;
}
/* 代码块样式 */
.chat-markdown {
  line-height: 1.7;
}

:deep(.t-chat__message-content pre) {
  background-color: var(--pa-surface-muted);
  border: 1px solid var(--pa-line);
  border-radius: var(--pa-radius-inline);
  padding: 12px;
  margin: 8px 0;
  overflow-x: auto;
}

:deep(.t-chat__message-content code) {
  background-color: var(--pa-surface-muted);
  padding: 2px 4px;
  border-radius: var(--pa-radius-inline);
  font-family: 'Monaco', 'Menlo', 'Ubuntu Mono', monospace;
  font-size: 0.9em;
}

.answer-sources {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 8px;
  margin-top: 12px;
  color: var(--pa-muted);
  font-size: 12px;
}
.answer-sources button {
  border: 1px solid var(--pa-line);
  border-radius: var(--pa-radius-inline);
  padding: 3px 7px;
  color: var(--td-brand-color);
  background: var(--pa-surface);
  font: inherit;
  cursor: pointer;
}
</style>
