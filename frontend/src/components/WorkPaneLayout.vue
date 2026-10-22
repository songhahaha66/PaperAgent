<template>
  <div
    ref="layout"
    :class="['work-pane-layout', { stacked: vertical, resizing, dragging: !!dragged }]"
    @dragover.prevent="updateDropHint"
    @drop.prevent="dropAtEdge"
  >
    <template v-for="(group, index) in groups" :key="group">
      <div
        v-if="index"
        class="pane-separator"
        role="separator"
        tabindex="0"
        :aria-orientation="vertical ? 'horizontal' : 'vertical'"
        :aria-valuenow="Math.round(session.splitRatio * 100)"
        :aria-valuemin="25"
        :aria-valuemax="75"
        :aria-label="isZh ? '调整窗格大小' : 'Resize panes'"
        @pointerdown="startResize"
        @pointermove="resize"
        @pointerup="stopResize"
        @lostpointercapture="resizing = false"
        @keydown="resizeByKey"
      />
      <section
        class="workspace-pane"
        :style="
          groups.length === 2
            ? { flexBasis: `${(index ? 1 - session.splitRatio : session.splitRatio) * 100}%` }
            : undefined
        "
        @focusin="$emit('focus', compact ? session.focusedGroup : group)"
        @pointerdown="$emit('focus', compact ? session.focusedGroup : group)"
        @dragover.prevent
        @drop.stop.prevent="drop($event, group)"
      >
        <WorkTabBar
          :panels="panels.filter((panel) => displayGroup(panel) === group)"
          :active-id="activeId(group)"
          :id-prefix="`${layoutId}-${group}`"
          :options="openOptions(group)"
          @select="(id) => $emit('select', id)"
          @close="(id) => $emit('close', id)"
          @drag="(id) => (dragged = id)"
          @drop="
            (id, before) => {
              $emit('move', id, group, before)
              dragged = null
            }
          "
        />
        <div :id="`${layoutId}-${group}-content`" class="pane-content" />
      </section>
    </template>
    <div v-if="dragged && dropHint" :class="['split-drop-preview', dropHint]">
      <t-icon name="view-column" /><span>{{ isZh ? '松开以分屏' : 'Release to split' }}</span>
    </div>
    <Teleport
      v-for="panel in panels"
      :key="panel.id"
      :to="`#${layoutId}-${displayGroup(panel)}-content`"
      defer
    >
      <div
        v-show="activeId(displayGroup(panel)) === panel.id"
        :id="`${layoutId}-${displayGroup(panel)}-panel-${groupIndex(panel)}`"
        class="panel-surface"
        role="tabpanel"
        :aria-labelledby="`${layoutId}-${displayGroup(panel)}-tab-${groupIndex(panel)}`"
      >
        <slot :panel="panel" :active="activeId(displayGroup(panel)) === panel.id" />
      </div>
    </Teleport>
  </div>
</template>
<script setup lang="ts">
import { computed, ref, useId, watch } from 'vue'
import { useStudioCopy } from '@/lang/studio'
import type { PanelGroup, SplitEdge, WorkPanel, WorkPanelSession } from '@/stores/workPanels'
import WorkTabBar from '@/components/WorkTabBar.vue'
const props = defineProps<{
  session: WorkPanelSession
  panels: (WorkPanel & { label: string; icon: string })[]
  options: { content: string; value: string }[]
  stacked: boolean
}>()
const emit = defineEmits<{
  select: [id: string]
  close: [id: string]
  open: [kind: string, group: PanelGroup]
  move: [id: string, group: PanelGroup, before?: string]
  split: [id: string, edge: SplitEdge]
  ratio: [value: number]
  focus: [group: PanelGroup]
}>()
const { isZh } = useStudioCopy()
const layoutId = `work-${useId().replace(/[^a-zA-Z0-9_-]/g, '')}`
const layout = ref<HTMLElement | null>(null),
  resizing = ref(false),
  dragged = ref<string | null>(null)
const dropHint = ref<SplitEdge | null>(null)
watch(dragged, (id) => {
  if (!id) dropHint.value = null
})
const vertical = computed(() => props.session.splitAxis === 'vertical')
const compact = computed(() => props.stacked && !vertical.value)
const displayGroup = (panel: WorkPanel): PanelGroup => (compact.value ? 'main' : panel.group)
const groups = computed<PanelGroup[]>(() =>
  !compact.value && props.session.secondaryActiveId
    ? props.session.secondaryFirst
      ? ['secondary', 'main']
      : ['main', 'secondary']
    : ['main'],
)
const activeId = (group: PanelGroup) =>
  (compact.value ? props.session.focusedGroup : group) === 'main'
    ? props.session.activeId
    : props.session.secondaryActiveId || props.session.activeId
