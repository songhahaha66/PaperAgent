<template>
  <Teleport :to="clip || 'body'"
    ><div class="paper-text-marks">
      <span
        v-for="(rect, index) in selectedRects"
        :key="`selection:${index}`"
        class="active-selection"
        :style="rect"
        aria-hidden="true"
      />
      <button
        v-for="mark in marks"
        :key="mark.key"
        type="button"
        class="text-mark"
        :data-kind="mark.annotation.kind"
        :style="mark.style"
        :title="mark.annotation.label"
        :aria-label="mark.annotation.label"
        @pointerdown.prevent
        @click="emit('open', mark.annotation, mark.anchor)"
      /></div
  ></Teleport>
</template>
<script setup lang="ts">
import { nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { uniqueTextSpan, type TextAnnotation, type PaperAnchor } from '@/utils/paperAnnotations'
const props = defineProps<{
  root: HTMLElement | null
  clip: HTMLElement | null
  annotations: TextAnnotation[]
  selectionRange?: Range | null
  selectedQuote?: string
}>()
const emit = defineEmits<{ open: [annotation: TextAnnotation, anchor: PaperAnchor] }>()
interface Mark {
  key: string
  annotation: TextAnnotation
  anchor: PaperAnchor
  style: Record<string, string>
}
const marks = ref<Mark[]>([])
const selectedRects = ref<Record<string, string>[]>([])
let observer: MutationObserver | undefined,
  resize: ResizeObserver | undefined,
  frame = 0
function update() {
  marks.value = []
  selectedRects.value = []
  const root = props.root,
    clip = props.clip
  if (!root || !clip || !root.isConnected) return
  const nodes: Text[] = []
  const walk = document.createTreeWalker(root, NodeFilter.SHOW_TEXT, {
    acceptNode(node) {
      return node.parentElement?.closest('button, script, style, .katex-mathml')
        ? NodeFilter.FILTER_REJECT
        : NodeFilter.FILTER_ACCEPT
    },
  })
  while (walk.nextNode()) nodes.push(walk.currentNode as Text)
  const text = nodes.map((node) => node.data).join('')
  const clipRect = clip.getBoundingClientRect()
  const readingRect = root.closest('.pages, .read-text')?.getBoundingClientRect() || clipRect
  function quoteRange(quote: string) {
    const span = uniqueTextSpan(text, quote)
    if (!span) return null
    const range = document.createRange()
    let offset = 0,
      started = false,
      ended = false
    for (const node of nodes) {
      if (!started && span.start < offset + node.length) {
        range.setStart(node, span.start - offset)
        started = true
      }
      if (started && span.end <= offset + node.length) {
        range.setEnd(node, span.end - offset)
        ended = true
        break
      }
      offset += node.length
    }
    return ended ? range : null
  }
  const selectedRange =
    props.selectionRange &&
    root.contains(props.selectionRange.startContainer) &&
    root.contains(props.selectionRange.endContainer)
      ? props.selectionRange
      : props.selectedQuote
        ? quoteRange(props.selectedQuote)
        : null
  if (selectedRange)
    for (const rect of Array.from(selectedRange.getClientRects())) {
      const left = Math.max(rect.left, clipRect.left, readingRect.left, 0),
        right = Math.min(rect.right, clipRect.right, readingRect.right, window.innerWidth),
        top = Math.max(rect.top, clipRect.top, readingRect.top, 0),
        bottom = Math.min(rect.bottom, clipRect.bottom, readingRect.bottom, window.innerHeight)
      if (right > left && bottom > top)
        selectedRects.value.push({
          left: left - clipRect.left + 'px',
          top: top - clipRect.top + 'px',
          width: right - left + 'px',
          height: bottom - top + 'px',
        })
    }
  for (const annotation of props.annotations) {
    const range = quoteRange(annotation.quote)
    if (!range) continue
    const anchor: PaperAnchor = () =>
      range.startContainer.isConnected ? range.getBoundingClientRect() : null
    Array.from(range.getClientRects()).forEach((rect, index) => {
      const left = Math.max(rect.left, clipRect.left, readingRect.left, 0),
        right = Math.min(rect.right, clipRect.right, readingRect.right, window.innerWidth)
      const top = rect.bottom - 1 + (annotation.kind === 'change' ? 7 : 0)
      if (
        right <= left ||
        rect.height < 1 ||
        top < Math.max(clipRect.top, readingRect.top, 0) ||
        top + 6 > Math.min(clipRect.bottom, readingRect.bottom, window.innerHeight)
      )
        return
      marks.value.push({
        key: annotation.id + ':' + index,
        annotation,
        anchor,
        style: {
          left: left - clipRect.left + 'px',
          top: top - clipRect.top + 'px',
          width: right - left + 'px',
        },
      })
    })
  }
}
function schedule() {
  cancelAnimationFrame(frame)
  frame = requestAnimationFrame(update)
}
watch(
  () => [props.root, props.clip, props.annotations, props.selectionRange, props.selectedQuote],
  async () => {
    observer?.disconnect()
    resize?.disconnect()
    await nextTick()
    if (props.root) {
      observer = new MutationObserver(schedule)
      observer.observe(props.root, { subtree: true, childList: true, characterData: true })
      resize = new ResizeObserver(schedule)
      resize.observe(props.root)
    }
    schedule()
  },
)
onMounted(() => {
  window.addEventListener('scroll', schedule, true)
  window.addEventListener('resize', schedule)
  schedule()
})
onBeforeUnmount(() => {
  observer?.disconnect()
  resize?.disconnect()
  cancelAnimationFrame(frame)
  window.removeEventListener('scroll', schedule, true)
  window.removeEventListener('resize', schedule)
})
</script>
<style scoped>
.active-selection {
  position: absolute;
  z-index: 1;
  pointer-events: none;
  background: color-mix(in srgb, var(--pa-info) 24%, transparent);
  border-radius: var(--pa-radius-inline);
  box-shadow: inset 0 -1px var(--pa-info);
}
.text-mark {
  position: absolute;
  z-index: 20;
  padding: 0;
  margin: 0;
  height: 6px;
  min-height: 0;
  border: 0;
  border-top: 2px solid var(--pa-accent);
  background: transparent;
  cursor: pointer;
}
.text-mark[data-kind='change'] {
  border-top: 2px dashed var(--pa-warning);
}
.text-mark:hover,
.text-mark:focus-visible {
  height: 9px;
  outline: none;
  background: var(--pa-accent-soft);
  box-shadow: 0 0 0 2px var(--pa-accent-border);
}
.text-mark[data-kind='change']:hover {
  background: var(--pa-warning-soft);
}
</style>
