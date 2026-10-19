export const outcomeLabels: Record<string, [string, string]> = {
  succeeded: ['已计算', 'Computed'],
  running: ['计算中', 'Computing'],
  pending: ['待计算', 'Pending'],
  pending_check: ['待检查', 'Awaiting checks'],
  failed: ['检查未通过', 'Checks failed'],
  cancelled: ['已中断', 'Interrupted'],
  unknown: ['记录未说明', 'Unknown'],
  current: ['成果可查看', 'Available'],
  historical: ['历史成果，依据已变化', 'Historical; inputs changed'],
  legacy: ['旧成果，版本待核实', 'Legacy; version unverified'],
  unavailable: ['尚无成果', 'Unavailable'],
  outdated: ['检查对应旧输入，待重新核实', 'Checks used older inputs'],
  passed: ['当前检查已完成', 'Current checks complete'],
  service_failed: ['检查服务故障，未通过', 'Check service failed; not passed'],
  findings: ['有待核实意见', 'Findings to verify'],
  unreviewed: ['尚未完成审查', 'Not reviewed'],
}
export const outcomeLabel = (value: string, zh: boolean) =>
  outcomeLabels[value]?.[zh ? 0 : 1] || value
