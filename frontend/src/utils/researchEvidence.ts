import type { ResearchSnapshot } from '@/types/research'
export function evidenceMarkdown(text: string, snapshot: ResearchSnapshot | null) {
  return text.replace(/\{\{\s*([RF]):\s*([RF]\d+)\s*\}\}/g, (raw, _kind, id) => {
    const result =
      snapshot?.results.find((r) => r.id === id) ||
      snapshot?.issue_evidence?.find((r) => r.id === id)
    if (!result) return raw
    const label =
      result.kind === 'figure' || result.kind === 'series'
        ? result.name
        : `${result.value} ${result.unit}`.trim()
    return `[${label.replace(/[\[\]\\]/g, '')}](https://paperagent.local/results/${id})`
  })
}
