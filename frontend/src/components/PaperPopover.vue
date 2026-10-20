<template>
  <Teleport :to="host || 'body'">
    <section
      v-if="open"
      ref="panel"
      class="paper-popover"
      :class="{ compact, 'in-paper': !!host }"
      :data-tone="tone"
      :style="position"
      role="dialog"
      :aria-label="title"
      tabindex="-1"
      @keydown.esc.stop.prevent="close"
    >
      <header v-if="!compact">
        <strong>{{ title }}</strong
        ><button type="button" :aria-label="`关闭${title}`" @click="close">×</button>
      </header>
      <div class="popover-content"><slot /></div>
    </section>
  </Teleport>
</template>
<script setup lang="ts">
import { nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { placePaperPopover, type PaperAnchor } from '@/utils/paperAnnotations'
const props = withDefaults(
  defineProps<{
    open: boolean
    title: string
    anchor?: PaperAnchor | null
    tone?: 'comment' | 'source' | 'change' | 'opinion'
    width?: number
    compact?: boolean
    host?: HTMLElement | null
  }>(),
  { width: 360, tone: 'comment', compact: false },
)
const emit = defineEmits<{ close: [] }>()
const panel = ref<HTMLElement | null>(null)
const position = ref<Record<string, string>>({ visibility: 'hidden' })
let previousFocus: HTMLElement | null = null,
  observer: ResizeObserver | undefined
function close() {
  emit('close')
  if (previousFocus?.isConnected) previousFocus.focus({ preventScroll: true })
}
function update() {
  if (!props.open || !panel.value) return
  const rect = props.anchor?.()
  if (props.anchor && !rect) {
    emit('close')
    return
  }
  const point = rect || {
    left: window.innerWidth - props.width - 24,
    right: window.innerWidth - 24,
    top: 60,
    bottom: 60,
  }
  const hostRect = props.host?.getBoundingClientRect()
  if (hostRect && (hostRect.width < 1 || hostRect.height < 1)) {
    emit('close')
    return
  }
  if (
    rect &&
    hostRect &&
    (rect.bottom < hostRect.top ||
      rect.top > hostRect.bottom ||
      rect.right < hostRect.left ||
      rect.left > hostRect.right)
  ) {
    emit('close')
    return
  }
  const localPoint = hostRect
    ? {
        left: point.left - hostRect.left,
        right: point.right - hostRect.left,
        top: point.top - hostRect.top,
        bottom: point.bottom - hostRect.top,
      }
    : point
  const result = placePaperPopover(
    localPoint,
    { width: hostRect?.width || window.innerWidth, height: hostRect?.height || window.innerHeight },
    { width: props.width, height: panel.value.scrollHeight },
  )
  position.value = {
    left: result.left + 'px',
    top: result.top + 'px',
    width: result.width + 'px',
    maxHeight: result.maxHeight + 'px',
  }
}
function outside(event: PointerEvent) {
  if (props.open && event.target instanceof Node && !panel.value?.contains(event.target))
    emit('close')
}
watch(
  () => [props.open, props.anchor, props.host, props.compact],
  async () => {
    observer?.disconnect()
    if (!props.open) return
    previousFocus = document.activeElement instanceof HTMLElement ? document.activeElement : null
    await nextTick()
    update()
    const focus = panel.value?.querySelector<HTMLElement>('[data-autofocus]') || panel.value
    focus?.focus({ preventScroll: true })
    if (panel.value) {
      observer = new ResizeObserver(update)
      observer.observe(panel.value)
      if (props.host) observer.observe(props.host)
    }
  },
)
onMounted(() => {
  document.addEventListener('pointerdown', outside, true)
  window.addEventListener('resize', update)
  window.addEventListener('scroll', update, true)
})
onBeforeUnmount(() => {
  observer?.disconnect()
  document.removeEventListener('pointerdown', outside, true)
  window.removeEventListener('resize', update)
  window.removeEventListener('scroll', update, true)
})
</script>
<style scoped>
.paper-popover {
  position: fixed;
  z-index: 1200;
  display: flex;
  flex-direction: column;
  overflow: hidden;
  border: 1px solid var(--pa-accent-border);
  border-radius: var(--pa-radius-overlay);
  background: var(--pa-surface);
  color: var(--pa-ink);
  box-shadow: var(--pa-shadow-overlay);
  font:
    13px/1.6 -apple-system,
    BlinkMacSystemFont,
    'PingFang SC',
    sans-serif;
}
.paper-popover.in-paper {
  position: absolute;
  z-index: 40;
}
.paper-popover.compact {
  border: 1px solid var(--pa-line);
  border-radius: var(--pa-radius-overlay);
}
.compact .popover-content {
  padding: 0;
}
.paper-popover[data-tone='source'] {
  border-color: var(--pa-info-border);
}
.paper-popover[data-tone='change'] {
  border-color: var(--pa-warning);
}
.paper-popover[data-tone='opinion'] {
  border-color: var(--pa-note);
}
header {
  flex-shrink: 0;
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 16px;
  padding: 12px 16px;
  border-bottom: 1px solid var(--pa-line);
}
header strong {
  font-weight: 600;
}
header button {
  width: 28px;
  height: 28px;
  border: 0;
  border-radius: var(--pa-radius-control);
  background: var(--pa-surface-muted);
  color: var(--pa-muted);
  cursor: pointer;
  font-size: 18px;
}
button:focus-visible {
  outline: 2px solid var(--pa-accent);
  outline-offset: 2px;
}
.popover-content {
  min-height: 0;
  padding: 16px;
  overflow: auto;
  overscroll-behavior: contain;
}
</style>
