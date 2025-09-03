<template>
  <div ref="rootEl" class="markdown-content" v-html="renderedContent"></div>
</template>

<script setup lang="ts">
import { computed, onMounted, onBeforeUnmount, ref, watch, nextTick } from 'vue';
import MarkdownIt from 'markdown-it';
import mila from 'markdown-it-katex';
import highlightjs from 'markdown-it-highlightjs';
import hljs from 'highlight.js';
import 'katex/dist/katex.min.css';
import 'highlight.js/styles/github.css';
import { useAuthStore } from '@/stores/auth';
import { workspaceFileAPI } from '@/api/workspace';

interface Props {
  content: string;
  // 渲染上下文（用于相对路径图片解析&鉴权）
  workId?: string;      // 工作区ID，可选
  basePath?: string;    // 当前 markdown 文件所在目录，用于解析相对图片路径
}

const props = defineProps<Props>();
const authStore = useAuthStore();

// 简单的相对路径规范化
function normalizePath(base: string, rel: string): string {
  if (!base) return rel;
  const baseParts = base.split('/').filter(Boolean);
  const relParts = rel.split('/');
  for (const part of relParts) {
    if (part === '.' || part === '') continue;
    if (part === '..') baseParts.pop();
    else baseParts.push(part);
  }
  return baseParts.join('/');
}

// markdown-it 实例（支持公式、基础语法、代码高亮）
const md = new MarkdownIt({
  html: true,
  linkify: true,
  breaks: true,
  highlight: function (str, lang) {
    if (lang && hljs.getLanguage(lang)) {
      try {
        return hljs.highlight(str, { language: lang }).value;
      } catch (__) {}
    }
    return ''; // use external default escaping
  }
});
md.use(mila);
md.use(highlightjs);

// 渲染后的 HTML
const renderedContent = computed(() => {
  if (!props.content) return '';
  return md.render(props.content);
});

// 图片 blob 缓存：key -> blobUrl
const imageBlobCache = new Map<string, string>();
const rootEl = ref<HTMLElement | null>(null);

async function ensureImageSrc(img: HTMLImageElement) {
  const rawSrc = img.getAttribute('src')?.trim();
  if (!rawSrc) return;

  // 绝对 URL 或 data: 不处理
  if (/^(https?:)?\/\//i.test(rawSrc) || rawSrc.startsWith('data:') || rawSrc.startsWith('blob:')) return;

  // 没有 workId 或 token，无权转换，直接忽略
  if (!props.workId || !authStore.token) return;

  // 处理相对路径（相对于 basePath）
  const filePath = props.basePath ? normalizePath(props.basePath, rawSrc) : rawSrc;
  const cacheKey = `${props.workId}::${filePath}`;
  if (imageBlobCache.has(cacheKey)) {
    img.src = imageBlobCache.get(cacheKey)!;
    return;
  }

  try {
    const url = workspaceFileAPI.getImageUrl(authStore.token!, props.workId, filePath);
    const resp = await fetch(url, { headers: { Authorization: `Bearer ${authStore.token}` } });
    if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
    const blob = await resp.blob();
    const blobUrl = URL.createObjectURL(blob);
    imageBlobCache.set(cacheKey, blobUrl);
    img.src = blobUrl;
  } catch (e) {
    console.error('加载图片失败:', e);
  }
}

async function processImages() {
  await nextTick();
  if (!rootEl.value) return;
  const imgs = Array.from(rootEl.value.querySelectorAll('img')) as HTMLImageElement[];
  // 懒加载标记
  imgs.forEach(img => img.setAttribute('loading', 'lazy'));
  await Promise.all(imgs.map(ensureImageSrc));
}

watch(() => renderedContent.value, () => { processImages(); });
onMounted(() => { processImages(); });
onBeforeUnmount(() => {
  // 释放 blob 资源
  for (const url of imageBlobCache.values()) {
    if (url.startsWith('blob:')) URL.revokeObjectURL(url);
  }
  imageBlobCache.clear();
});
</script>

<style scoped>
/* 基础markdown样式 */
.markdown-content {
  /* 隐藏横向滚动，优先让内容换行显示 */
  overflow-x: hidden;
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
  font-family: 'Courier New', 'Monaco', 'Consolas', monospace;
  font-size: 0.9em;
}

/* 代码块样式：去除横向滚动，改为自动换行 */
.markdown-content :deep(pre) {
  background-color: #f8f8f8;
  padding: 12px;
  border-radius: 6px;
  margin: 8px 0;
  white-space: pre-wrap; /* 换行 */
  word-break: break-word;
  border: 1px solid #e1e4e8;
  overflow-x: auto;
}

.markdown-content :deep(pre code) {
  background-color: transparent;
  padding: 0;
  font-family: 'Courier New', 'Monaco', 'Consolas', monospace;
  font-size: 0.85em;
  line-height: 1.45;
}

/* highlight.js 样式覆盖 */
.markdown-content :deep(.hljs) {
  background: transparent !important;
  padding: 0 !important;
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

/* 图片在容器内自适应，避免撑宽导致横向滚动 */
.markdown-content :deep(img) {
  max-width: 100%;
  height: auto;
  display: block;
}

/* 表格适配宽度并允许单元格换行 */
.markdown-content :deep(table) {
  width: 100%;
  border-collapse: collapse;
  table-layout: fixed;
}
.markdown-content :deep(td),
.markdown-content :deep(th) {
  word-break: break-word;
  white-space: normal;
}

/* KaTeX 渲染区域微调（行内与块级） */
.markdown-content :deep(.katex) {
  font-size: 1.2em;
}
.markdown-content :deep(.katex-display) {
  margin: 12px 0;
}
</style>