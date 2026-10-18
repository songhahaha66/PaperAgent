<template>
  <section class="research-progress" aria-label="任务与验收">
    <header class="progress-overview">
      <div>
        <strong class="accepted-total"
          >{{ accepted }}<span>/ {{ tasks.length }}</span></strong
        ><span class="accepted-caption">{{ isZh ? '任务已验收' : 'tasks accepted' }}</span>
      </div>
      <span v-if="snapshot?.research_revision" class="revision"
        >{{ isZh ? '研究版本' : 'Revision' }} {{ snapshot.research_revision }}</span
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
        <span class="task-node" aria-hidden="true">{{
          taskState(task, status) === 'accepted' ? '✓' : ''
        }}</span>
        <div class="task-content">
          <div class="task-heading">
            <strong>{{ task.task_id }}</strong
            ><span class="task-version">v{{ task.revision }}</span
            ><span class="task-state">{{ taskLabel(taskState(task, status), isZh) }}</span>
          </div>
          <p class="task-objective">{{ task.objective }}</p>
          <div class="task-meta">
            <span v-if="task.dependencies.length"
              >{{ isZh ? '依赖 ' : 'Depends on ' }}{{ task.dependencies.join('、') }}</span
            >
            <span v-else>{{ isZh ? '独立任务' : 'Independent task' }}</span>
            <span v-if="task.progress.waiting_for.length"
              >{{ isZh ? '等待 ' : 'Waiting for ' }}{{ task.progress.waiting_for.join('、') }}
              {{ isZh ? '验收' : 'acceptance' }}</span
            >
            <span v-if="task.kind === 'verify'">{{
              isZh ? '独立核验任务' : 'Independent verification'
            }}</span>
            <span v-if="task.progress.rework">{{
              isZh ? '返工后重新验收' : 'Fresh acceptance after rework'
            }}</span>
          </div>
          <details v-if="task.progress.attempts || issuesFor(task).length" class="task-details">
            <summary>
              {{
                issuesFor(task).length
                  ? `${issuesFor(task).length} ${isZh ? '项待处理' : 'findings to review'}`
                  : isZh
                    ? '执行记录'
                    : 'Execution record'
              }}<span aria-hidden="true">⌄</span>
            </summary>
            <div class="task-detail-body">
              <p>
                {{ isZh ? '当前版本尝试' : 'Attempts in this revision' }}
                {{ task.progress.attempts }} / {{ task.progress.attempt_limit }}
              </p>
              <p v-if="task.reason && task.progress.state !== 'accepted'">{{ task.reason }}</p>
              <p v-for="issue in issuesFor(task)" :key="issue.id" class="task-finding">
                {{ issue.message }}
              </p>
            </div>
          </details>
        </div>
      </li>
    </ol>
    <section v-if="snapshot?.delivery" class="delivery-progress">
      <header>
        <span class="delivery-symbol" aria-hidden="true">▤</span
        ><strong>{{ isZh ? '论文交付' : 'Paper delivery' }}</strong
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
        {{ isZh ? '已发布：' : 'Published: ' }}{{ snapshot.artifacts.join('、') }}
      </p>
      <details v-if="writingBlockers.length" class="task-details">
        <summary>
          {{ writingBlockers.length }} {{ isZh ? '项论文修订意见' : 'paper revision findings'
          }}<span aria-hidden="true">⌄</span>
        </summary>
        <div class="task-detail-body">
          <p v-for="(blocker, i) in writingBlockers" :key="i">{{ blocker.message }}</p>
        </div>
      </details>
    </section>
  </section>
