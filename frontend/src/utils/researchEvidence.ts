import type { ResearchSnapshot } from '@/types/research'
export function evidenceMarkdown(text: string, snapshot: ResearchSnapshot | null) {
  return text
    .replace(/\{\{\s*([RF]):\s*([RF]\d+)\s*\}\}/g, (raw, _kind, id, offset: number) => {
      const result =
        snapshot?.results.find((r) => r.id === id) ||
        snapshot?.issue_evidence?.find((r) => r.id === id)
      if (!result) return '依据当前不可用'
      const unit = result.unit.trim()
      const suffix = text.slice(offset + raw.length).trimStart()
      const label =
        result.kind === 'figure' || result.kind === 'series'
          ? result.name
          : `${result.value}${unit && !suffix.startsWith(unit) ? ' ' + unit : ''}`
      return `[${label.replace(/[\[\]\\]/g, '')}](https://paperagent.local/results/${id})`
    })
    .replace(/\[([RF]\d+)\](?!\()/g, (_raw, id) => {
      const result =
        snapshot?.results.find((r) => r.id === id) ||
        snapshot?.issue_evidence?.find((r) => r.id === id)
      return result
        ? `[${(result.name || '查看依据').replace(/[\[\]\\]/g, '')}](https://paperagent.local/results/${id})`
        : '依据当前不可用'
    })
}

// Labels come from the persisted result; never infer meaning from variable names.
export function resultName(name: string) {
  return name
}
