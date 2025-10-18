<template>
  <div ref="rootEl" class="markdown-content" v-html="renderedContent"></div>
</template>

<script setup lang="ts">
import { computed, onMounted, onBeforeUnmount, ref, watch, nextTick } from 'vue'
import MarkdownIt from 'markdown-it'
import mila from 'markdown-it-katex'
import 'katex/dist/katex.min.css'
import { useAuthStore } from '@/stores/auth'
import { workspaceFileAPI } from '@/api/workspace'

interface Props {
  content: string
  // 渲染上下文（用于相对路径图片解析&鉴权）
  workId?: string // 工作区ID，可选
  basePath?: string // 当前 markdown 文件所在目录，用于解析相对图片路径
}

const props = defineProps<Props>()
const authStore = useAuthStore()

// 简单的相对路径规范化
function normalizePath(base: string, rel: string): string {
  if (!base) return rel
  const baseParts = base.split('/').filter(Boolean)
  const relParts = rel.split('/')
  for (const part of relParts) {
    if (part === '.' || part === '') continue
    if (part === '..') baseParts.pop()
    else baseParts.push(part)
  }
  return baseParts.join('/')
}

// markdown-it 实例（支持公式、基础语法）
const md = new MarkdownIt({
  html: true,
  linkify: true,
  breaks: true,
})
md.use(mila)

// 预处理内容，将方括号LaTeX公式转换为美元符号格式
function preprocessLatex(content: string): string {
  if (!content) return ''
  
  // 处理行内公式 [ ... ] -> $ ... $
  // 匹配 [ 后面跟着非空格字符，然后是 LaTeX 内容，最后是 ]
  content = content.replace(/\[([^[\]]*\\[^[\]]*)\]/g, '$$$1$$')
  
  return content
}

// 渲染后的 HTML
const renderedContent = computed(() => {
  if (!props.content) return ''
  const processedContent = preprocessLatex(props.content)
  return md.render(processedContent)
})

// 图片 blob 缓存：key -> blobUrl
const imageBlobCache = new Map<string, string>()
const rootEl = ref<HTMLElement | null>(null)

async function ensureImageSrc(img: HTMLImageElement) {
  const rawSrc = img.getAttribute('src')?.trim()
  if (!rawSrc) return

  // 绝对 URL 或 data: 不处理
  if (/^(https?:)?\/\//i.test(rawSrc) || rawSrc.startsWith('data:') || rawSrc.startsWith('blob:'))
    return

  // 没有 workId 或 token，无权转换，直接忽略
  if (!props.workId || !authStore.token) return

  // 处理相对路径（相对于 basePath）
  const filePath = props.basePath ? normalizePath(props.basePath, rawSrc) : rawSrc
  const cacheKey = `${props.workId}::${filePath}`
  if (imageBlobCache.has(cacheKey)) {
    img.src = imageBlobCache.get(cacheKey)!
    return
  }

  try {
    const url = workspaceFileAPI.getImageUrl(authStore.token!, props.workId, filePath)
    const resp = await fetch(url, { headers: { Authorization: `Bearer ${authStore.token}` } })
    if (!resp.ok) throw new Error(`HTTP ${resp.status}`)
    const blob = await resp.blob()
    const blobUrl = URL.createObjectURL(blob)
    imageBlobCache.set(cacheKey, blobUrl)
    img.src = blobUrl
  } catch (e) {
    console.error('加载图片失败:', e)
  }
}

async function processImages() {
  await nextTick()
  if (!rootEl.value) return
  const imgs = Array.from(rootEl.value.querySelectorAll('img')) as HTMLImageElement[]
  // 懒加载标记
  imgs.forEach((img) => img.setAttribute('loading', 'lazy'))
  await Promise.all(imgs.map(ensureImageSrc))
}

watch(
  () => renderedContent.value,
  () => {
    processImages()
  },
)
onMounted(() => {
  processImages()
})
onBeforeUnmount(() => {
  // 释放 blob 资源
  for (const url of imageBlobCache.values()) {
    if (url.startsWith('blob:')) URL.revokeObjectURL(url)
  }
  imageBlobCache.clear()
})
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
  font-family: 'Courier New', monospace;
}

/* 代码块样式：去除横向滚动，改为自动换行 */
.markdown-content :deep(pre) {
  background-color: #f5f5f5;
  padding: 12px;
  border-radius: 4px;
  margin: 8px 0;
  white-space: pre-wrap; /* 换行 */
  word-break: break-word;
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
