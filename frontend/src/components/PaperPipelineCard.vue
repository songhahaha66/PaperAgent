<template>
  <div class="paper-pipeline">
    <div v-if="currentText" class="pipeline-current" role="status">
      <t-loading v-if="running" size="14px" /><span>{{ currentText }}</span>
    </div>
    <details
      class="pipeline-record"
      :open="recordOpen"
      @toggle="recordOpen = ($event.target as HTMLDetailsElement).open"
    >
      <summary class="record-summary">
        <t-icon name="task" size="14px" />
        <span>{{
          running ? '本次操作进行中' : waitingConfirm ? '本次操作等待确认' : '本次操作记录'
        }}</span>
        <small v-if="subproblems.length">{{ subproblems.length }} 项任务</small>
        <span class="record-caret" aria-hidden="true">⌄</span>
      </summary>
      <div class="record-content">
        <!-- 子问题进度 -->
        <div v-if="subproblems.length" class="pipeline-section">
          <div class="section-title">子问题</div>
          <div
            v-for="sp in subproblems"
            :key="sp.id"
            :class="['subproblem-row', `is-${sp.status}`]"
          >
            <span class="sp-text">{{ sp.name }}</span>
            <span :class="['sp-status', `is-${sp.status}`]">{{
              sp.status === 'completed'
                ? '本次计算已提交'
                : sp.status === 'failed'
                  ? '需处理'
                  : running
                    ? '进行中'
                    : '已保存'
            }}</span>
          </div>
        </div>

        <details v-if="confirm && !waitingConfirm">
          <summary>本次建模方案</summary>
          <MarkdownRenderer :content="confirm" />
        </details>
        <!-- 原始事件（排查用） -->
        <details class="pipeline-raw">
          <summary>
            运行记录 <span>{{ events.length }} 条事件</span>
          </summary>
          <div v-for="sp in subproblems.filter((s) => s.runs)" :key="sp.id" class="run-stat">
            {{ sp.id }} · 执行 {{ sp.runs }} 次<span v-if="sp.fails">
              · 失败 {{ sp.fails }} 次</span
            >
          </div>
          <ul class="raw-list">
            <li v-for="(block, i) in events" :key="i">
              {{ recordText(block.content) }}
            </li>
          </ul>
        </details>
      </div>
    </details>
    <t-alert v-if="waitingConfirm" theme="warning" class="pipeline-confirm">
      <template #title>研究需要你的输入</template>
      <template #message
        ><MarkdownRenderer :content="confirm" />
        <p class="confirm-hint">请在输入框补充必要信息或说明研究调整。</p>
        <button v-if="canConfirm" class="pipeline-action" @click="$emit('confirm-plan')">
          继续研究
        </button></template
      >
    </t-alert>
    <button v-if="error" class="pipeline-action" @click="$emit('open-progress')">
      查看运行状态
    </button>
    <t-alert v-if="error" theme="error" :message="error" class="pipeline-error" />

    <div v-if="result" class="pipeline-section pipeline-result">
      <template
        v-if="
          /EvidenceDecision|ResearchIntent|结构化输出.*失败|系统未能生成可执行的下一步操作|下一步操作准备失败/.test(
            result,
          )
        "
        ><p>本次操作未完成。</p>
        <details>
          <summary>查看当时的技术记录</summary>
          <MarkdownRenderer :content="result" /></details></template
      ><MarkdownRenderer v-else :content="result" />
    </div>
  </div>
</template>

<script setup lang="ts">
import { computed, ref } from 'vue'
import type { ResearchSnapshot } from '@/types/research'
import MarkdownRenderer from '@/components/MarkdownRenderer.vue'
import { deriveSubproblems, asText, isPaperEvent, type PipelineBlock } from '@/utils/paperPipeline'

// superseded：这条消息之后已经有新消息（例如用户已回复确认），卡片不再提示等待确认
const props = defineProps<{
  blocks: PipelineBlock[]
  streaming?: boolean
  superseded?: boolean
  canConfirm?: boolean
  tasks?: ResearchSnapshot['experiments']
}>()

defineEmits<{ 'confirm-plan': []; 'open-progress': [] }>()
const recordText = (content: unknown) =>
  typeof content === 'string'
    ? content
    : content && typeof content === 'object'
      ? String(
          (content as Record<string, unknown>).message ||
            (content as Record<string, unknown>).summary ||
            (content as Record<string, unknown>).note ||
            '阶段状态已更新',
        )
      : ''
