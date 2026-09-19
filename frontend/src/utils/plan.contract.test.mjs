import { markdownPlanToData, normalizePlanStatus } from './plan.ts'

const markdown = `# 写作计划

| 序号 | 章节 | 状态 | 说明 |
|------|------|------|------|
| 1 | 明确需求与约束 | ✅ 已完成 | ok |
| 2 | 引言 | ⏳ 进行中 | writing |
| 3 | 最终检查与完善 | ⬜ 待写 | later |
`

const plan = markdownPlanToData(markdown)
if (!plan) throw new Error('old-workspace markdown fallback returned null')
if (plan.items[0].id !== 'task-1') throw new Error('stable ids must stay order-based')
if (plan.current_focus?.title !== '引言') throw new Error('current_focus must follow in-progress item')
if (plan.active_phase !== 'implement') throw new Error(`active_phase should be implement, got ${plan.active_phase}`)
if (plan.next_actions?.[0]?.title !== '最终检查与完善') throw new Error('next_actions should list remaining work')
if (normalizePlanStatus('❌ 阻塞') !== 'blocked') throw new Error('blocked status normalization failed')
if (plan.stats.progress_percent !== 33) throw new Error(`unexpected progress ${plan.stats.progress_percent}`)

console.log('frontend plan fallback contract ok', plan.stats)
