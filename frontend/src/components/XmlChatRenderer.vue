<template>
  <div class="xml-chat-renderer">
    <div class="chat-messages" ref="chatMessages">
      <div
        v-for="(message, index) in parsedMessages"
        :key="index"
        :class="['message', message.type]"
      >
        <!-- 系统消息 -->
        <div v-if="message.type === 'system'" class="message-content system">
          <div class="message-icon">🤖</div>
          <div class="message-text">
            <div class="message-header">系统</div>
            <div class="message-body" v-html="renderXmlContent(message.content)"></div>
          </div>
        </div>

        <!-- 用户消息 -->
        <div v-else-if="message.type === 'user'" class="message-content user">
          <div class="message-text">
            <div class="message-header">用户</div>
            <div class="message-body">{{ message.content }}</div>
          </div>
          <div class="message-icon">👤</div>
        </div>

        <!-- AI消息 -->
        <div v-else-if="message.type === 'ai'" class="message-content ai">
          <div class="message-icon">🤖</div>
          <div class="message-text">
            <div class="message-header">AI助手</div>
            <div class="message-body" v-html="renderXmlContent(message.content)"></div>
          </div>
        </div>

        <!-- 工具调用消息 -->
        <div v-else-if="message.type === 'tool'" class="message-content tool">
          <div class="message-icon">🔧</div>
          <div class="message-text">
            <div class="message-header">工具调用</div>
            <div class="message-body" v-html="renderXmlContent(message.content)"></div>
          </div>
        </div>

        <!-- 工具结果消息 -->
        <div v-else-if="message.type === 'tool-result'" class="message-content tool-result">
          <div class="message-icon">📊</div>
          <div class="message-text">
            <div class="message-header">执行结果</div>
            <div class="message-body" v-html="renderXmlContent(message.content)"></div>
          </div>
        </div>
      </div>

      <!-- 加载指示器 -->
      <div v-if="isLoading" class="message loading">
        <div class="message-content ai">
          <div class="message-icon">🤖</div>
          <div class="message-text">
            <div class="message-header">AI助手</div>
            <div class="message-body">
              <div class="typing-indicator">
                <span></span>
                <span></span>
                <span></span>
              </div>
            </div>
          </div>
        </div>
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { ref, computed, watch, nextTick } from 'vue'

interface ChatMessage {
  type: 'system' | 'user' | 'ai' | 'tool' | 'tool-result'
  content: string
  timestamp?: Date
}

interface Props {
  messages: ChatMessage[]
  isLoading?: boolean
}

const props = withDefaults(defineProps<Props>(), {
  isLoading: false
})

const chatMessages = ref<HTMLElement>()

// 解析消息，将XML内容转换为结构化消息
const parsedMessages = computed(() => {
  return props.messages.map(msg => {
    // 如果消息内容包含XML标签，进行特殊处理
    if (msg.content.includes('<') && msg.content.includes('>')) {
      return {
        ...msg,
        content: msg.content
      }
    }
    return msg
  })
})

// 渲染XML内容
const renderXmlContent = (content: string): string => {
  if (!content) return ''
  
  // 转义HTML字符
  let escaped = content
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&#39;')
  
  // 处理XML标签
  escaped = escaped
    // 主Agent标签
    .replace(/&lt;main_agent&gt;/g, '<div class="xml-tag main-agent"><span class="tag-name">主Agent</span>')
    .replace(/&lt;\/main_agent&gt;/g, '</div>')
    
    // CodeAgent标签
    .replace(/&lt;call_code_agent&gt;/g, '<div class="xml-tag code-agent-call"><span class="tag-name">调用代码助手</span>')
    .replace(/&lt;\/call_code_agent&gt;/g, '</div>')
    .replace(/&lt;ret_code_agent&gt;/g, '<div class="xml-tag code-agent-result"><span class="tag-name">代码助手结果</span>')
    .replace(/&lt;\/ret_code_agent&gt;/g, '</div>')
    
    // 代码执行标签
    .replace(/&lt;call_exec&gt;/g, '<div class="xml-tag code-exec-call"><span class="tag-name">执行代码</span><pre class="code-block">')
    .replace(/&lt;\/call_exec&gt;/g, '</pre></div>')
    .replace(/&lt;ret_exec&gt;/g, '<div class="xml-tag code-exec-result"><span class="tag-name">执行结果</span><pre class="result-block">')
    .replace(/&lt;\/ret_exec&gt;/g, '</pre></div>')
    
    // 文件操作标签
    .replace(/&lt;writemd_result&gt;/g, '<div class="xml-tag file-result"><span class="tag-name">文件写入结果</span>')
    .replace(/&lt;\/writemd_result&gt;/g, '</div>')
    .replace(/&lt;tree_result&gt;/g, '<div class="xml-tag tree-result"><span class="tag-name">目录结构</span><pre class="tree-block">')
    .replace(/&lt;\/tree_result&gt;/g, '</pre></div>')
    
    // 换行符
    .replace(/\n/g, '<br>')
  
  return escaped
}