const recordOpen = ref(false)
const events = computed(() => props.blocks.filter(isPaperEvent))

const lastOf = (type: string) => {
  for (let i = props.blocks.length - 1; i >= 0; i--) {
    if (props.blocks[i]?.type === type) return props.blocks[i]
  }
  return undefined
}

const result = computed(() => asText(lastOf('main_agent_complete')?.content))
const error = computed(() => asText(lastOf('main_agent_error')?.content))
const confirm = computed(() => asText(lastOf('paper_confirm')?.content))

// 方案确认是这次运行的最后一个事件时，说明图停在 interrupt 等用户回复
const waitingConfirm = computed(() => {
  if (props.superseded) return false
  const last = [...props.blocks]
    .reverse()
    .find((b) => b.type !== 'plan_updated' && b.type !== 'file_changed')
  return last?.type === 'paper_confirm'
})

const running = computed(
  () => !!props.streaming && !result.value && !error.value && !waitingConfirm.value,
)

const subproblems = computed(() => deriveSubproblems(props.blocks, props.tasks))

const currentText = computed(() => {
  if (!running.value) return ''
  const text = asText(lastOf('paper_stage')?.content)
  return subproblems.value.reduce(
    (value, task) => value.replace(new RegExp(`^${task.id}(?=[：:·\\s])`), task.name),
    text,
  )
})
</script>

<style scoped>
.pipeline-action {
  border: 0;
  background: transparent;
  color: var(--pa-accent);
  font: inherit;
  text-align: left;
  cursor: pointer;
  padding: 6px 0;
}
.paper-pipeline {
  display: flex;
  flex-direction: column;
  gap: 12px;
  margin-top: 8px;
}

.record-summary {
  display: flex;
  align-items: center;
  gap: 8px;
  width: fit-content;
  padding: 9px 12px;
  border: 1px solid var(--pa-line);
  border-radius: var(--pa-radius-control);
  background: var(--pa-surface-muted);
  color: var(--pa-muted);
  font-size: 12px;
  cursor: pointer;
  list-style: none;
}
.record-summary::-webkit-details-marker {
  display: none;
}
.record-summary small {
  color: var(--pa-placeholder);
  margin-left: 4px;
  font-size: 11px;
}
.record-caret {
  color: var(--pa-placeholder);
}
.record-content {
  display: flex;
  flex-direction: column;
  gap: 14px;
  padding: 16px 0 4px 16px;
  margin-left: 8px;
  border-left: 1px solid var(--pa-line);
}
.pipeline-current {
  display: flex;
  align-items: center;
  gap: 8px;
  font-size: 13px;
  color: var(--td-text-color-secondary);
}

.section-title {
  font-size: 12px;
  font-weight: 600;
  color: var(--td-text-color-secondary);
  margin-bottom: 4px;
}

.subproblem-row {
  display: flex;
  align-items: baseline;
  gap: 8px;
  padding: 4px 0;
  font-size: 13px;
  border-bottom: 1px solid var(--td-component-stroke);
}

.subproblem-row:last-child {
  border-bottom: none;
}

.sp-text {
  flex: 1;
  min-width: 0;
  overflow-wrap: anywhere;
}

.confirm-hint {
  margin: 8px 0 0;
  font-weight: 600;
}

.pipeline-raw {
  font-size: 12px;
}

.raw-list {
  margin: 6px 0 0;
  padding-left: 18px;
  max-height: 240px;
  overflow-y: auto;
  color: var(--td-text-color-secondary);
}

.raw-list code {
  font-size: 11px;
}
.sp-status {
  font-size: 11px;
  color: var(--td-text-color-secondary);
  white-space: nowrap;
}
.sp-status.is-completed {
  color: var(--td-success-color);
}
.sp-status.is-failed {
  color: var(--td-warning-color);
}
.pipeline-raw summary {
  cursor: pointer;
  color: var(--td-text-color-secondary);
}
.pipeline-raw summary > span {
  font-size: 11px;
  color: var(--td-text-color-placeholder);
  margin-left: 8px;
}
.run-stat {
  font-size: 12px;
  margin-top: 8px;
}
.raw-list {
  overflow-wrap: anywhere;
}
</style>
