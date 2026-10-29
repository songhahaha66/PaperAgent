<template>
  <div
    class="work-tab-bar"
    @dragenter.stop.prevent="$emit('drag-over')"
    @dragover.stop.prevent="$emit('drag-over')"
    @drop.stop.prevent="dropOnTab($event)"
  >
    <div
      ref="tabList"
      class="work-tab-list"
      role="tablist"
      :aria-label="isZh ? '工作面板' : 'Work panels'"
    >
      <div
        v-for="(panel, index) in panels"
        :key="panel.id"
        :class="['work-tab', { active: panel.id === activeId }]"
        :draggable="panel.kind !== 'chat'"
        @dragstart="startDrag($event, panel.id)"
        @dragend="$emit('drag', null)"
        @dragover.prevent
        @drop.stop.prevent="dropOnTab($event, panel.id)"
      >
        <button
          :id="`${idPrefix}-tab-${index}`"
          class="work-tab-select"
          role="tab"
          :aria-selected="panel.id === activeId"
          :aria-controls="`${idPrefix}-panel-${index}`"
          :tabindex="panel.id === activeId ? 0 : -1"
          :data-panel-id="panel.id"
          :title="panel.path || panel.label"
          @click="$emit('select', panel.id)"
          @keydown="handleKey($event, index)"
        >
          <t-icon :name="panel.icon" /><span>{{ panel.label }}</span>
        </button>
        <button
          v-if="panel.kind !== 'chat'"
          class="work-tab-close"
          :aria-label="`${isZh ? '关闭' : 'Close'} ${panel.label}`"
          tabindex="-1"
          @click="$emit('close', panel.id)"
        >
          <t-icon name="close" />
        </button>
      </div>
    </div>
    <t-dropdown :options="options" trigger="click" placement="bottom-left">
      <button
        class="add-panel"
        :aria-label="isZh ? '打开面板' : 'Open panel'"
        :title="isZh ? '打开面板' : 'Open panel'"
        aria-haspopup="menu"
      >
        <t-icon name="add" />
      </button>
    </t-dropdown>
  </div>
</template>
<script setup lang="ts">
import { nextTick, ref, watch } from 'vue'
import { useStudioCopy } from '@/lang/studio'
import type { WorkPanel } from '@/stores/workPanels'
const props = defineProps<{
  panels: (WorkPanel & { label: string; icon: string })[]
  activeId: string
  idPrefix: string
  options: { content: string; value: string; onClick: () => void }[]
}>()
const emit = defineEmits<{
  select: [id: string]
  close: [id: string]
  drag: [id: string | null]
  drop: [id: string, beforeId?: string]
  'drag-over': []
}>()
const { isZh } = useStudioCopy()
const tabList = ref<HTMLElement | null>(null)
watch(
  () => [props.activeId, props.panels.length],
  async () => {
    await nextTick()
    tabList.value
      ?.querySelector<HTMLElement>('[aria-selected="true"]')
      ?.scrollIntoView({ block: 'nearest', inline: 'nearest' })
  },
)
function startDrag(event: DragEvent, id: string) {
  event.dataTransfer?.setData('application/x-paperagent-panel', id)
  if (event.dataTransfer) event.dataTransfer.effectAllowed = 'move'
  emit('drag', id)
}
function dropOnTab(event: DragEvent, beforeId?: string) {
  const id = event.dataTransfer?.getData('application/x-paperagent-panel')
  if (id) emit('drop', id, beforeId)
  emit('drag', null)
}
async function handleKey(event: KeyboardEvent, index: number) {
  let next = index
  if (event.key === 'ArrowRight') next = (index + 1) % props.panels.length
  else if (event.key === 'ArrowLeft') next = (index - 1 + props.panels.length) % props.panels.length
  else if (event.key === 'Home') next = 0
  else if (event.key === 'End') next = props.panels.length - 1
  else if (event.key === 'Delete' && props.panels[index]?.kind !== 'chat') {
    emit('close', props.panels[index]!.id)
    event.preventDefault()
    await nextTick()
    tabList.value?.querySelector<HTMLElement>('[aria-selected="true"]')?.focus()
    return
  } else return
  event.preventDefault()
  emit('select', props.panels[next]!.id)
  await nextTick()
  tabList.value?.querySelectorAll<HTMLElement>('[role="tab"]')[next]?.focus()
}
</script>
<style scoped>
.work-tab-bar {
  display: flex;
  align-items: center;
  gap: 6px;
  min-height: 48px;
  padding: 6px 16px 0;
  flex-shrink: 0;
  background: var(--pa-surface-muted);
  border-bottom: 1px solid var(--pa-line);
  box-sizing: border-box;
}
.work-tab-list {
  display: flex;
  align-self: stretch;
  min-width: 0;
  overflow-x: auto;
  scrollbar-width: none;
  gap: 4px;
}
.work-tab-list::-webkit-scrollbar {
  display: none;
}
.work-tab {
  display: flex;
  align-items: center;
  flex-shrink: 0;
  border: 1px solid transparent;
  border-bottom: 0;
  border-radius: var(--pa-radius-control) var(--pa-radius-control) 0 0;
  color: var(--pa-muted);
  position: relative;
}
.work-tab.active {
  background: var(--pa-surface);
  border-color: var(--pa-line);
  color: var(--pa-ink);
}
.work-tab.active::after {
  content: '';
  position: absolute;
  height: 1px;
  background: var(--pa-surface);
  bottom: -1px;
  left: 0;
  right: 0;
}
.work-tab-select {
  display: flex;
  align-items: center;
  gap: 8px;
  min-height: 40px;
  max-width: 210px;
  padding: 0 14px;
  border: 0;
  border-radius: var(--pa-radius-control) var(--pa-radius-control) 0 0;
  background: transparent;
  color: inherit;
  cursor: pointer;
  font-size: 13px;
}
.work-tab-select > span {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.work-tab-select > .t-icon {
  flex-shrink: 0;
  font-size: 15px;
}
.work-tab:not(.active):hover {
  background: var(--pa-surface-hover);
}
.work-tab-close,
.add-panel {
  display: grid;
  place-items: center;
  flex-shrink: 0;
  width: var(--pa-control-height-sm);
  height: var(--pa-control-height-sm);
  border: 0;
  border-radius: var(--pa-radius-inline);
  background: transparent;
  color: var(--pa-muted);
  cursor: pointer;
  font-size: 15px;
}
.work-tab-close {
  margin-right: 6px;
}
.work-tab-close:hover,
.add-panel:hover {
  background: var(--pa-surface-hover);
  color: var(--pa-ink);
}
.add-panel {
  margin: 0 0 6px;
  width: 32px;
  height: 32px;
  font-size: 17px;
}
@media (max-width: 768px) {
  .work-tab-bar {
    padding-inline: 10px;
  }
  .work-tab-select {
    max-width: 170px;
    padding: 0 10px;
  }
}
</style>