</template>
<script setup lang="ts">
import { computed } from 'vue'
import { useStudioCopy } from '@/lang/studio'
import type { ResearchSnapshot } from '@/types/research'
import {
  taskState,
  taskLabel,
  deliveryLabels,
  orderedTasks,
  type ResearchTask,
} from '@/utils/researchProgress'
const props = defineProps<{ snapshot: ResearchSnapshot | null; workStatus?: string }>()
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
  props.snapshot?.issues?.filter((i) => task.progress.issue_ids.includes(i.id)) || []
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
.research-progress {
  padding: 0 24px 28px;
  color: #29352e;
}
.progress-overview {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 20px 0 26px;
  border-top: 1px solid #e8ede9;
  gap: 16px;
}
.progress-overview > div {
  display: flex;
  align-items: baseline;
  gap: 12px;
}
.accepted-total {
  font-size: 26px;
  font-weight: 600;
  letter-spacing: -1px;
  font-variant-numeric: tabular-nums;
}
.accepted-total span {
  margin-left: 4px;
  font-size: 16px;
  font-weight: 400;
  color: #a1aaa4;
}
.accepted-caption,
.revision {
  font-size: 12px;
  color: #7a857e;
}
.research-task-list {
  list-style: none;
  margin: 0;
  padding: 0;
}
li {
  position: relative;
  display: grid;
  grid-template-columns: 22px minmax(0, 1fr);
  gap: 12px;
  padding: 0 0 26px;
  overflow-wrap: anywhere;
}
li:not(:last-child)::before {
  content: '';
  position: absolute;
  top: 23px;
  bottom: 1px;
  left: 10px;
  border-left: 1px solid #e4eae5;
}
.task-node {
  display: grid;
  place-items: center;
  width: 20px;
  height: 20px;
  border: 1px solid #d9e0db;
  border-radius: 50%;
  font-size: 11px;
  color: #7d8980;
  background: #fff;
}
li[data-state='accepted'] .task-node {
  background: #e9f5ee;
  color: #16865d;
  border-color: #cfe9da;
}
li[data-state='running'] .task-node {
  border: 5px solid #d9eee3;
  background: #16865d;
  box-sizing: border-box;
}
li[data-state='failed'] .task-node,
li[data-state='needs_plan'] .task-node,
li[data-state='review'] .task-node {
  border-color: #e2c5a4;
  background: #fff9f0;
}
.task-heading {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 8px;
  min-height: 22px;
  font-size: 13px;
}
.task-heading strong {
  font-weight: 600;
}
.task-version {
  font-size: 11px;
  color: #9aa49d;
}
.task-state {
  margin-left: auto;
  font-size: 11px;
  padding: 3px 7px;
  border-radius: 5px;
  background: #f1f4f1;
  color: #748078;
  line-height: 1.4;
}
li[data-state='accepted'] .task-state {
  background: #edf6f0;
  color: #16865d;
}
li[data-state='running'] .task-state {
  color: #16865d;
  background: #edf6f0;
}
li[data-state='failed'] .task-state,
li[data-state='needs_plan'] .task-state,
li[data-state='review'] .task-state {
  color: #a66b29;
  background: #fcf5e9;
}
p {
  margin: 8px 0;
  font-size: 13px;
  line-height: 1.7;
}
.task-objective {
  color: #49554d;
}
.task-meta {
  display: flex;
  flex-wrap: wrap;
  gap: 4px 12px;
  font-size: 11px;
  color: #758078;
  line-height: 1.6;
}
.task-details {
  margin-top: 10px;
}
summary {
  list-style: none;
  display: inline-flex;
  align-items: center;
  gap: 8px;
  cursor: pointer;
  font-size: 12px;
  color: #7a897e;
}
summary::-webkit-details-marker {
  display: none;
}
summary > span {
  color: #9ca69e;
}
.task-detail-body {
  margin-top: 10px;
  padding: 4px 12px;
  border-left: 2px solid #e5ebe6;
  color: #768077;
}
.task-detail-body p {
  font-size: 12px;
  white-space: pre-wrap;
}
.task-finding {
  padding: 8px 0;
  border-bottom: 1px solid #eef1ed;
}
.task-finding:last-child {
  border: 0;
}
.delivery-progress {
  border-top: 1px solid #e8ede9;
  padding-top: 20px;
  overflow-wrap: anywhere;
}
.delivery-progress > header {
  display: flex;
  align-items: center;
  gap: 8px;
  font-size: 13px;
}
.delivery-symbol {
  color: #94a298;
}
.delivery-state {
  margin-left: auto;
  color: #9aa49c;
  font-size: 11px;
}
.empty-progress {
  padding-bottom: 24px;
  color: #758078;
}
</style>
