<template>
  <div class="markdown-content" v-html="renderedContent"></div>
</template>

<script setup lang="ts">
import { computed } from 'vue';

interface Props {
  content: string;
}

const props = defineProps<Props>();

// Markdown渲染函数 - 统一来自多个地方的重复实现
const renderedContent = computed(() => {
  const text = props.content;
  if (!text) return '';
  
  // 先处理代码块，避免代码块中的标记被处理
  let codeBlocks: string[] = [];
  let codeBlockCounter = 0;
  
  // 提取代码块（包括语言标识）
  let processedText = text.replace(/```(\w+)?\n([\s\S]*?)\n```/g, (match, lang, content) => {
    codeBlocks.push(`<pre><code class="language-${lang || 'plaintext'}">${content}</code></pre>`);
    return `{{CODE_BLOCK_${codeBlockCounter++}}}`;
  });

  // 处理行内代码
  processedText = processedText.replace(/`([^`]+)`/g, '<code>$1</code>');
  
  // 按照从h6到h1的顺序处理标题
  processedText = processedText
    .replace(/^###### (.*)$/gm, '<h6>$1</h6>')
    .replace(/^##### (.*)$/gm, '<h5>$1</h5>')
    .replace(/^#### (.*)$/gm, '<h4>$1</h4>')
    .replace(/^### (.*)$/gm, '<h3>$1</h3>')
    .replace(/^## (.*)$/gm, '<h2>$1</h2>')
    .replace(/^# (.*)$/gm, '<h1>$1</h1>');
  
  // 处理粗体和斜体
  processedText = processedText
    .replace(/\*\*(.*?)\*\*/g, '<strong>$1</strong>')
    .replace(/\*(.*?)\*/g, '<em>$1</em>');
  
  // 处理链接
  processedText = processedText.replace(/\[([^\]]+)\]\(([^)]+)\)/g, '<a href="$2" target="_blank">$1</a>');
  
  // 处理无序列表
  processedText = processedText.replace(/^\s*[\*|\-|\+]\s(.*)$/gm, '<li>$1</li>');
  processedText = processedText.replace(/(<li>.*<\/li>)/gms, '<ul>$1</ul>');
  
  // 处理有序列表
  processedText = processedText.replace(/^\s*(\d+)\.\s(.*)$/gm, '<li data-line="$1">$2</li>');
  processedText = processedText.replace(/(<li data-line="\d+">.*<\/li>)/gms, '<ol>$1</ol>');
  processedText = processedText.replace(/ data-line="\d+"/g, '');
  
  // 处理换行（但不在块级元素内部添加<br>）
  processedText = processedText.replace(/\n/g, '<br>');

  // 恢复代码块
  for (let i = 0; i < codeBlockCounter; i++) {
    processedText = processedText.replace(`{{CODE_BLOCK_${i}}}`, codeBlocks[i]);
  }
  
  return processedText;
});
</script>

<style scoped>
/* 基础markdown样式 */
.markdown-content {
  padding: 16px;
  line-height: 1.6;
  background-color: #f9f9f9;
  overflow-y: auto;
}

/* 标题样式 */
.markdown-content :deep(h1),
.markdown-content :deep(h2),
.markdown-content :deep(h3),
.markdown-content :deep(h4),
.markdown-content :deep(h5),
.markdown-content :deep(h6) {
  margin: 16px 0 8px 0;
  color: #2c3e50;
}

/* 段落样式 */
.markdown-content :deep(p) {
  margin: 8px 0;
  line-height: 1.6;
}

/* 行内代码样式 */
.markdown-content :deep(code) {
  background-color: #f0f0f0;
  padding: 2px 4px;
  border-radius: 3px;
  font-family: 'Courier New', monospace;
}

/* 代码块样式 */
.markdown-content :deep(pre) {
  background-color: #f5f5f5;
  padding: 12px;
  border-radius: 4px;
  overflow-x: auto;
  margin: 8px 0;
}

.markdown-content :deep(pre code) {
  background-color: transparent;
  padding: 0;
}

/* 列表样式 */
.markdown-content :deep(ul),
.markdown-content :deep(ol) {
  margin: 8px 0;
  padding-left: 20px;
}

.markdown-content :deep(li) {
  margin: 4px 0;
}

/* 链接样式 */
.markdown-content :deep(a) {
  color: #1976d2;
  text-decoration: underline;
}

.markdown-content :deep(a:hover) {
  color: #1565c0;
}

/* 强调样式 */
.markdown-content :deep(strong) {
  font-weight: 600;
}

.markdown-content :deep(em) {
  font-style: italic;
}
</style>