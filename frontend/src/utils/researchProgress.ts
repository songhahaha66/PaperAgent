import type { ResearchSnapshot, ResearchActivity, ResearchQuestion } from '@/types/research'

export const researchRunning = (status?: string) =>
  ['in_progress', 'running', 'processing'].includes(status || '')
export const callRunning = (row: ResearchActivity) =>
  !row.finished_at && ['running', 'sent', 'http_response'].includes(row.status)
export const currentActivity = (runtime: NonNullable<ResearchSnapshot['runtime']>) => {
  const rows = [...(runtime.actions || []), ...(runtime.models || [])]
  const current =
    rows.filter(callRunning).sort((a, b) => (b.started_at || 0) - (a.started_at || 0))[0] || null
  // An old convenience projection cannot revive an operation already finished
  // in the actual rows. Older snapshots may carry only `current`.
  return (
    current ||
    (runtime.current &&
    !rows.some((row) => row.id === runtime.current?.id) &&
    callRunning(runtime.current)
      ? runtime.current
      : null)
  )
}

export function researchExecution(snapshot: ResearchSnapshot | null, workStatus?: string) {
  const status = workStatus || snapshot?.work_status || 'created'
  const running = researchRunning(status)
  return {
    status,
    running,
    interrupted: ['paused', 'failed', 'cancelled'].includes(status),
    waiting: status === 'waiting',
    current: running && snapshot?.runtime ? currentActivity(snapshot.runtime) : null,
  }
}

function sourceQuestionNumber(question: ResearchQuestion) {
  const pattern =
    /^(?:问题\s*([0-9０-９一二三四五六七八九十两]+)|第\s*([0-9０-９一二三四五六七八九十两]+)\s*[问题]|question\s+(\d+)\b)/i
  const match = question.title.trim().match(pattern) || question.question.trim().match(pattern)
  if (!match) return null
  const value = (match[1] || match[2] || match[3] || '').replace(/[０-９]/g, (digit) =>
    String(digit.charCodeAt(0) - 0xff10),
  )
  if (/^\d+$/.test(value)) return Number(value)
  const digits = '零一二三四五六七八九'
  const digit = (text: string) => (text === '两' ? 2 : digits.indexOf(text))
  const parts = value.split('十')
  if (parts.length === 2)
    return (parts[0] ? digit(parts[0]) : 1) * 10 + (parts[1] ? digit(parts[1]) : 0)
  return value.length === 1 ? digit(value) : null
}

export function orderedQuestions(questions: ResearchQuestion[]) {
  // Display only: never renumber IDs, mutate saved questions, or reorder the
  // Agent's model-visible history/tools to fix a navigation issue.
  return questions
    .filter((question) => !question.retired)
    .map((question, index) => ({ question, index, order: sourceQuestionNumber(question) }))
    .sort((a, b) => (a.order ?? Infinity) - (b.order ?? Infinity) || a.index - b.index)
    .map(({ question }) => question)
}
export function activityHeadline(row: ResearchActivity, zh = true) {
  if (row.kind === 'model')
    return row.status === 'http_response'
      ? zh
        ? '正在接收模型响应'
        : 'Receiving model response'
      : zh
        ? '等待模型响应'
        : 'Waiting for model response'
  if (row.capability === 'experiments.execute') return zh ? '正在处理实验' : 'Working on experiment'
  return row.label
}
export const executionLabel = (status: string) =>
  ({
    planned: '待执行',
    pending: '待执行',
    running: '执行中',
    sent: '等待模型响应',
    http_response: '接收模型响应',
    completed: '完成',
    done: '完成',
    failed: '失败',
    interrupted: '已中断',
    truncated: '生成截断',
    cancelled: '已取消',
    success: '成功',
    error: '失败',
    waiting: '等待补充信息',
    recorded: '记录已保存',
  })[status] || '状态未记录'
export function activityStatus(row: ResearchActivity) {
  if (callRunning(row) && row.kind !== 'model')
    return row.phase === 'delivery' ? '同步文件' : row.phase === 'preparing' ? '准备中' : '进行中'
  return executionLabel(row.status)
}
export function executionDuration(row: ResearchActivity, now: number, running: boolean) {
  if (!row.started_at) return '时间未记录'
  const end = row.finished_at || (running && callRunning(row) ? now : null)
  if (end == null) return '结束时间未记录'
  const seconds = Math.max(0, Math.floor(end - row.started_at))
  return seconds < 60 ? `${seconds} 秒` : `${Math.floor(seconds / 60)} 分 ${seconds % 60} 秒`
}

export type ResearchTask = NonNullable<ResearchSnapshot['experiments']>[number]

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

