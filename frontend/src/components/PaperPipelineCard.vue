<template>
  <div class="paper-pipeline">
    <!-- 阶段进度 -->
    <div class="pipeline-steps">
      <div
        v-for="phase in phases"
        :key="phase.key"
        :class="['pipeline-step', `is-${phase.status}`]"
        :title="phase.note || phase.label"
      >
        <span class="step-dot">
          <t-icon v-if="phase.status === 'completed'" name="check" size="12px" />
          <t-icon v-else-if="phase.status === 'blocked'" name="close" size="12px" />
          <t-loading v-else-if="phase.status === 'in_progress' && running" size="12px" />
        </span>
        <span class="step-label">{{ phase.label }}</span>
      </div>
    </div>

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
      <div v-for="sp in subproblems" :key="sp.id" :class="['subproblem-row', `is-${sp.status}`]">
        <span class="sp-id">{{ sp.id }}</span>
        <span class="sp-text">{{ sp.text }}</span>
        <span v-if="sp.runs" class="sp-runs">
          执行 {{ sp.runs }} 次<template v-if="sp.fails">，失败 {{ sp.fails }}</template>
        </span>
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
    <t-alert v-if="confirm" :theme="waitingConfirm ? 'warning' : 'info'" class="pipeline-confirm">
      <template #title>{{ waitingConfirm ? '请确认建模方案' : '建模方案' }}</template>
      <template #message>
        <MarkdownRenderer :content="confirm" />
        <p v-if="waitingConfirm" class="confirm-hint">
          回复“确认”继续求解，或直接写下修改意见重新设计。
        </p>
      </template>
    </t-alert>

    <t-alert v-if="error" theme="error" :message="error" class="pipeline-error" />

    <div v-if="result" class="pipeline-section pipeline-result">
      <MarkdownRenderer :content="result" />
    </div>

    <!-- 原始事件（排查用） -->
    <div class="pipeline-raw">
      <t-link theme="default" size="small" @click="showRaw = !showRaw">
        {{ showRaw ? '收起事件明细' : `事件明细（${events.length}）` }}
      </t-link>
      <ul v-if="showRaw" class="raw-list">
        <li v-for="(block, i) in events" :key="i">
          <code>{{ block.type }}</code> {{ asText(block.content) }}
        </li>
      </ul>
    </div>
  </div>
</template>

<script setup lang="ts">
import { computed, ref } from 'vue'
import MarkdownRenderer from '@/components/MarkdownRenderer.vue'
import {
  derivePhases,
  deriveSubproblems,
  asText,
  isPaperEvent,
  type PipelineBlock,
} from '@/utils/paperPipeline'

// superseded：这条消息之后已经有新消息（例如用户已回复确认），卡片不再提示等待确认
const props = defineProps<{ blocks: PipelineBlock[]; streaming?: boolean; superseded?: boolean }>()

const showRaw = ref(false)

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

const phases = computed(() => derivePhases(props.blocks, { done: !!result.value }))
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

.pipeline-steps {
  display: flex;
  flex-wrap: wrap;
  gap: 6px 4px;
}

.pipeline-step {
  display: flex;
  align-items: center;
  gap: 4px;
  padding: 2px 8px 2px 4px;
  border-radius: 12px;
  font-size: 12px;
  color: var(--td-text-color-placeholder, #999);
  background: var(--td-bg-color-secondarycontainer, #f3f3f3);
}

.step-dot {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 16px;
  height: 16px;
  border-radius: 50%;
  border: 1px solid currentColor;
}

.pipeline-step.is-completed {
  color: var(--td-success-color, #2ba471);
}

.pipeline-step.is-completed .step-dot {
  background: var(--td-success-color, #2ba471);
  border-color: var(--td-success-color, #2ba471);
  color: #fff;
}

.pipeline-step.is-in_progress {
  color: var(--td-text-color-primary, #333);
  font-weight: 600;
}

.pipeline-step.is-blocked {
  color: var(--td-error-color, #d54941);
}

.pipeline-current {
  display: flex;
  align-items: center;
  gap: 8px;
  font-size: 13px;
  color: var(--td-text-color-secondary, #666);
}

.section-title {
  font-size: 12px;
  font-weight: 600;
  color: var(--td-text-color-secondary, #666);
  margin-bottom: 4px;
}

.subproblem-row {
  display: flex;
  align-items: baseline;
  gap: 8px;
  padding: 4px 0;
  font-size: 13px;
  border-bottom: 1px solid var(--td-component-stroke, #eee);
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
  color: var(--td-text-color-placeholder, #999);
}

.subproblem-row.is-completed .sp-id {
  color: var(--td-success-color, #2ba471);
}

.subproblem-row.is-failed .sp-id {
  color: var(--td-error-color, #d54941);
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
  color: var(--td-text-color-secondary, #666);
}

.raw-list code {
  font-size: 11px;
}
</style>
