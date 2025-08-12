<script setup lang="ts">
import { ref, reactive } from 'vue'
import TDChatExample from './components/TDChatExample.vue'

// 显示TD Chat示例
const showTDChat = ref(false)

// 聊天消息数据
const messages = ref([
  {
    id: '1',
    type: 'user',
    content: '你好！请介绍一下TDesign Vue Next',
    time: new Date().toLocaleTimeString()
  },
  {
    id: '2',
    type: 'assistant',
    content: 'TDesign Vue Next 是腾讯开源的企业级设计系统，基于 Vue 3 和 TypeScript 构建。它提供了丰富的组件库和设计规范，帮助开发者快速构建美观、一致的用户界面。',
    time: new Date().toLocaleTimeString()
  }
])

// 输入框内容
const inputValue = ref('')

// 发送消息
const sendMessage = () => {
  if (!inputValue.value.trim()) return
  
  const newMessage = {
    id: Date.now().toString(),
    type: 'user' as const,
    content: inputValue.value,
    time: new Date().toLocaleTimeString()
  }
  
  messages.value.push(newMessage)
  
  // 模拟AI回复
  setTimeout(() => {
    const aiReply = {
      id: (Date.now() + 1).toString(),
      type: 'assistant' as const,
      content: `我收到了你的消息："${inputValue.value}"。这是一个使用TDesign Vue Next和TD Chat构建的聊天界面示例。`,
      time: new Date().toLocaleTimeString()
    }
    messages.value.push(aiReply)
  }, 1000)
  
  inputValue.value = ''
}

// 表单数据
const formData = reactive({
  name: '',
  email: '',
  message: ''
})

// 提交表单
const onSubmit = () => {
  console.log('表单提交:', formData)
  alert('表单提交成功！')
}
</script>

<template>
  <div class="app-container">
    <!-- 头部 -->
    <t-header class="header">
      <t-head-menu>
        <template #logo>
          <div class="logo">
            <t-icon name="logo-github" />
            <span>PaperAgent</span>
          </div>
        </template>
        <t-menu-item value="home">首页</t-menu-item>
        <t-menu-item value="chat">聊天</t-menu-item>
        <t-menu-item value="about">关于</t-menu-item>
      </t-head-menu>
    </t-header>

    <!-- 主要内容区域 -->
    <div class="main-content">
      <!-- 左侧面板 -->
      <div class="left-panel">
        <t-card title="TDesign 组件示例" class="component-demo">
          <t-space direction="vertical" size="large">
            <!-- 按钮组件 -->
            <div>
              <h4>按钮组件</h4>
              <t-space>
                <t-button theme="primary">主要按钮</t-button>
                <t-button theme="default">默认按钮</t-button>
                <t-button theme="danger">危险按钮</t-button>
                <t-button theme="warning">警告按钮</t-button>
              </t-space>
            </div>

            <!-- 输入框组件 -->
            <div>
              <h4>输入框组件</h4>
              <t-space direction="vertical">
                <t-input v-model="formData.name" placeholder="请输入姓名" />
                <t-input v-model="formData.email" placeholder="请输入邮箱" />
                <t-textarea v-model="formData.message" placeholder="请输入留言" />
              </t-space>
            </div>

            <!-- 表单提交 -->
            <div>
              <t-button theme="primary" @click="onSubmit">提交表单</t-button>
            </div>

            <!-- TD Chat 示例链接 -->
            <div>
              <h4>TD Chat 组件</h4>
              <t-button 
                theme="primary" 
                variant="outline" 
                @click="showTDChat = !showTDChat"
              >
                {{ showTDChat ? '隐藏' : '显示' }} TD Chat 示例
              </t-button>
            </div>
          </t-space>
        </t-card>
      </div>

      <!-- 右侧聊天区域 -->
      <div class="right-panel">
        <t-card title="自定义聊天界面示例" class="chat-demo">
          <!-- 聊天消息列表 -->
          <div class="chat-messages">
            <div
              v-for="message in messages"
              :key="message.id"
              :class="['message', `message-${message.type}`]"
            >
              <div class="message-content">
                <div class="message-text">{{ message.content }}</div>
                <div class="message-time">{{ message.time }}</div>
              </div>
            </div>
          </div>

          <!-- 聊天输入区域 -->
          <div class="chat-input">
            <t-input
              v-model="inputValue"
              placeholder="请输入消息..."
              @keyup.enter="sendMessage"
            >
              <template #suffix>
                <t-button
                  theme="primary"
                  size="small"
                  @click="sendMessage"
                >
                  发送
                </t-button>
              </template>
            </t-input>
          </div>
        </t-card>
      </div>
    </div>

    <!-- TD Chat 专业示例 -->
    <div v-if="showTDChat" class="td-chat-section">
      <TDChatExample />
    </div>

    <!-- 底部 -->
    <t-footer class="footer">
      <p>&copy; 2024 PaperAgent. 使用 TDesign Vue Next 构建</p>
    </t-footer>
  </div>
</template>

<style scoped>
.app-container {
  min-height: 100vh;
  display: flex;
  flex-direction: column;
}

.header {
  background: #fff;
  border-bottom: 1px solid #e7e7e7;
  box-shadow: 0 2px 4px rgba(0, 0, 0, 0.1);
}

.logo {
  display: flex;
  align-items: center;
  gap: 8px;
  font-size: 18px;
  font-weight: bold;
  color: #0052d9;
}

.main-content {
  flex: 1;
  display: flex;
  gap: 24px;
  padding: 24px;
  background: #f5f5f5;
}

.left-panel {
  flex: 1;
  max-width: 400px;
}

.right-panel {
  flex: 2;
  max-width: 600px;
}

.component-demo,
.chat-demo {
  height: 100%;
}

.chat-demo {
  display: flex;
  flex-direction: column;
}

.chat-messages {
  flex: 1;
  overflow-y: auto;
  padding: 16px;
  background: #fafafa;
  border-radius: 8px;
  margin-bottom: 16px;
  max-height: 400px;
}

.message {
  margin-bottom: 16px;
  display: flex;
}

.message-user {
  justify-content: flex-end;
}

.message-assistant {
  justify-content: flex-start;
}

.message-content {
  max-width: 80%;
  padding: 12px 16px;
  border-radius: 12px;
  position: relative;
}

.message-user .message-content {
  background: #0052d9;
  color: white;
}

.message-assistant .message-content {
  background: white;
  border: 1px solid #e7e7e7;
}

.message-text {
  margin-bottom: 4px;
  line-height: 1.5;
}

.message-time {
  font-size: 12px;
  opacity: 0.7;
}

.chat-input {
  padding: 16px 0;
  border-top: 1px solid #e7e7e7;
}

.td-chat-section {
  padding: 20px;
  background: #fff;
  border-top: 1px solid #e7e7e7;
}

.footer {
  background: #fff;
  border-top: 1px solid #e7e7e7;
  text-align: center;
  padding: 16px;
  color: #666;
}

/* 响应式设计 */
@media (max-width: 768px) {
  .main-content {
    flex-direction: column;
    padding: 16px;
  }
  
  .left-panel,
  .right-panel {
    max-width: none;
  }
}
</style>
