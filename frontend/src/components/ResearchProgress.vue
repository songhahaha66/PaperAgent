<template>
  <section class="research-progress" aria-label="任务与验收">
    <header class="progress-overview">
      <strong>{{ isZh ? '研究任务' : 'Research tasks' }}</strong>
      <span class="accepted-total"
        >{{ accepted }}/{{ tasks.length }} {{ isZh ? '已完成检查' : 'checked' }}</span
      >
    </header>
    <p v-if="!tasks.length" class="empty-progress">
      {{
        snapshot
          ? isZh
            ? '完成题目分析后，研究任务会出现在这里。'
            : 'Tasks will appear after problem analysis.'
          : isZh
            ? '正在读取研究进度…'
            : 'Loading research progress…'
      }}
    </p>
    <ol v-else class="research-task-list">
      <li
        v-for="task in tasks"
        :key="task.task_id"
        :data-task="task.task_id"
        :data-state="taskState(task, status)"
      >
        <div class="task-content">
          <div class="task-heading">
            <strong>{{ taskName(task) }}</strong>
            <span class="task-state">{{ taskStatus(task) }}</span>
          </div>
          <p class="task-reason">{{ taskNextStep(task) }}</p>
          <p v-for="issue in issuesFor(task)" :key="issue.id" class="step-error" role="alert">
            {{ issue.message }}
            <button @click="emit('issue', issue.id)">{{ isZh ? '处理' : 'Address' }}</button>
          </p>
          <button
            v-for="r in snapshot?.results.filter((r) => r.subproblem_id === task.task_id) || []"
            :key="r.id"
            @click="emit('object', { type: 'result', resultType: 'registered', id: r.id })"
          >
            {{ r.name }}
          </button>
          <details class="task-details">
            <summary>
              {{ isZh ? '运行记录' : 'Run records' }}<span aria-hidden="true">⌄</span>
            </summary>
            <div class="task-detail-body">
              <p>
                {{ isZh ? '当前版本尝试' : 'Attempts in this revision' }}
                {{ task.progress.attempts }} / {{ task.progress.attempt_limit }}
              </p>
              <p v-if="task.dependencies.length">
                {{ isZh ? '依赖 ' : 'Depends on ' }}{{ task.dependencies.join('、') }}
              </p>
              <p v-if="task.progress.waiting_for.length">
                {{ isZh ? '等待检查 ' : 'Awaiting checks for '
                }}{{ task.progress.waiting_for.join('、') }}
              </p>
              <p v-if="task.kind === 'verify'">
                {{ isZh ? '独立核验任务' : 'Independent verification' }}
              </p>
              <p v-if="issuesFor(task).length">
                {{ isZh ? '问题记录 ' : 'Issue records '
                }}{{
                  issuesFor(task)
                    .map((i) => i.id)
                    .join('、')
                }}
              </p>
            </div>
          </details>
        </div>
      </li>
    </ol>
    <details
      v-if="snapshot?.requirements?.length || snapshot?.assumptions?.length"
      class="task-details"
    >
      <summary>{{ isZh ? '研究计划与假设' : 'Plan and assumptions' }}</summary>
      <p v-for="r in snapshot?.requirements || []" :key="r.id">
        {{ r.statement }}<small v-if="r.source_quote">（{{ r.source_quote }}）</small>
      </p>
      <p v-for="a in snapshot?.assumptions || []" :key="a.id">
        {{ a.statement }}（{{ a.status }}）
      </p>
    </details>
    <section v-if="snapshot?.delivery" class="delivery-progress">
      <header>
        <strong>{{ isZh ? '论文交付' : 'Paper delivery' }}</strong
        ><span class="delivery-state">{{
          snapshot.delivery.ready
            ? isZh
              ? '研究已验收'
              : 'Research accepted'
            : isZh
              ? '待验收'
              : 'Pending acceptance'
        }}</span>
      </header>
      <p v-for="(stage, key) in snapshot.delivery_stages" :key="key">
        {{ stageName(String(key)) }} · {{ stageLabel(stage.status)
        }}<span v-if="stage.note"> · {{ stage.note }}</span>
      </p>
      <p v-if="snapshot.artifacts.length">
        {{ isZh ? '已发布：' : 'Published: '
        }}{{ snapshot.artifacts.map((path) => paperLabel(path, isZh) || path).join('、') }}
      </p>
      <p v-if="writingBlockers.length">
        {{ writingBlockers.length }}
        {{
          isZh
            ? '项论文问题需要处理，请在论文中处理。'
            : 'paper issues to address; open the paper to address them.'
        }}
      </p>
    </section>
  </section>
