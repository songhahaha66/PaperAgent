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
  json_blocks?: any[];
  message_type?: 'text' | 'json_card';
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

// 解析JSON块
const parseJsonBlocks = (content: string) => {
  const blocks: any[] = [];
  const lines = content.split('\n');
  let currentBlock = '';
  let inJsonBlock = false;
  
  for (const line of lines) {
    if (line.trim().startsWith('{') && line.trim().endsWith('}')) {
      // 单行JSON
      try {
        const block = JSON.parse(line.trim());
        blocks.push(block);
      } catch (e) {
        // 不是有效的JSON，作为普通文本处理
        if (!inJsonBlock) {
          currentBlock += line + '\n';
        }
      }
    } else if (line.trim().startsWith('{')) {
      // 多行JSON开始
      inJsonBlock = true;
      currentBlock = line + '\n';
    } else if (line.trim().endsWith('}') && inJsonBlock) {
      // 多行JSON结束
      currentBlock += line;
      try {
        const block = JSON.parse(currentBlock);
        blocks.push(block);
      } catch (e) {
        // 解析失败，作为普通文本处理
        currentBlock = '';
      }
      inJsonBlock = false;
      currentBlock = '';
    } else if (inJsonBlock) {
      // 多行JSON中间
      currentBlock += line + '\n';
    } else {
      // 普通文本
      currentBlock += line + '\n';
    }
  }
  
  return { blocks, remainingText: currentBlock.trim() };
};

// 渲染消息内容
const renderMessageContent = (message: ChatMessage) => {
  if (message.role === 'user') {
    return message.content;
  }
  
  // 对于AI消息，优先使用预解析的JSON块
  if (message.role === 'assistant') {
    let renderedContent = '';
    
    // 如果有预解析的JSON块，直接使用
    if (message.json_blocks && message.json_blocks.length > 0) {
      // 添加文本内容
      if (message.content) {
        renderedContent += message.content;
      }
      
      // 渲染每个JSON块
      message.json_blocks.forEach((block, index) => {
        if (index > 0 || message.content) {
          renderedContent += '\n\n';
        }
        
        // 为每个type创建一个独立的格子
        const blockType = block.type || 'unknown';
        const blockContent = block.content || '';
        
        renderedContent += `<div class="json-block json-block-${blockType}">`;
        renderedContent += `<div class="json-block-header">Type: ${blockType}</div>`;
        renderedContent += `<div class="json-block-content">${blockContent}</div>`;
        renderedContent += `</div>`;
      });
    } else {
      // 兼容旧格式：解析内容中的JSON块
      const { blocks, remainingText } = parseJsonBlocks(message.content);
      
      // 添加剩余文本
      if (remainingText) {
        renderedContent += remainingText;
      }
      
      // 渲染每个JSON块
      blocks.forEach((block, index) => {
        if (index > 0 || remainingText) {
          renderedContent += '\n\n';
        }
        
        // 为每个type创建一个独立的格子
        const blockType = block.type || 'unknown';
        const blockContent = block.content || '';
        
        renderedContent += `<div class="json-block json-block-${blockType}">`;
        renderedContent += `<div class="json-block-header">Type: ${blockType}</div>`;
        renderedContent += `<div class="json-block-content">${blockContent}</div>`;
        renderedContent += `</div>`;
      });
    }
    
    return renderedContent;
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

/* JSON块样式 */
:deep(.json-block) {
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
