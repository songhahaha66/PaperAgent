import type { ResearchSnapshot } from '@/types/research'

export type ResearchTask = NonNullable<ResearchSnapshot['tasks']>[number]

export function orderedTasks(tasks: ResearchTask[]) {
  const byId = new Map(tasks.map((task) => [task.task_id, task]))
  const seen = new Set<string>(),
    ordered: ResearchTask[] = []
  function visit(task: ResearchTask) {
    if (seen.has(task.task_id)) return
    seen.add(task.task_id)
    task.dependencies.forEach((id) => {
      const dep = byId.get(id)
      if (dep) visit(dep)
    })
    ordered.push(task)
  }
  tasks.forEach(visit)
  return ordered
}

export function taskState(task: ResearchTask, workStatus: string) {
  return task.progress.state === 'running' && ['paused', 'failed', 'cancelled'].includes(workStatus)
    ? 'interrupted'
    : task.progress.state
}

export function taskLabel(state: ResearchTask['progress']['state'], zh: boolean) {
  const labels = {
    accepted: ['已验收', 'Accepted'],
    running: ['执行中', 'Running'],
    interrupted: ['已中断，待恢复', 'Interrupted, resume needed'],
    needs_plan: ['需调整计划', 'Plan change needed'],
    failed: ['执行失败，待重试', 'Failed, retry needed'],
    waiting: ['等待上游验收', 'Waiting for dependencies'],
    blocked: ['前提待修订', 'Premise revision needed'],
    rework: ['待返工', 'Rework needed'],
    ready: ['可执行', 'Ready'],
    review: ['待复核验收', 'Review needed'],
    pending: ['待执行', 'Pending'],
    writing: ['等待论文撰写', 'Pending writing'],
  }
  return labels[state][zh ? 0 : 1]
}

export const deliveryLabels = {
  write: ['论文撰写', 'Writing'],
  validate: ['论文校验', 'Paper validation'],
  render: ['论文发布', 'Paper publication'],
  export: ['格式导出', 'Export'],
} as const

export function researchHeadline(
  snapshot: ResearchSnapshot | null,
  workStatus: string,
  zh: boolean,
) {
  if (!snapshot && workStatus !== 'created')
    return zh ? '正在读取研究进度' : 'Loading research progress'
  if (!snapshot?.tasks?.length) {
    if (workStatus === 'waiting') return zh ? '等待确认研究方案' : 'Waiting for plan confirmation'
    if (workStatus === 'in_progress')
      return zh ? '正在分析并建立研究任务' : 'Preparing research tasks'
    if (['paused', 'failed', 'cancelled'].includes(workStatus))
      return zh ? '研究已停止，等待恢复' : 'Research stopped, resume needed'
    return zh ? '等待建立研究任务' : 'Waiting for research tasks'
  }
  const running = snapshot.tasks.filter((t) => taskState(t, workStatus) === 'running')
  if (running.length)
    return `${zh ? '正在执行' : 'Running'} ${running.map((t) => t.task_id).join('、')}`
  if (['paused', 'failed', 'cancelled'].includes(workStatus))
    return zh ? '研究已暂停，进度已保留' : 'Research paused, progress saved'
  for (const [key, label] of Object.entries(deliveryLabels)) {
    if (snapshot.delivery_stages?.[key]?.status === 'in_progress') return label[zh ? 0 : 1]
    if (snapshot.delivery_stages?.[key]?.status === 'blocked')
      return `${label[zh ? 0 : 1]}${zh ? '需处理' : ' needs attention'}`
  }
  if (!snapshot.delivery?.ready)
    return zh ? '研究任务待执行或验收' : 'Research tasks pending execution or acceptance'
  if (snapshot.artifacts.length)
    return zh ? '论文已发布，请查看交付物' : 'Paper published, review deliverables'
  return zh ? '研究验收通过，等待生成论文' : 'Research accepted, preparing paper'
}
