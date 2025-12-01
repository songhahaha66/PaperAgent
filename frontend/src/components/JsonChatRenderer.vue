<template>
  <div class="json-chat-renderer">
    <div class="chat-messages">
      <div
        v-for="(message, index) in messages"
        :key="message.id"
        :class="[
          'chat-message-wrapper',
          { 'message-dimmed': hoveredDivider !== null && index > hoveredDivider },
        ]"
      >
        <ChatItem
          :role="message.role"
          :datetime="formatDateTime(message.datetime)"
          :avatar="getSystemAvatar(message)"
        >
          <template #content>
            <div class="chat-markdown">
              <!-- 文本内容（作为 Markdown 渲染，支持 KaTeX） -->
              <MarkdownRenderer v-if="getTextContent(message)" :content="getTextContent(message)" />

              <!-- JSON 块内容（内部也用 Markdown 渲染） -->
              <template v-for="(block, bIndex) in getJsonBlocks(message)" :key="bIndex">
                <div class="json-block" :class="`json-block-${block.type || 'unknown'}`">
                  <div 
                    class="json-block-header" 
                    @click="toggleBlock(message.id, bIndex)"
                    :class="{ 'collapsed': isBlockCollapsed(message.id, bIndex) }"
                  >
                    <span class="header-text">Type: {{ block.type || 'unknown' }}</span>
                    <span class="content-preview" v-if="isBlockCollapsed(message.id, bIndex)">
                      {{ getContentPreview(block.content) }}
                    </span>
                    <t-icon 
                      :name="isBlockCollapsed(message.id, bIndex) ? 'chevron-down' : 'chevron-up'" 
                      class="toggle-icon"
                    />
                  </div>
                  <div 
                    class="json-block-content" 
                    v-show="!isBlockCollapsed(message.id, bIndex)"
                  >
                    <MarkdownRenderer :content="String(block.content || '')" />
                  </div>
                </div>
              </template>
            </div>
          </template>
        </ChatItem>

        <!-- 对话分割线 -->
        <div v-if="index < messages.length - 1" class="message-divider">
          <div class="divider-line"></div>
          <div
            class="divider-icon"
            :class="{ show: hoveredDivider === index }"
            @mouseenter="showDivider(index)"
            @mouseleave="hideDivider"
          >
            <t-icon name="arrow-up" />
          </div>
        </div>
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { ref, computed } from 'vue'
import { ChatItem } from '@tdesign-vue-next/chat'
import { MessagePlugin } from 'tdesign-vue-next'
import MarkdownRenderer from '@/components/MarkdownRenderer.vue'

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
}

const props = defineProps<Props>()

// 分割线悬停状态
const hoveredDivider = ref<number | null>(null)

// JSON块折叠状态 (messageId-blockIndex -> collapsed)
const collapsedBlocks = ref<Map<string, boolean>>(new Map())

// 显示分割线
const showDivider = (index: number) => {
  hoveredDivider.value = index
}

// 隐藏分割线
const hideDivider = () => {
  hoveredDivider.value = null
}

// 切换JSON块的折叠状态
const toggleBlock = (messageId: string, blockIndex: number) => {
  const key = `${messageId}-${blockIndex}`
  const currentState = collapsedBlocks.value.get(key) ?? false // 默认展开
  collapsedBlocks.value.set(key, !currentState)
}

// 检查JSON块是否折叠
const isBlockCollapsed = (messageId: string, blockIndex: number) => {
  const key = `${messageId}-${blockIndex}`
  return collapsedBlocks.value.get(key) ?? false // 默认展开
}

// 获取内容预览（前N个字符）
const getContentPreview = (content: any) => {
  const str = String(content || '')
  const maxLength = 100
  if (str.length <= maxLength) {
    return str
  }
  return str.substring(0, maxLength) + '...'
}

