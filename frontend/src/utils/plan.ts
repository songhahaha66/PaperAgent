import type { PlanData, PlanItem, PlanItemStatus, PlanPhase } from '@/api/workspace'

export const SPEC_DRIVEN_PHASES: PlanPhase[] = [
  { id: 'requirements', title: '需求澄清' },
  { id: 'design', title: '方案设计' },
  { id: 'tasks', title: '任务拆解' },
  { id: 'implement', title: '执行生成' },
  { id: 'verify', title: '验收检查' },
]

export const normalizePlanStatus = (rawStatus: string): PlanItemStatus => {
  const text = rawStatus.toLowerCase()
  if (text.includes('❌') || text.includes('阻塞') || text.includes('blocked') || text.includes('失败')) {
    return 'blocked'
  }
  if (text.includes('⬜') || text.includes('待写') || text.includes('pending') || text.includes('todo')) {
    return 'pending'
  }
  if (text.includes('⏳') || text.includes('进行') || text.includes('progress')) {
    return 'in_progress'
  }
  if (text.includes('✅') || text.includes('完成') || text.includes('complete')) {
    return 'completed'
  }
  return 'pending'
}

export const markdownPlanToData = (content: string): PlanData | null => {
  if (!content.trim()) return null
  const title =
    content
      .split('\n')
      .find((line) => line.trim().startsWith('#'))
      ?.replace(/^#+/, '')
      .trim() || '写作计划'
  const rows = content
    .split('\n')
    .map((line) => line.trim())
    .filter((line) => line.startsWith('|') && !line.includes('---'))
    .map((line) =>
      line
        .replace(/^\|/, '')
        .replace(/\|$/, '')
        .split('|')
        .map((cell) => cell.trim()),
    )
  const items: PlanItem[] = rows.slice(1).map((row, index) => {
    const order = Number.parseInt(row[0] || `${index + 1}`, 10) || index + 1
    const status = normalizePlanStatus(row[2] || '')
    return {
      id: `task-${order}`,
      order,
      title: row[1] || `任务 ${order}`,
      status,
      status_label: row[2]?.replace(/[✅⏳⬜❌]/g, '').trim() || undefined,
      description: row[3] || '',
      phase: inferFallbackPhase(row[1] || ''),
      depends_on: order > 1 ? [`task-${order - 1}`] : [],
      raw_status: row[2] || '',
    }
  })
  const stats = {
    total: items.length,
    completed: items.filter((item) => item.status === 'completed').length,
    in_progress: items.filter((item) => item.status === 'in_progress').length,
    blocked: items.filter((item) => item.status === 'blocked').length,
    pending: items.filter((item) => item.status === 'pending').length,
    progress_percent: items.length
      ? Math.round((items.filter((item) => item.status === 'completed').length / items.length) * 100)
      : 0,
  }
  const current_focus =
    items.find((item) => item.status === 'in_progress') ||
    items.find((item) => item.status === 'pending') ||
    items.find((item) => item.status === 'blocked') ||
    null
  return {
    version: 1,
    revision: 0,
    title,
    methodology: 'spec-driven',
    planning_mode: 'dynamic',
    phases: SPEC_DRIVEN_PHASES,
    active_phase: current_focus?.phase || (items.length ? 'implement' : 'requirements'),
    items,
    stats,
    current_focus,
    next_actions: items
      .filter((item) => item.status === 'pending' || item.status === 'blocked')
      .slice(0, 3)
      .map((item) => ({
        id: item.id,
        title: item.title,
        reason: item.status === 'pending' ? '等待执行' : '需要解除阻塞',
      })),
    source: 'frontend_markdown_fallback',
    source_markdown: content,
  }
}

const inferFallbackPhase = (title: string): string => {
  const text = title.toLowerCase()
  if (['需求', '澄清', '约束', 'requirement', 'goal'].some((token) => text.includes(token))) {
    return 'requirements'
  }
  if (['结构', '大纲', '方案', '设计', 'outline', 'design'].some((token) => text.includes(token))) {
    return 'design'
  }
  if (['拆解', '任务规划', '任务列表'].some((token) => text.includes(token))) {
    return 'tasks'
  }
  if (['检查', '验收', '完善', '最终', 'verify', 'review'].some((token) => text.includes(token))) {
    return 'verify'
  }
  return 'implement'
}
