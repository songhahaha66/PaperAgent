<script setup lang="ts">
import { ref } from 'vue'
import { ChatItem, ChatSender } from '@tdesign-vue-next/chat'

// 定义消息类型
interface ChatMessage {
  id: string
  role: 'user' | 'assistant' | 'error' | 'model-change' | 'system'
  content: string
  datetime: string
  avatar: string
}

// 聊天消息数据
const messages = ref<ChatMessage[]>([
  {
    id: '1',
    role: 'user',
    content: '你好！请介绍一下TD Chat组件',
    datetime: new Date().toLocaleString(),
    avatar: 'https://tdesign.gtimg.com/site/avatar.jpg',
  },
  {
    id: '2',
    role: 'assistant',
    content:
      'TD Chat 是 TDesign Vue Next 提供的专业聊天组件，专为 AI 对话场景设计。它提供了丰富的功能，包括消息展示、输入框、打字机效果、头像显示等。',
    datetime: new Date().toLocaleString(),
    avatar: 'https://tdesign.gtimg.com/site/avatar.jpg',
  },
])

// 输入框内容
const inputValue = ref('')

// 发送消息
const sendMessage = () => {
  if (!inputValue.value.trim()) return

  const newMessage: ChatMessage = {
    id: Date.now().toString(),
    role: 'user',
    content: inputValue.value,
    datetime: new Date().toLocaleString(),
    avatar: 'https://tdesign.gtimg.com/site/avatar.jpg',
  }

  messages.value.push(newMessage)

  // 模拟AI回复
  setTimeout(() => {
    const aiReply: ChatMessage = {
      id: (Date.now() + 1).toString(),
      role: 'assistant',
      content: `我收到了你的消息："${inputValue.value}"。这是一个使用TD Chat组件构建的专业聊天界面。TD Chat提供了完整的聊天功能，包括消息管理、样式定制、响应式设计等。`,
      datetime: new Date().toLocaleString(),
      avatar: 'https://tdesign.gtimg.com/site/avatar.jpg',
    }
    messages.value.push(aiReply)
  }, 1000)

  inputValue.value = ''
}

// 清空聊天记录
const clearChat = () => {
  messages.value = []
}

// 复制消息
const copyMessage = (content: string) => {
  navigator.clipboard.writeText(content)
  alert('消息已复制到剪贴板！')
}

// 重新生成消息
const regenerateMessage = (messageId: string) => {
  const message = messages.value.find((m) => m.id === messageId)
  if (message && message.role === 'assistant') {
    message.content = '正在重新生成回复...'
    setTimeout(() => {
      message.content =
        '这是重新生成的回复内容。TD Chat组件支持多种操作，包括复制、重新生成、点赞等。'
    }, 1000)
  }
}
</script>

<template>
  <div class="td-chat-example">
    <t-card title="TD Chat 专业聊天组件示例" class="chat-card">
      <!-- 控制面板 -->
      <div class="control-panel">
        <t-space>
          <t-button theme="danger" @click="clearChat">清空聊天</t-button>
        </t-space>
      </div>

      <!-- TD Chat 组件 -->
      <div class="chat-container">
        <!-- 聊天消息列表 -->
        <div class="chat-messages">
          <ChatItem
            v-for="message in messages"
            :key="message.id"
            :role="message.role"
            :content="message.content"
            :datetime="message.datetime"
            :avatar="message.avatar"
            :actions="message.role === 'assistant' ? 'copy,replay' : undefined"
            @operation="
              (action) => {
                if (action === 'copy') copyMessage(message.content)
                if (action === 'replay') regenerateMessage(message.id)
              }
            "
          />
        </div>

        <!-- 聊天输入区域 -->
        <div class="chat-input">
          <ChatSender v-model="inputValue" placeholder="请输入您的问题..." @send="sendMessage" />
        </div>
      </div>

      <!-- 功能说明 -->
      <div class="feature-description">
        <h4>TD Chat 主要特性：</h4>
        <ul>
          <li>支持用户和助手两种消息类型</li>
          <li>显示头像和时间戳</li>
          <li>支持消息操作（复制、重新生成、点赞等）</li>
          <li>专业的输入框组件</li>
          <li>可自定义样式和布局</li>
          <li>响应式设计，支持移动端</li>
        </ul>
      </div>
    </t-card>
  </div>
</template>

<style scoped>
.td-chat-example {
  padding: 20px;
}

.chat-card {
  max-width: 800px;
  margin: 0 auto;
}

.control-panel {
  margin-bottom: 20px;
  padding: 16px;
  background: #f5f5f5;
  border-radius: 8px;
}

.chat-container {
  margin: 20px 0;
  border: 1px solid #e7e7e7;
  border-radius: 8px;
  overflow: hidden;
}

.chat-messages {
  padding: 16px;
  background: #fafafa;
  max-height: 400px;
  overflow-y: auto;
}

.chat-input {
  padding: 16px;
  border-top: 1px solid #e7e7e7;
  background: white;
}

.feature-description {
  margin-top: 20px;
  padding: 16px;
  background: #f8f9fa;
  border-radius: 8px;
}

.feature-description h4 {
  margin: 0 0 12px 0;
  color: #0052d9;
}

.feature-description ul {
  margin: 0;
  padding-left: 20px;
}

.feature-description li {
  margin-bottom: 8px;
  line-height: 1.5;
}
</style>
