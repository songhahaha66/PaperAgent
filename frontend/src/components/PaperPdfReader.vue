<template>
  <section class="pdf-reader">
    <header>
      <button :disabled="page <= 1 || loading" @click="page--">上一页</button
      ><span>第 {{ page }} / {{ count || '…' }} 页</span
      ><button :disabled="page >= count || loading" @click="page++">下一页</button
      ><button @click="zoom = Math.max(0.5, zoom - 0.15)">−</button
      ><button @click="zoom = Math.min(2, zoom + 0.15)">＋</button
      ><button :disabled="!selection" @click="$emit('selection', selection)">评论选中文字</button>
    </header>
    <p v-if="error" role="alert">{{ error }}</p>
    <p v-if="loading">正在加载 PDF…</p>
    <div class="pages" ref="scroller">
      <div ref="pageElement" class="pdf-page" @mouseup="capture" @keyup="capture"></div>
    </div>
  </section>
</template>
<script setup lang="ts">
import { nextTick, onBeforeUnmount, ref, watch } from 'vue'
import {
  getDocument,
  GlobalWorkerOptions,
  TextLayer,
  type PDFDocumentProxy,
  type PDFDocumentLoadingTask,
} from 'pdfjs-dist'
import workerUrl from 'pdfjs-dist/build/pdf.worker.min.mjs?url'
import { useAuthStore } from '@/stores/auth'
GlobalWorkerOptions.workerSrc = workerUrl
const props = defineProps<{ workId: string; version?: string }>()
const emit = defineEmits<{ selection: [quote: string]; citation: [url: string] }>()
const auth = useAuthStore()
const page = ref(1),
  count = ref(0),
  zoom = ref(1),
  loading = ref(false),
  error = ref(''),
  selection = ref('')
const pageElement = ref<HTMLElement | null>(null),
  scroller = ref<HTMLElement | null>(null)
let pdf: PDFDocumentProxy | undefined,
  task: PDFDocumentLoadingTask | undefined,
  generation = 0,
  renderGeneration = 0
let textLayer: TextLayer | undefined,
  renderTask: ReturnType<Awaited<ReturnType<PDFDocumentProxy['getPage']>>['render']> | undefined