</template>
<script setup lang="ts">
import { computed } from 'vue'
import { useStudioCopy } from '@/lang/studio'
import { paperLabel } from '@/utils/workFiles'
import type { WorkObject } from '@/stores/workPanels'
import type { ResearchSnapshot } from '@/types/research'
import {
  taskState,
  taskName,
  taskLabel,
  deliveryLabels,
  orderedTasks,
  type ResearchTask,
} from '@/utils/researchProgress'
const props = defineProps<{ snapshot: ResearchSnapshot | null; workStatus?: string }>()
const emit = defineEmits<{ object: [object: WorkObject]; issue: [id: string] }>()
const { isZh } = useStudioCopy()
const tasks = computed(() => orderedTasks(props.snapshot?.tasks || []))
const status = computed(() => props.workStatus || props.snapshot?.work_status || 'created')
const accepted = computed(
  () => tasks.value.filter((t) => taskState(t, status.value) === 'accepted').length,
)
const writingBlockers = computed(
  () => props.snapshot?.delivery?.blockers.filter((b) => b.action === 'rewrite') || [],
)
const issuesFor = (task: ResearchTask) =>
  props.snapshot?.issues?.filter(
    (i) =>
      task.progress.issue_ids.includes(i.id) &&
      !['resolved', 'dismissed', 'superseded'].includes(i.status),
  ) || []
const taskStatus = (task: ResearchTask) => {
  const count = issuesFor(task).length
  return count
    ? isZh.value
      ? `有 ${count} 项问题需要处理`
      : `${count} issues to address`
    : taskLabel(taskState(task, status.value), isZh.value)
}
const taskNextStep = (task: ResearchTask) => {
  const state = taskState(task, status.value)
  if (state === 'accepted') {
    const count =
      props.snapshot?.results.filter((r) => r.subproblem_id === task.task_id).length || 0
    return count
      ? isZh.value
        ? `${count} 项结果已通过检查，可在下方打开。`
        : `${count} results passed checks; open the paper to address them.`
      : isZh.value
        ? '这部分已完成检查。'
        : 'Checks for this task are complete.'
  }
  if (task.reason?.trim()) return task.reason
  const next = {
    running: ['正在计算与检查结果。', 'Calculations and checks are running.'],
    interrupted: [
      '进度已保存，继续研究后恢复这部分。',
      'Progress saved. Continue research to resume this task.',
    ],
    waiting: [
      '前置任务完成检查后，再继续这部分。',
      'Waiting for prerequisite tasks to pass their checks.',
    ],
    rework: [
      '根据检查意见重新计算，再检查结果。',
      'Recompute using the findings, then check again.',
    ],
    failed: ['处理这一步的问题后重试。', 'Address this step’s findings before retrying.'],
    needs_plan: ['先调整研究计划，再继续计算。', 'Revise the research plan before continuing.'],
    blocked: ['先修正相关假设或输入。', 'Correct the related assumptions or inputs first.'],
    review: ['结果已生成，等待进一步检查。', 'Results are ready for further checks.'],
    writing: ['结果将用于论文撰写。', 'These results will be used in the paper.'],
    ready: ['前置条件已满足，等待执行。', 'Prerequisites met; waiting to run.'],
    pending: ['尚未开始，按研究计划推进。', 'Not started; following the research plan.'],
  }
  return next[state as keyof typeof next]?.[isZh.value ? 0 : 1] || ''
}
const stageName = (key: string) =>
  deliveryLabels[key as keyof typeof deliveryLabels]?.[isZh.value ? 0 : 1] || key