// 获取系统头像
const getSystemAvatar = (message: ChatMessage) => {
  // 直接使用消息中的头像
  return message.avatar || 'https://tdesign.gtimg.com/site/avatar.jpg'
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

// 提取 JSON 块数组
const getJsonBlocks = (message: ChatMessage) => {
  if (!message) return [] as any[]

  // 添加调试日志
  console.log('getJsonBlocks called for message:', {
    id: message.id,
    role: message.role,
    hasJsonBlocks: !!(message.json_blocks && message.json_blocks.length > 0),
    jsonBlocksCount: message.json_blocks?.length || 0,
    messageType: message.message_type,
    contentLength: message.content?.length || 0
  })

  if (message.json_blocks && message.json_blocks.length > 0) {
    console.log('Using explicit json_blocks for message:', message.id, 'blocks:', message.json_blocks)
    return message.json_blocks
  }

  const { blocks } = parseJsonBlocks(message.content || '')
  console.log('Parsed json blocks from content for message:', message.id, 'count:', blocks.length)
  return blocks
}

// 复制消息
const copyMessage = (content: string) => {
  navigator.clipboard
    .writeText(content)
    .then(() => {
      MessagePlugin.success('消息已复制到剪贴板')
    })
    .catch(() => {
      MessagePlugin.error('复制失败')
    })
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
  margin-bottom: 16px;
  transition: opacity 0.3s ease;
}

.message-dimmed {
  opacity: 0.3;
}

.message-divider {
  position: relative;
  height: 20px;
  margin: 8px 0;
  display: flex;
  align-items: center;
  justify-content: center;
}

.divider-line {
  position: absolute;
  top: 50%;
  left: 0;
  right: 0;
  height: 1px;
  background-color: #e7e7e7;
  z-index: 1;
}

.divider-icon {
  position: relative;
  z-index: 2;
  width: 24px;
  height: 24px;
  border-radius: 50%;
  background-color: #f5f5f5;
  border: 1px solid #e7e7e7;
  display: flex;
  align-items: center;
  justify-content: center;
  cursor: pointer;
  opacity: 0;
  transition: all 0.3s ease;
}

.divider-icon.show {
  opacity: 1;
  background-color: #0052d9;
  border-color: #0052d9;
  color: white;
}

.divider-icon:hover {
  background-color: #0052d9;
  border-color: #0052d9;
  color: white;
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
  border: 1px solid #e0e0e0;
  border-radius: 8px;
  margin: 8px 0;
  overflow: hidden;
  background-color: #fafafa;
}

:deep(.json-block-header) {
  background-color: #f0f0f0;
  padding: 8px 12px;
  font-weight: 600;
  font-size: 14px;
  border-bottom: 1px solid #e0e0e0;
  color: #333;
  cursor: pointer;
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
  transition: background-color 0.2s ease;
}

:deep(.json-block-header:hover) {
  background-color: #e8e8e8;
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
  color: #666;
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
  padding: 12px;
  font-family: 'Monaco', 'Menlo', 'Ubuntu Mono', monospace;
  font-size: 13px;
  line-height: 1.5;
  white-space: pre-wrap;
  word-break: break-word;
  background-color: white;
}

/* 不同type的JSON块样式 */
:deep(.json-block-main) {
  border-left: 4px solid #0052d9;
}

:deep(.json-block-main .json-block-header) {
  background-color: #e6f3ff;
  color: #0052d9;
}

:deep(.json-block-call_code_agent) {
  border-left: 4px solid #00a870;
}

:deep(.json-block-call_code_agent .json-block-header) {
  background-color: #e6fff2;
  color: #00a870;
}

:deep(.json-block-code_agent) {
  border-left: 4px solid #00a870;
}

:deep(.json-block-code_agent .json-block-header) {
  background-color: #e6fff2;
  color: #00a870;
}

:deep(.json-block-call_exec_py) {
  border-left: 4px solid #ff6b35;
}

:deep(.json-block-call_exec_py .json-block-header) {
  background-color: #fff2e6;
  color: #ff6b35;
}

:deep(.json-block-exec_py) {
  border-left: 4px solid #ff6b35;
}

:deep(.json-block-exec_py .json-block-header) {
  background-color: #fff2e6;
  color: #ff6b35;
}

:deep(.json-block-call_writing_agent) {
  border-left: 4px solid #ed7b2f;
}

:deep(.json-block-call_writing_agent .json-block-header) {
  background-color: #fff2e6;
  color: #ed7b2f;
}

:deep(.json-block-writing_agent) {
  border-left: 4px solid #ed7b2f;
}

:deep(.json-block-writing_agent .json-block-header) {
  background-color: #fff2e6;
  color: #ed7b2f;
}

:deep(.json-block-writemd_result) {
  border-left: 4px solid #8e44ad;
}

:deep(.json-block-writemd_result .json-block-header) {
  background-color: #f4e6ff;
  color: #8e44ad;
}

:deep(.json-block-tree_result) {
  border-left: 4px solid #27ae60;
}

:deep(.json-block-tree_result .json-block-header) {
  background-color: #e6fff2;
  color: #27ae60;
}

:deep(.json-block-word_tool_call) {
  border-left: 4px solid #2980b9;
}

:deep(.json-block-word_tool_call .json-block-header) {
  background-color: #e8f4f8;
  color: #2980b9;
}

:deep(.json-block-word_tool_result) {
  border-left: 4px solid #2980b9;
}

:deep(.json-block-word_tool_result .json-block-header) {
  background-color: #e8f4f8;
  color: #2980b9;
}

:deep(.json-block-word_tool_error) {
  border-left: 4px solid #e74c3c;
}

:deep(.json-block-word_tool_error .json-block-header) {
  background-color: #fdecea;
  color: #e74c3c;
}

/* 代码块样式 */
:deep(.t-chat__message-content) {
  line-height: 1.7;
}

:deep(.t-chat__message-content pre) {
  background-color: #f8f9fa;
  border: 1px solid #e9ecef;
  border-radius: 6px;
  padding: 12px;
  margin: 8px 0;
  overflow-x: auto;
}

:deep(.t-chat__message-content code) {
  background-color: #f1f3f4;
  padding: 2px 4px;
  border-radius: 3px;
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
}
</style>