const apiBase = import.meta.env.VITE_API_BASE_URL || 'http://localhost:8000'
function capture() {
  const selected = window.getSelection()
  selection.value =
    selected &&
    pageElement.value?.contains(selected.anchorNode) &&
    pageElement.value.contains(selected.focusNode)
      ? selected.toString().trim()
      : ''
}
async function render() {
  const seq = ++renderGeneration
  renderTask?.cancel()
  textLayer?.cancel()
  if (!pdf) return
  loading.value = true
  error.value = ''
  selection.value = ''
  try {
    const source = await pdf.getPage(page.value)
    await nextTick()
    const root = pageElement.value
    if (!root || seq !== renderGeneration) return
    root.replaceChildren()
    const viewport = source.getViewport({ scale: zoom.value })
    root.style.width = viewport.width + 'px'
    root.style.height = viewport.height + 'px'
    root.style.setProperty('--total-scale-factor', String(viewport.scale))
    const canvas = document.createElement('canvas')
    const ratio = window.devicePixelRatio || 1
    canvas.width = viewport.width * ratio
    canvas.height = viewport.height * ratio
    canvas.style.width = viewport.width + 'px'
    canvas.style.height = viewport.height + 'px'
    root.append(canvas)
    renderTask = source.render({
      canvas,
      viewport,
      transform: ratio === 1 ? undefined : [ratio, 0, 0, ratio, 0, 0],
    })
    await renderTask.promise
    if (seq !== renderGeneration) return
    const textContent = await source.getTextContent()
    if (seq !== renderGeneration) return
    const text = document.createElement('div')
    text.className = 'textLayer'
    root.append(text)
    textLayer = new TextLayer({
      textContentSource: textContent,
      container: text,
      viewport,
    })
    await textLayer.render()
    const annotations = await source.getAnnotations()
    if (seq !== renderGeneration) return
    for (const annotation of annotations) {
      const url = annotation.url || annotation.unsafeUrl
      if (typeof url !== 'string' || !/^https?:\/\//i.test(url) || !Array.isArray(annotation.rect))
        continue
      const rect = [
        ...viewport.convertToViewportPoint(annotation.rect[0], annotation.rect[1]),
        ...viewport.convertToViewportPoint(annotation.rect[2], annotation.rect[3]),
      ]
      const link = document.createElement('button')
      link.className = 'pdf-link'
      link.title = '查看引用来源'
      link.setAttribute('aria-label', '查看引用来源')
      Object.assign(link.style, {
        left: Math.min(rect[0]!, rect[2]!) + 'px',
        top: Math.min(rect[1]!, rect[3]!) + 'px',
        width: Math.abs(rect[2]! - rect[0]!) + 'px',
        height: Math.abs(rect[3]! - rect[1]!) + 'px',
      })
      link.onclick = () => emit('citation', url)
      root.append(link)
    }
  } catch (cause) {
    if (
      seq === renderGeneration &&
      !(cause instanceof Error && cause.name === 'RenderingCancelledException')
    )
      error.value = 'PDF 文本预览加载失败，请刷新或打开下载文件。'
  } finally {
    if (seq === renderGeneration) loading.value = false
  }
}
async function load() {
  const seq = ++generation
  const workId = props.workId,
    version = props.version || '',
    previousTask = task
  ++renderGeneration
  renderTask?.cancel()
  textLayer?.cancel()
  task = undefined
  pdf = undefined
  loading.value = true
  error.value = ''
  try {
    await previousTask?.destroy()
    if (seq !== generation) return
    const response = await fetch(
      `${apiBase}/api/workspace/${encodeURIComponent(workId)}/download/paper.pdf?version=${encodeURIComponent(version)}`,
      { headers: { Authorization: `Bearer ${auth.token || ''}` }, cache: 'no-store' },
    )
    if (!response.ok) throw new Error('download')
    const bytes = new Uint8Array(await response.arrayBuffer())
    if (seq !== generation) return
    task = getDocument({ data: bytes })
    const document = await task.promise
    if (seq !== generation) return
    pdf = document
    count.value = pdf.numPages
    page.value = Math.min(page.value, count.value)
    await render()
  } catch {
    if (seq === generation) {
      error.value = 'PDF 暂时无法读取，请稍后重试。'
      loading.value = false
    }
  }
}
watch(() => [props.workId, props.version], load, { immediate: true })
watch([page, zoom], render)
onBeforeUnmount(() => {
  generation++
  renderGeneration++
  renderTask?.cancel()
  textLayer?.cancel()
  void task?.destroy()
})
</script>
<style scoped>
.pdf-reader {
  display: flex;
  flex-direction: column;
  min-height: 0;
  height: 100%;
}
header {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 10px;
  flex-wrap: wrap;
  font-size: 12px;
}
button {
  border: 1px solid var(--pa-line);
  background: var(--pa-surface);
  color: var(--pa-ink);
  padding: 5px 9px;
  border-radius: var(--pa-radius-control);
  cursor: pointer;
}
button:disabled {
  opacity: 0.4;
}
.pages {
  overflow: auto;
  flex: 1;
  padding: 15px;
  background: var(--pa-surface-muted);
}
.pdf-page {
  position: relative;
  margin: auto;
  background: var(--pa-document-paper);
  color: var(--pa-document-ink);
  --min-font-size: 1;
  --text-scale-factor: calc(var(--total-scale-factor) * var(--min-font-size));
  --min-font-size-inv: calc(1 / var(--min-font-size));
}
:deep(.textLayer) {
  position: absolute;
  inset: 0;
  overflow: clip;
  line-height: 1;
  letter-spacing: normal;
  word-spacing: normal;
  text-size-adjust: none;
  transform-origin: 0 0;
}
:deep(.textLayer span),
:deep(.textLayer br) {
  color: transparent;
  position: absolute;
  white-space: pre;
  cursor: text;
  transform-origin: 0 0;
  user-select: text;
}
:deep(.textLayer > :not(.markedContent)),
:deep(.textLayer .markedContent span:not(.markedContent)) {
  --font-height: 0;
  font-size: calc(var(--text-scale-factor) * var(--font-height));
  --scale-x: 1;
  --rotate: 0deg;
  transform: rotate(var(--rotate)) scaleX(var(--scale-x)) scale(var(--min-font-size-inv));
}
:deep(.textLayer .markedContent) {
  display: contents;
}
:deep(.textLayer ::selection) {
  background: color-mix(in srgb, var(--pa-info) 40%, transparent);
}
:deep(.pdf-link) {
  position: absolute;
  padding: 0;
  border: 0;
  background: transparent;
  cursor: pointer;
  z-index: 3;
}
:deep(.pdf-link:hover),
:deep(.pdf-link:focus) {
  background: color-mix(in srgb, var(--pa-info) 20%, transparent);
  outline: 1px solid var(--pa-info);
}
</style>