const groupIndex = (panel: WorkPanel) =>
  props.panels
    .filter((item) => displayGroup(item) === displayGroup(panel))
    .findIndex((item) => item.id === panel.id)
const openOptions = (group: PanelGroup) =>
  props.options.map((option) => ({ ...option, onClick: () => emit('open', option.value, group) }))
function updateDropHint(event: DragEvent) {
  if (!dragged.value || !layout.value) {
    dropHint.value = null
    return
  }
  const rect = layout.value.getBoundingClientRect()
  const x = (event.clientX - rect.left) / rect.width,
    y = (event.clientY - rect.top) / rect.height
  const edges: [SplitEdge, number][] = [
    ['left', x],
    ['right', 1 - x],
    ['top', y],
    ['bottom', 1 - y],
  ]
  edges.sort((a, b) => a[1] - b[1])
  dropHint.value = edges[0]![1] < 0.22 ? edges[0]![0] : null
}
function dropAtEdge(event: DragEvent) {
  const id = event.dataTransfer?.getData('application/x-paperagent-panel') || dragged.value
  if (id && dropHint.value) emit('split', id, dropHint.value)
  dragged.value = null
  dropHint.value = null
}
function drop(event: DragEvent, group: PanelGroup) {
  if (dropHint.value) {
    dropAtEdge(event)
    return
  }
  const id = event.dataTransfer?.getData('application/x-paperagent-panel') || dragged.value
  if (id) emit('move', id, group)
  dragged.value = null
}
function startResize(event: PointerEvent) {
  if (event.button !== 0) return
  resizing.value = true
  ;(event.currentTarget as HTMLElement).setPointerCapture(event.pointerId)
  event.preventDefault()
}
function resize(event: PointerEvent) {
  if (!resizing.value || !layout.value) return
  const rect = layout.value.getBoundingClientRect()
  const ratio = vertical.value
    ? (event.clientY - rect.top) / rect.height
    : (event.clientX - rect.left) / rect.width
  emit('ratio', Math.max(0.25, Math.min(0.75, ratio)))
}
function stopResize(event: PointerEvent) {
  resizing.value = false
  const target = event.currentTarget as HTMLElement
  if (target.hasPointerCapture(event.pointerId)) target.releasePointerCapture(event.pointerId)
}
function resizeByKey(event: KeyboardEvent) {
  const decrease = vertical.value ? 'ArrowUp' : 'ArrowLeft',
    increase = vertical.value ? 'ArrowDown' : 'ArrowRight'
  if (event.key !== decrease && event.key !== increase) return
  event.preventDefault()
  emit(
    'ratio',
    Math.max(
      0.25,
      Math.min(0.75, props.session.splitRatio + (event.key === increase ? 0.05 : -0.05)),
    ),
  )
}
</script>
<style scoped>
.work-pane-layout {
  position: relative;
  display: flex;
  flex: 1;
  min-height: 0;
  min-width: 0;
  overflow: hidden;
}
.work-pane-layout.stacked {
  flex-direction: column;
}
.workspace-pane {
  display: flex;
  flex-direction: column;
  flex: 1 1 0;
  min-width: 0;
  min-height: 0;
  overflow: hidden;
}
.pane-content,
.panel-surface {
  display: flex;
  flex: 1;
  min-height: 0;
  min-width: 0;
  overflow: hidden;
}
.panel-surface {
  width: 100%;
  flex-direction: column;
}
.pane-separator {
  z-index: 2;
  flex: 0 0 5px;
  background: var(--pa-surface-hover);
  border-inline: 1px solid var(--pa-line);
  cursor: col-resize;
  outline-offset: -2px;
}
.pane-separator:hover,
.pane-separator:focus-visible,
.resizing .pane-separator {
  background: var(--pa-accent-border);
}
.stacked .pane-separator {
  cursor: row-resize;
  border-inline: 0;
  border-block: 1px solid var(--pa-line);
}
.resizing {
  user-select: none;
}
.split-drop-preview {
  pointer-events: none;
  position: absolute;
  z-index: 5;
  display: flex;
  align-items: center;
  justify-content: center;
  flex-direction: column;
  gap: 12px;
  background: var(--pa-accent-soft);
  border: 2px dashed var(--pa-accent-border);
  color: var(--pa-accent);
  font-size: 13px;
  box-sizing: border-box;
}
.split-drop-preview.left {
  left: 0;
  top: 48px;
  bottom: 0;
  width: 50%;
}
.split-drop-preview.right {
  right: 0;
  top: 48px;
  bottom: 0;
  width: 50%;
}
.split-drop-preview.top {
  top: 48px;
  left: 0;
  right: 0;
  height: calc(50% - 24px);
}
.split-drop-preview.bottom {
  bottom: 0;
  left: 0;
  right: 0;
  height: 50%;
}
.dragging :deep(iframe),
.resizing :deep(iframe) {
  pointer-events: none;
}
</style>
