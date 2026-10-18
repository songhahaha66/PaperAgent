<template>
  <details class="research-progress" :open="expanded">
    <summary>
      <strong>{{ isZh ? '研究任务' : 'Research tasks' }}</strong>
      <span v-if="tasks.length"
        >{{ accepted }}/{{ tasks.length }} {{ isZh ? '项已验收' : 'accepted' }}</span
      >
      <span v-else>{{
        snapshot
          ? isZh
            ? '尚未建立任务'
            : 'Tasks not created yet'
          : isZh
            ? '正在读取已提交的研究进度'
            : 'Loading committed progress'
      }}</span>
      <span v-if="snapshot?.research_revision"
        >{{ isZh ? '研究版本' : 'Revision' }} {{ snapshot.research_revision }}</span
      >
    </summary>
    <p v-if="!tasks.length">
      {{ isZh ? '研究任务将在题目分析完成后建立。' : 'Tasks will appear after problem analysis.' }}
    </p>
    <ol v-else class="research-task-list">
      <li
        v-for="task in tasks"
        :key="task.task_id"
        :data-task="task.task_id"
        :data-state="taskState(task, status)"
      >
        <div class="task-heading">
          <strong>{{ task.task_id }} · v{{ task.revision }}</strong>
          <span class="task-state">{{ taskLabel(taskState(task, status), isZh) }}</span>
          <small v-if="task.kind === 'verify'">{{
            isZh ? '独立核验任务' : 'Independent verification'
          }}</small>
          <small v-if="task.progress.rework">{{
            isZh ? '需重新执行并验收' : 'Fresh execution and acceptance required'
          }}</small>
        </div>
        <p>{{ task.objective }}</p>
        <small
          >{{ isZh ? '依赖：' : 'Dependencies: '
          }}{{
            task.dependencies.join('、') || (isZh ? '无，可独立执行' : 'None, independent task')
          }}</small
        >
        <small v-if="task.progress.waiting_for.length"
          >{{ isZh ? '等待：' : 'Waiting for: ' }}{{ task.progress.waiting_for.join('、') }}</small
        >
        <small
          >{{ isZh ? '当前版本尝试：' : 'Attempts in this revision: '
          }}{{ task.progress.attempts }}/{{ task.progress.attempt_limit }}</small
        >
        <details v-if="issuesFor(task).length" class="task-blocker">
          <summary>
            {{ issuesFor(task).length }} {{ isZh ? '项阻塞意见' : 'blocking findings' }}
          </summary>
          <p v-for="issue in issuesFor(task)" :key="issue.id">{{ issue.message }}</p>
        </details>
        <small v-if="task.reason && task.progress.state !== 'accepted'">{{ task.reason }}</small>
      </li>
    </ol>
    <div v-if="snapshot?.delivery" class="delivery-progress">
      <strong>{{
        snapshot.delivery.ready
          ? isZh
            ? '研究验收通过'
            : 'Research accepted'
          : isZh
            ? '研究交付待验收'
            : 'Delivery checks pending'
      }}</strong>
      <p v-for="(stage, key) in snapshot.delivery_stages" :key="key">
        {{ stageName(String(key)) }} · {{ stageLabel(stage.status)
        }}<span v-if="stage.note"> · {{ stage.note }}</span>
      </p>
      <p v-if="snapshot.artifacts.length">
        {{ isZh ? '已发布：' : 'Published: ' }}{{ snapshot.artifacts.join('、') }}
      </p>
      <details v-if="writingBlockers.length">
        <summary>
          {{ writingBlockers.length }} {{ isZh ? '项论文修订意见' : 'paper revision findings' }}
        </summary>
        <p v-for="(blocker, i) in writingBlockers" :key="i">{{ blocker.message }}</p>
      </details>
    </div>
  </details>
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
const props = defineProps<{
  snapshot: ResearchSnapshot | null
  workStatus?: string
  expanded?: boolean
}>()
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
  padding: 12px 16px;
  border-top: 1px solid var(--studio-border, #e5e7eb);
  font-size: 13px;
}
summary {
  display: flex;
  flex-wrap: wrap;
  gap: 8px 16px;
  cursor: pointer;
}
.research-task-list {
  list-style: none;
  padding: 0;
  display: grid;
  gap: 10px;
  max-height: 42vh;
  overflow: auto;
}
li {
  border-left: 3px solid #b7bdc8;
  padding: 8px 12px;
  background: var(--studio-panel, #f7f8fa);
  overflow-wrap: anywhere;
}
li[data-state='accepted'] {
  border-color: #16845b;
}
li[data-state='running'] {
  border-color: #245de6;
}
li[data-state='needs_plan'],
li[data-state='failed'],
li[data-state='review'],
li[data-state='rework'] {
  border-color: #bc6a20;
}
.task-heading {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  align-items: center;
}
p {
  margin: 6px 0;
}
li > small {
  display: block;
  margin-top: 4px;
}
.task-state {
  font-weight: 600;
}
.task-blocker {
  color: #995415;
}
.delivery-progress {
  margin-top: 12px;
  overflow-wrap: anywhere;
}
</style>