export function taskLabel(state: string, zh: boolean) {
  const labels: Record<string, string[]> = {
    completed: ['执行完成', 'Completed'],
    accepted: ['运行已结束', 'Run finished'],
    running: ['执行中', 'Running'],
    interrupted: ['已中断，待恢复', 'Interrupted, resume needed'],
    needs_plan: ['方法待调整', 'Plan change needed'],
    failed: ['执行未成功', 'Failed, retry needed'],
    waiting: ['等待依赖结果', 'Waiting for prerequisite results'],
    blocked: ['需修正假设或输入', 'Assumptions or inputs need correction'],
    rework: ['待返工', 'Rework needed'],
    ready: ['可执行', 'Ready'],
    review: ['等待进一步检查', 'Further checks needed'],
    pending: ['待执行', 'Pending'],
    writing: ['等待论文撰写', 'Pending writing'],
  }
  return labels[state]?.[zh ? 0 : 1] || (zh ? '状态未记录' : 'Status unavailable')
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
  const execution = researchExecution(snapshot, workStatus)
  if (execution.waiting) return zh ? '等待补充研究信息' : 'Waiting for research input'
  if (execution.current) return activityHeadline(execution.current, zh)
  if (execution.running) return zh ? '研究执行中' : 'Research in progress'
  if (zh && snapshot?.delivery_summary?.state === 'incomplete')
    return snapshot.delivery_summary.label
  if (!snapshot && workStatus !== 'created')
    return zh ? '正在读取研究进度' : 'Loading research progress'
  if (
    !snapshot?.experiments?.length &&
    !snapshot?.questions?.length &&
    !snapshot?.artifacts?.length
  ) {
    if (workStatus === 'waiting') return zh ? '等待补充研究信息' : 'Waiting for research input'
    if (workStatus === 'in_progress') return zh ? '正在分析问题' : 'Preparing research tasks'
    if (workStatus === 'failed')
      return zh ? '执行失败，进度已保留' : 'Execution failed, progress saved'
    if (['paused', 'cancelled'].includes(workStatus))
      return zh ? '研究已暂停，进度已保留' : 'Research paused, progress saved'
    return zh ? '等待建立研究任务' : 'Waiting for research tasks'
  }
  const running = (snapshot.experiments || []).filter((t) => taskState(t, workStatus) === 'running')
  if (running.length && researchRunning(workStatus))
    return zh ? '正在处理实验' : 'Working on experiments'
  if (['paused', 'failed', 'cancelled'].includes(workStatus)) {
    for (const [key, label] of Object.entries(deliveryLabels)) {
      if (snapshot.delivery_stages?.[key]?.status === 'blocked')
        return `${label[zh ? 0 : 1]}${zh ? '未完成，进度已保留' : ' incomplete, progress saved'}`
    }
    return zh ? '研究已暂停，进度已保留' : 'Research paused, progress saved'
  }
  for (const [key, label] of Object.entries(deliveryLabels)) {
    if (snapshot.delivery_stages?.[key]?.status === 'in_progress') return label[zh ? 0 : 1]
    if (snapshot.delivery_stages?.[key]?.status === 'blocked')
      return `${label[zh ? 0 : 1]}${zh ? '需处理' : ' needs attention'}`
  }
  if (snapshot.artifacts.length)
    return zh ? '论文产物已保存，可查看' : 'Paper artifacts saved and available'
  const questions = snapshot.questions || []
  const missingJudgments = questions.filter((question) => !question.judgment).length
  if (snapshot.results.length && missingJudgments)
    return missingJudgments === questions.length
      ? zh
        ? '已有实验结果，问题尚未形成判断'
        : 'Experiment results available; question judgments not yet recorded'
      : zh
        ? '已有实验结果，部分问题尚无判断'
        : 'Experiment results available; some question judgments are missing'
  if (questions.length && !missingJudgments)
    return zh
      ? '已记录问题判断，论文产物尚不可用'
      : 'Question judgments recorded; paper artifacts unavailable'
  if (snapshot.results.length)
    return zh
      ? '已有实验结果，论文产物尚不可用'
      : 'Experiment results available; paper artifacts unavailable'
  return zh ? '等待实验结果与问题判断' : 'Awaiting experiment results and question judgments'
}

export function taskName(task: Pick<ResearchTask, 'task_id' | 'objective'>) {
  return task.objective?.trim() || task.task_id
}

export function researchQuestions(snapshot: ResearchSnapshot | null) {
  return snapshot?.questions || []
}

export const researchActionName = (action: string) =>
  ({
    experiment: '执行实验',
    inspect: '查看材料',
    literature: '补充文献',
    ask: '补充必要信息',
    write: '撰写论文',
    pause: '暂停研究',
    tool: '调用研究能力',
  })[action] || '研究判断'
