<template>
  <div class="json-chat-renderer">
    <div class="chat-messages">
      <div v-for="(message, index) in messages" :key="message.id" class="chat-message-wrapper">
        <ChatItem
          :role="message.role"
          :datetime="formatDateTime(message.datetime)"
          :avatar="getSystemAvatar(message)"
        >
          <template #content>
            <div class="chat-markdown">
              <!-- 文本内容（作为 Markdown 渲染，支持 KaTeX） -->
              <template v-if="technicalReceipt(message)"
                ><p>这次操作未完成，已保存当时的研究成果与执行位置。</p>
                <details class="technical-receipt">
                  <summary>查看当时的技术记录</summary>
                  <MarkdownRenderer :content="getTextContent(message)" /></details
              ></template>
              <MarkdownRenderer
                v-else-if="getTextContent(message)"
                :content="getTextContent(message)"
              />

              <!-- 保留每次研究运行的过程记录 -->
              <PaperPipelineCard
                v-if="isPipelineMessage(getJsonBlocks(message))"
                :blocks="getJsonBlocks(message)"
                :tasks="index === messages.length - 1 ? tasks : undefined"
                :streaming="message.isStreaming"
                :superseded="index < messages.length - 1"
                :can-confirm="
                  index === messages.length - 1 &&
                  workStatus === 'waiting' &&
                  !message.isStreaming &&
                  !confirmBusy
                "
                @confirm-plan="$emit('confirm-plan')"
                @open-progress="$emit('open-progress')"
              />

              <div
                v-for="context in getResearchContexts(message)"
                :key="context.research_id || 'context'"
                class="answer-sources"
              >
                <span>参考材料</span>
                <button
                  v-for="path in context.files"
                  :key="path"
                  :title="path"
                  @click="$emit('open-file', path)"
                >
                  {{ researchSourceLabel(path, context.files) }}
                </button>
                <button
                  v-for="id in context.result_ids || []"
                  :key="id"
                  @click="$emit('open-result', id)"
                >
                  {{ snapshot?.results.find((r) => r.id === id)?.name || '查看实测结果' }}
                </button>
              </div>

              <!-- JSON 块内容（内部也用 Markdown 渲染） -->
              <div
                v-for="{ block, index: bIndex } in getEventBlocks(message)"
                :key="bIndex"
                class="json-block"
                :class="`event-${eventKind(block)}`"
              >
                <button
                  type="button"
                  class="json-block-header"
                  :aria-expanded="!isBlockCollapsed(message.id, bIndex, block)"
                  :aria-controls="`event-${message.id}-${bIndex}`"
                  @click="toggleBlock(message.id, bIndex, block)"
                >
                  <span class="header-text"
                    >{{ eventTitle(block)
                    }}<span v-if="eventKind(block) === 'error'"> · 需要处理</span></span
                  >
                  <span class="content-preview">{{ eventSummary(block) }}</span>
                  <t-icon
                    :name="
                      isBlockCollapsed(message.id, bIndex, block) ? 'chevron-down' : 'chevron-up'
                    "
                    class="toggle-icon"
                  />
                </button>
                <div
                  :id="`event-${message.id}-${bIndex}`"
                  class="json-block-content"
                  v-if="!isBlockCollapsed(message.id, bIndex, block)"
                >
                  <MarkdownRenderer v-if="readableEvent(block)" :content="readableEvent(block)" />
                  <p v-else>操作记录已保存。可在运行状态中查看相关步骤和产物。</p>
                </div>
                <div v-if="eventKind(block) !== 'normal'" class="event-actions">
                  <button
                    v-if="
                      eventKind(block) === 'confirm' &&
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
            </div>
          </template>
        </ChatItem>
      </div>
      <ResearchRuntime
        v-if="snapshot?.has_research"
        :snapshot="snapshot"
        :work-status="workStatus"
        compact
        @progress="$emit('open-progress')"
        @file="(path) => $emit('open-file', path)"
      />
      <slot />
    </div>
  </div>
</template>

<script setup lang="ts">
import { ref } from 'vue'
import ResearchRuntime from './ResearchRuntime.vue'
import type { ResearchSnapshot } from '@/types/research'
import { ChatItem } from '@tdesign-vue-next/chat'
import MarkdownRenderer from '@/components/MarkdownRenderer.vue'
import PaperPipelineCard from '@/components/PaperPipelineCard.vue'
import { isPipelineMessage, isPaperEvent, asText, type PipelineBlock } from '@/utils/paperPipeline'

// 定义消息类型
interface ChatMessage {
  id: string
  role: 'user' | 'assistant' | 'error' | 'model-change' | 'system'
  content: string
  datetime: string
  avatar: string
  isStreaming?: boolean
  json_blocks?: any[]
  message_type?: 'text' | 'json_card'
}

// Props
interface Props {
  messages: ChatMessage[]
  confirmBusy?: boolean
  workStatus?: string
  tasks?: ResearchSnapshot['tasks']
  snapshot?: ResearchSnapshot | null
}