// 监听消息变化，自动滚动到底部
watch(() => props.messages.length, async () => {
  await nextTick()
  if (chatMessages.value) {
    chatMessages.value.scrollTop = chatMessages.value.scrollHeight
  }
})

// 监听加载状态变化
watch(() => props.isLoading, async (newVal) => {
  if (newVal) {
    await nextTick()
    if (chatMessages.value) {
      chatMessages.value.scrollTop = chatMessages.value.scrollHeight
    }
  }
})
</script>

<style scoped>
.xml-chat-renderer {
  height: 100%;
  display: flex;
  flex-direction: column;
}

.chat-messages {
  flex: 1;
  overflow-y: auto;
  padding: 20px;
  display: flex;
  flex-direction: column;
  gap: 16px;
}

.message {
  display: flex;
  margin-bottom: 16px;
}

.message-content {
  display: flex;
  align-items: flex-start;
  gap: 12px;
  max-width: 80%;
  padding: 12px 16px;
  border-radius: 12px;
  box-shadow: 0 2px 8px rgba(0, 0, 0, 0.1);
}

.message-content.user {
  margin-left: auto;
  background: #007bff;
  color: white;
  flex-direction: row-reverse;
}

.message-content.ai {
  background: #f8f9fa;
  color: #333;
  border: 1px solid #e9ecef;
}

.message-content.system {
  background: #e3f2fd;
  color: #1976d2;
  border: 1px solid #bbdefb;
}

.message-content.tool {
  background: #fff3e0;
  color: #f57c00;
  border: 1px solid #ffcc02;
}

.message-content.tool-result {
  background: #e8f5e8;
  color: #2e7d32;
  border: 1px solid #c8e6c9;
}

.message-icon {
  font-size: 24px;
  flex-shrink: 0;
}

.message-text {
  flex: 1;
  min-width: 0;
}

.message-header {
  font-weight: 600;
  font-size: 14px;
  margin-bottom: 4px;
  opacity: 0.8;
}

.message-body {
  font-size: 14px;
  line-height: 1.5;
  word-wrap: break-word;
}

/* XML标签样式 */
.xml-tag {
  margin: 8px 0;
  padding: 12px;
  border-radius: 8px;
  border-left: 4px solid;
}

.xml-tag.main-agent {
  background: #e3f2fd;
  border-left-color: #1976d2;
}

.xml-tag.code-agent-call {
  background: #fff3e0;
  border-left-color: #f57c00;
}

.xml-tag.code-agent-result {
  background: #e8f5e8;
  border-left-color: #2e7d32;
}

.xml-tag.code-exec-call {
  background: #fce4ec;
  border-left-color: #c2185b;
}

.xml-tag.code-exec-result {
  background: #f1f8e9;
  border-left-color: #689f38;
}

.xml-tag.file-result {
  background: #e0f2f1;
  border-left-color: #00695c;
}

.xml-tag.tree-result {
  background: #fafafa;
  border-left-color: #424242;
}

.tag-name {
  display: inline-block;
  font-weight: 600;
  font-size: 12px;
  text-transform: uppercase;
  color: #666;
  margin-bottom: 8px;
  padding: 2px 8px;
  background: rgba(255, 255, 255, 0.7);
  border-radius: 4px;
}

.code-block {
  background: #2d3748;
  color: #e2e8f0;
  padding: 12px;
  border-radius: 6px;
  font-family: 'Courier New', monospace;
  font-size: 13px;
  line-height: 1.4;
  overflow-x: auto;
  margin: 8px 0;
}

.result-block {
  background: #f7fafc;
  color: #2d3748;
  padding: 12px;
  border-radius: 6px;
  font-family: 'Courier New', monospace;
  font-size: 13px;
  line-height: 1.4;
  border: 1px solid #e2e8f0;
  margin: 8px 0;
}

.tree-block {
  background: #f8f9fa;
  color: #495057;
  padding: 12px;
  border-radius: 6px;
  font-family: 'Courier New', monospace;
  font-size: 12px;
  line-height: 1.4;
  border: 1px solid #dee2e6;
  margin: 8px 0;
}

/* 打字指示器 */
.typing-indicator {
  display: flex;
  gap: 4px;
  align-items: center;
}

.typing-indicator span {
  width: 8px;
  height: 8px;
  border-radius: 50%;
  background: #007bff;
  animation: typing 1.4s infinite ease-in-out;
}

.typing-indicator span:nth-child(1) {
  animation-delay: -0.32s;
}

.typing-indicator span:nth-child(2) {
  animation-delay: -0.16s;
}

@keyframes typing {
  0%, 80%, 100% {
    transform: scale(0.8);
    opacity: 0.5;
  }
  40% {
    transform: scale(1);
    opacity: 1;
  }
}

/* 响应式设计 */
@media (max-width: 768px) {
  .chat-messages {
    padding: 12px;
  }
  
  .message-content {
    max-width: 90%;
    padding: 10px 12px;
  }
  
  .message-icon {
    font-size: 20px;
  }
  
  .message-header {
    font-size: 13px;
  }
  
  .message-body {
    font-size: 13px;
  }
}
</style>
