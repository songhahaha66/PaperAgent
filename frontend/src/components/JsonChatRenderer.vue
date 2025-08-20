<template>
  <div class="json-chat-renderer">
    <div class="chat-messages">
      <div 
        v-for="(message, index) in messages" 
        :key="message.id" 
        :class="[
          'chat-message-wrapper',
          { 'message-dimmed': hoveredDivider !== null && index > hoveredDivider }
        ]"
      >
        <ChatItem
          :role="message.role"
          :content="renderMessageContent(message)"
          :datetime="message.datetime"
          :avatar="getSystemAvatar(message)"
          :actions="message.role === 'assistant' ? 'copy' : undefined"
          @operation="(action) => {
            if (action === 'copy') copyMessage(message.content)
          }"
        />
        <div v-if="message.systemType" :class="['system-label', message.systemType]">
          {{ getSystemName(message) }}
        </div>
        
        <!-- 对话分割线 -->
        <div 
          v-if="index < messages.length - 1" 
          class="message-divider"
        >
          <div class="divider-line"></div>
          <div 
            class="divider-icon" 
            :class="{ 'show': hoveredDivider === index }"
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
import { ref, computed } from 'vue';
import { ChatItem } from '@tdesign-vue-next/chat';
import { MessagePlugin } from 'tdesign-vue-next';

// 定义消息类型
interface ChatMessage {
  id: string;
  role: 'user' | 'assistant' | 'error' | 'model-change' | 'system';
  content: string;
  datetime: string;
  avatar: string;
  systemType?: 'brain' | 'code' | 'writing';
  isStreaming?: boolean;
}

// Props
interface Props {
  messages: ChatMessage[];
}

const props = defineProps<Props>();

// 分割线悬停状态
const hoveredDivider = ref<number | null>(null);

// 显示分割线
const showDivider = (index: number) => {
  hoveredDivider.value = index;
};

// 隐藏分割线
const hideDivider = () => {
  hoveredDivider.value = null;
};

// 获取系统头像
const getSystemAvatar = (message: ChatMessage) => {
  if (message.systemType) {
    const systemAvatars = {
      brain: 'https://api.dicebear.com/7.x/bottts/svg?seed=brain&backgroundColor=0052d9',   // 中枢系统头像 - 蓝色机器人
      code: 'https://api.dicebear.com/7.x/bottts/svg?seed=code&backgroundColor=00a870',    // 代码执行系统头像 - 绿色机器人
      writing: 'https://api.dicebear.com/7.x/bottts/svg?seed=writing&backgroundColor=ed7b2f' // 论文生成系统头像 - 橙色机器人
    };
    return systemAvatars[message.systemType];
  }
  // 如果没有系统类型，返回默认头像
  return 'https://tdesign.gtimg.com/site/avatar.jpg';
};

// 获取系统名称
const getSystemName = (message: ChatMessage) => {
  if (message.systemType) {
    const systemNames = {
      brain: '中枢系统',
      code: '代码执行',
      writing: '论文生成'
    };
    return systemNames[message.systemType];
  }
  return 'AI助手';
};

// 渲染消息内容
const renderMessageContent = (message: ChatMessage) => {
  if (message.role === 'user') {
    return message.content;
  }
  
  // 对于AI消息，支持Markdown渲染
  if (message.role === 'assistant') {
    return message.content;
  }
  
  return message.content;
};

// 复制消息
const copyMessage = (content: string) => {
  navigator.clipboard.writeText(content).then(() => {
    MessagePlugin.success('消息已复制到剪贴板');
  }).catch(() => {
    MessagePlugin.error('复制失败');
  });
};
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
  padding: 16px;
}

.chat-message-wrapper {
  margin-bottom: 16px;
  transition: opacity 0.3s ease;
}

.message-dimmed {
  opacity: 0.3;
}

.system-label {
  display: inline-block;
  padding: 2px 8px;
  border-radius: 12px;
  font-size: 12px;
  font-weight: 500;
  margin-top: 4px;
  margin-left: 48px;
}

.system-label.brain {
  background-color: #e6f3ff;
  color: #0052d9;
}

.system-label.code {
  background-color: #e6fff2;
  color: #00a870;
}

.system-label.writing {
  background-color: #fff2e6;
  color: #ed7b2f;
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

/* 代码块样式 */
:deep(.t-chat__message-content) {
  line-height: 1.6;
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
  
  .system-label {
    margin-left: 32px;
    font-size: 11px;
  }
}
</style>