const props = defineProps<Props>()
const technicalReceipt = (message: ChatMessage) =>
  message.role !== 'user' &&
  /EvidenceDecision|ResearchIntent|结构化输出.*失败/.test(getTextContent(message))
defineEmits<{
  'open-file': [path: string]
  'open-progress': []
  'confirm-plan': []
  'open-result': [id: string]
}>()
const researchSourceLabel = (path: string, files: string[]) => {
  if (path === '.system/validation.json') return '校验问题'
  if (path === '.system/consistency.json') return '结论审查'
  if (path.startsWith('runs/'))
    return '执行输出 ' + (files.filter((p) => p.startsWith('runs/')).indexOf(path) + 1)
  return path.split('/').pop()
}
const getResearchContexts = (message: ChatMessage) =>
  (message.json_blocks || []).filter((b) => b.type === 'research_context').map((b) => b.content)
const eventLabels: Record<string, [string, string]> = {
  main: ['研究说明', '本次研究的说明。'],
  main_agent_start: ['开始研究', '已接收本次研究请求。'],
  main_agent_complete: ['本次操作结果', '本次操作已返回结果。'],
  main_agent_error: ['研究遇到问题', '请查看错误说明和任务进度。'],
  call_code_agent: ['准备分析代码', '本次计算的输入与分析要求。'],
  code_agent: ['编写分析代码', '本次分析代码的生成记录。'],
  call_exec_py: ['执行分析代码', '本次分析代码的执行请求。'],
  exec_py: ['分析代码执行结果', '本次计算返回的输出。'],
  call_writing_agent: ['准备更新论文', '本次写作的要求与材料。'],
  writing_agent: ['更新论文', '本次论文写作的记录。'],
  writemd_result: ['论文更新结果', '本次正文的写入结果。'],
  tree_result: ['查看工作文件', '本次读取的文件列表。'],
  word_tool_call: ['编辑 Word 论文', '本次文档编辑请求。'],
  word_tool_result: ['Word 编辑结果', '本次文档编辑的返回结果。'],
  word_tool_error: ['Word 编辑遇到问题', '请查看错误说明。'],
  generate_figure: ['生成图表', '本次图表生成记录。'],
  confirm: ['研究需要你的输入', '请在输入框补充必要信息或说明研究调整。'],
}
const knownEvent = (block: PipelineBlock) => Object.hasOwn(eventLabels, block.type)
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
    block.type === 'confirm' ||
    ['waiting', 'needs_confirmation'].includes(String(content?.status))
  )
    return 'confirm'
  return 'normal'
}
const readableEvent = (block: PipelineBlock): string => {
  if (typeof block.content === 'string') return block.content
  if (block.content && typeof block.content === 'object') {
    const c = block.content as Record<string, unknown>
    return ['message', 'summary', 'reason', 'error']
      .map((k) => c[k])
      .filter((v) => typeof v === 'string')
      .join('\n\n')
  }
  return ''
}
const eventTitle = (block: PipelineBlock) => eventLabels[block.type]?.[0] || '其他执行记录'
const eventSummary = (block: PipelineBlock) =>
  eventLabels[block.type]?.[1] ||
  (eventKind(block) === 'normal' ? '展开查看操作记录。' : '这条记录需要处理，请查看详情。')
// Keep user choices keyed to message and event position while streaming appends content.
const collapsedBlocks = ref<Map<string, boolean>>(new Map())
const isBlockCollapsed = (messageId: string, blockIndex: number, block: PipelineBlock) =>
  collapsedBlocks.value.get(`${messageId}-${blockIndex}`) ?? eventKind(block) === 'normal'
const toggleBlock = (messageId: string, blockIndex: number, block: PipelineBlock) =>
  collapsedBlocks.value.set(
    `${messageId}-${blockIndex}`,
    !isBlockCollapsed(messageId, blockIndex, block),
  )
const getEventBlocks = (message: ChatMessage) => {
  const blocks = getJsonBlocks(message)
  const pipeline = isPipelineMessage(blocks)
  return blocks
    .map((block, index) => ({ block, index }))
    .filter(
      ({ block }) =>
        !pipeline ||
        (!isPaperEvent(block) &&
          !['main_agent_start', 'main_agent_complete', 'main_agent_error'].includes(block.type)),
    )
}

// 获取系统头像
const getSystemAvatar = (message: ChatMessage) => {
  // 直接使用消息中的头像
  return message.avatar || (message.role === 'user' ? '/avatar-user.svg' : '/logo.png')
}

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