const stageLabel = (state: string) =>
  ({
    pending: ['待继续', 'Pending'],
    in_progress: ['进行中', 'Running'],
    completed: ['完成', 'Completed'],
    blocked: ['需处理', 'Blocked'],
    interrupted: ['已中断，待恢复', 'Interrupted, resume needed'],
  })[state]?.[isZh.value ? 0 : 1] || state
</script>
<style scoped>
.step-error {
  color: var(--pa-danger);
}
button {
  border: 1px solid var(--pa-line);
  background: var(--pa-surface);
  color: var(--pa-accent);
  padding: 5px 9px;
  border-radius: 6px;
  cursor: pointer;
  margin: 4px;
}

.research-progress {
  padding: 16px;
  color: var(--td-text-color-primary);
}
.progress-overview {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  margin-bottom: 12px;
  font-size: 13px;
  line-height: 22px;
}
.progress-overview strong {
  font-weight: 600;
}
.accepted-total {
  font-size: 12px;
  color: var(--td-text-color-secondary);
  font-variant-numeric: tabular-nums;
}
.research-task-list {
  list-style: none;
  margin: 0;
  padding: 0;
  display: flex;
  flex-direction: column;
  gap: 12px;
}
.research-task-list > li {
  padding: 14px;
  border: 1px solid var(--pa-line);
  border-radius: var(--pa-radius-card);
  background: var(--pa-surface);
  overflow-wrap: anywhere;
}
.task-heading {
  display: flex;
  align-items: center;
  justify-content: space-between;
  flex-wrap: wrap;
  gap: 8px;
  font-size: 13px;
  line-height: 20px;
}
.task-heading strong {
  font-weight: 600;
}
.task-state {
  display: inline-flex;
  align-items: center;
  gap: 5px;
  font-size: 12px;
  color: var(--pa-muted);
}
.task-state::before {
  content: '';
  width: 5px;
  height: 5px;
  border-radius: var(--pa-radius-circle);
  background: var(--pa-surface-active);
}
li[data-state='accepted'] .task-state,
li[data-state='running'] .task-state {
  color: var(--pa-accent);
}
li[data-state='accepted'] .task-state::before,
li[data-state='running'] .task-state::before {
  background: var(--pa-accent);
}
li[data-state='failed'] .task-state,
li[data-state='needs_plan'] .task-state,
li[data-state='review'] .task-state,
li[data-state='rework'] .task-state {
  color: var(--pa-warning);
}
li[data-state='failed'] .task-state::before,
li[data-state='needs_plan'] .task-state::before,
li[data-state='review'] .task-state::before,
li[data-state='rework'] .task-state::before {
  background: var(--pa-warning);
}
p {
  margin: 8px 0;
  font-size: 13px;
  line-height: 1.65;
}

.task-reason {
  color: var(--pa-muted);
  font-size: 12px;
}

.task-details {
  margin-top: 10px;
}
summary {
  list-style: none;
  display: inline-flex;
  align-items: center;
  gap: 6px;
  cursor: pointer;
  font-size: 12px;
  line-height: 20px;
  color: var(--pa-muted);
}
summary::-webkit-details-marker {
  display: none;
}
summary > span {
  color: var(--pa-placeholder);
}
.task-detail-body {
  margin-top: 8px;
  padding-top: 4px;
  border-top: 1px solid var(--pa-line);
  color: var(--pa-muted);
}
.task-detail-body p {
  font-size: 12px;
  white-space: pre-wrap;
}

.delivery-progress {
  margin-top: 16px;
  padding: 14px;
  border: 1px solid var(--pa-line);
  border-radius: var(--pa-radius-card);
  background: var(--pa-surface);
  overflow-wrap: anywhere;
}
.delivery-progress > header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  flex-wrap: wrap;
  gap: 8px;
  font-size: 13px;
  line-height: 20px;
}
.delivery-progress > header strong {
  font-weight: 600;
}
.delivery-state {
  color: var(--pa-muted);
  font-size: 12px;
}

.empty-progress {
  padding: 12px 0;
  color: var(--pa-muted);
}
</style>
