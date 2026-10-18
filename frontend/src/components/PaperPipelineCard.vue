<template>
  <div class="paper-pipeline">
    <details class="pipeline-record" :open="running || waitingConfirm">
      <summary class="record-summary">
        <t-icon name="task" size="14px" />
        <span>{{ running ? '正在研究' : waitingConfirm ? '待确认方案' : '研究过程' }}</span>
        <small v-if="subproblems.length">{{ subproblems.length }} 项任务</small>
        <span class="record-caret" aria-hidden="true">⌄</span>
      </summary>
      <div class="record-content">
        <div v-if="currentText" class="pipeline-current">
          <t-loading v-if="running" size="14px" />
          <span>{{ currentText }}</span>
        </div>

        <!-- 题目分析结果 -->
        <div v-if="analysis" class="pipeline-section">
          <div class="section-title">题目分析</div>
          <MarkdownRenderer :content="analysis" />
        </div>

        <!-- 子问题进度 -->
        <div v-if="subproblems.length" class="pipeline-section">
          <div class="section-title">子问题</div>
          <div
            v-for="sp in subproblems"
            :key="sp.id"
            :class="['subproblem-row', `is-${sp.status}`]"
          >
            <span class="sp-id">{{ sp.id }}</span>
            <span class="sp-text">{{ sp.text }}</span>
            <span :class="['sp-status', `is-${sp.status}`]">{{
              sp.status === 'completed'
                ? '已完成'
                : sp.status === 'failed'
                  ? '需处理'
                  : sp.status === 'pending'
                    ? '待执行'
                    : running
                      ? '进行中'
                      : '已保存'
            }}</span>
          </div>
        </div>

        <!-- 其他阶段事件 -->
        <div v-if="milestones.length" class="pipeline-section">
          <div class="section-title">进展</div>
          <ul class="milestones">
            <li v-for="(item, i) in milestones" :key="i">{{ item }}</li>
          </ul>
        </div>

        <!-- 等待用户确认 -->
        <t-alert
          v-if="confirm"
          :theme="waitingConfirm ? 'warning' : 'info'"
          class="pipeline-confirm"
        >
          <template #title>{{ waitingConfirm ? '请确认建模方案' : '建模方案' }}</template>
          <template #message>
            <MarkdownRenderer :content="confirm" />
            <p v-if="waitingConfirm" class="confirm-hint">
              回复“确认”继续求解，或直接写下修改意见重新设计。
            </p>
          </template>
        </t-alert>

        <!-- 原始事件（排查用） -->
        <details class="pipeline-raw">
          <summary>
            技术详情 <span>{{ events.length }} 条事件</span>
          </summary>
          <div v-for="sp in subproblems.filter((s) => s.runs)" :key="sp.id" class="run-stat">
            {{ sp.id }} · 执行 {{ sp.runs }} 次<span v-if="sp.fails">
              · 失败 {{ sp.fails }} 次</span
            >
          </div>
          <ul class="raw-list">
            <li v-for="(block, i) in events" :key="i">
              <code>{{ block.type }}</code> {{ asText(block.content) }}
            </li>
          </ul>
        </details>
      </div>
    </details>
    <t-alert v-if="error" theme="error" :message="error" class="pipeline-error" />

    <div v-if="result" class="pipeline-section pipeline-result">
      <MarkdownRenderer :content="result" />
    </div>
  </div>
</template>

<script setup lang="ts">
import { computed } from 'vue'
import MarkdownRenderer from '@/components/MarkdownRenderer.vue'
import { deriveSubproblems, asText, isPaperEvent, type PipelineBlock } from '@/utils/paperPipeline'

// superseded：这条消息之后已经有新消息（例如用户已回复确认），卡片不再提示等待确认
const props = defineProps<{ blocks: PipelineBlock[]; streaming?: boolean; superseded?: boolean }>()

const events = computed(() => props.blocks.filter(isPaperEvent))

const lastOf = (type: string) => {
  for (let i = props.blocks.length - 1; i >= 0; i--) {
    if (props.blocks[i]?.type === type) return props.blocks[i]
  }
  return undefined
}

const result = computed(() => asText(lastOf('main_agent_complete')?.content))
const error = computed(() => asText(lastOf('main_agent_error')?.content))
const analysis = computed(() => asText(lastOf('paper_analysis')?.content))
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

const subproblems = computed(() => deriveSubproblems(props.blocks))

const MILESTONE_TYPES = new Set(['paper_consistency', 'paper_validation', 'paper_export'])
const milestones = computed(() =>
  props.blocks.filter((b) => MILESTONE_TYPES.has(b.type)).map((b) => asText(b.content)),
)

const currentText = computed(() => {
  if (!running.value) return ''
  return asText(lastOf('paper_stage')?.content)
})
</script>

<style scoped>
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

.sp-id {
  flex: none;
  font-weight: 600;
  min-width: 28px;
}

.sp-text {
  flex: 1;
  min-width: 0;
  overflow-wrap: anywhere;
}

.sp-runs {
  flex: none;
  font-size: 12px;
  color: var(--td-text-color-placeholder);
}

.subproblem-row.is-completed .sp-id {
  color: var(--td-success-color);
}

.subproblem-row.is-failed .sp-id {
  color: var(--td-error-color);
}

.milestones {
  margin: 0;
  padding-left: 18px;
  font-size: 13px;
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