// 解析JSON块
const parseJsonBlocks = (content: string) => {
  const blocks: any[] = []
  const lines = content.split('\n')
  let currentBlock = ''
  let inJsonBlock = false

  for (const line of lines) {
    if (line.trim().startsWith('{') && line.trim().endsWith('}')) {
      // 单行JSON
      try {
        const block = JSON.parse(line.trim())
        blocks.push(block)
      } catch (e) {
        // 不是有效的JSON，作为普通文本处理
        if (!inJsonBlock) {
          currentBlock += line + '\n'
        }
      }
    } else if (line.trim().startsWith('{')) {
      // 多行JSON开始
      inJsonBlock = true
      currentBlock = line + '\n'
    } else if (line.trim().endsWith('}') && inJsonBlock) {
      // 多行JSON结束
      currentBlock += line
      try {
        const block = JSON.parse(currentBlock)
        blocks.push(block)
      } catch (e) {
        // 解析失败，作为普通文本处理
        currentBlock = ''
      }
      inJsonBlock = false
      currentBlock = ''
    } else if (inJsonBlock) {
      // 多行JSON中间
      currentBlock += line + '\n'
    } else {
      // 普通文本
      currentBlock += line + '\n'
    }
  }

  return { blocks, remainingText: currentBlock.trim() }
}

// 提取供渲染的纯文本 Markdown
const getTextContent = (message: ChatMessage) => {
  if (!message) return ''
  // 对 assistant，如果有显式 json_blocks，则文本就是 content；
  // 否则需要从 content 里解析掉 JSON，返回剩余文本。
  if (message.role === 'assistant') {
    if (message.json_blocks && message.json_blocks.length > 0) {
      return message.content || ''
    }
    const { remainingText } = parseJsonBlocks(message.content || '')
    return remainingText || ''
  }
  return message.content || ''
}

// 进度与文件变更事件由右侧面板展示，不进聊天卡片（历史记录里也会保存这些块）
const HIDDEN_BLOCK_TYPES = new Set([
  'plan_updated',
  'file_changed',
  'research_context',
  'research_snapshot',
  'research_state',
])

// 提取 JSON 块数组
const getJsonBlocks = (message: ChatMessage) => {
  if (!message) return [] as any[]
  const blocks =
    message.json_blocks && message.json_blocks.length > 0
      ? message.json_blocks
      : parseJsonBlocks(message.content || '').blocks
  return blocks.filter(
    (block) => block && typeof block === 'object' && !HIDDEN_BLOCK_TYPES.has(block.type),
  )
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
  margin-bottom: 28px;
}
:deep(.t-chat__avatar) {
  width: 28px;
  height: 28px;
}
:deep(.t-chat__datetime) {
  color: var(--pa-muted);
  font-size: 11px;
  margin-bottom: 8px;
}

/* Ensure chat-markdown container uses full width */
.chat-markdown {
  width: 100%;
  display: block;
}

/* 让 Chat 内部的 Markdown 容器与气泡样式协调，避免内外 padding 叠加导致公式错位 */
:deep(.t-chat__message-content) .chat-markdown > .markdown-content {
  padding: 0; /* 由外层气泡控制内边距 */
  background: transparent; /* 去掉 MarkdownRenderer 的背景，避免视觉偏移 */
  overflow: visible; /* 避免嵌套滚动影响定位 */
}

/* KaTeX 对齐与间距（块级） */
:deep(.t-chat__message-content) .katex-display {
  margin: 12px 0; /* 合理上下间距 */
}

/* 行内公式与文本的基线对齐通常由 KaTeX 默认处理，这里确保 line-height 不被异常覆盖 */
:deep(.t-chat__message-content) {
  width: 100%;
  max-width: none;
  line-height: 1.7; /* 略增大，给行内公式留足空间，减少上/下溢出导致的视觉错位 */
}

/* JSON块样式 */
:deep(.json-block) {
  width: 100%;
  box-sizing: border-box;
  border: 1px solid var(--pa-line);
  border-radius: var(--pa-radius-card);
  margin: 8px 0;
  overflow: hidden;
  background-color: var(--pa-surface-muted);
}

:deep(.json-block-header) {
  width: 100%;
  border: 0;
  font-family: inherit;
  text-align: left;
  background-color: var(--pa-surface-hover);
  padding: 8px 12px;
  font-weight: 600;
  font-size: 14px;
  border-bottom: 1px solid var(--pa-line);
  color: var(--pa-ink);
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

:deep(.json-block-header .content-preview) {
  flex: 1;
  font-weight: 400;
  font-size: 13px;
  color: var(--pa-muted);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  margin: 0 8px;
}

:deep(.json-block-header .toggle-icon) {
  flex-shrink: 0;
  transition: transform 0.2s ease;
}

:deep(.json-block-content) {
  padding: 12px 12px 12px 16px;
  font-family: 'Monaco', 'Menlo', 'Ubuntu Mono', monospace;
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
:deep(.t-chat__message-content) {
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

/* 流式消息样式 */
:deep(.t-chat__message--assistant) {
  animation: fadeIn 0.3s ease-in-out;
}

@keyframes fadeIn {
  from {
    opacity: 0;
    transform: translateY(10px);
  }
  to {
    opacity: 1;
    transform: translateY(0);
  }
}

/* 响应式设计 */
@media (max-width: 768px) {
  .chat-messages {
    padding: 8px;
  }

  .divider-icon {
    opacity: 0.45;
  }
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
