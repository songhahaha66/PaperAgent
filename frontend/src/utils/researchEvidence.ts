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

// Expand familiar words in machine-style metric names without guessing units,
// parameter meanings or scientific interpretations. The stored name stays intact.
export function resultName(name: string) {
  if (!/^[a-z][a-z0-9_]*$/.test(name) || !name.includes('_')) return name
  const words: Record<string, string> = {
    linear: '线性',
    trend: '趋势',
    valid: '验证集',
    pred: '预测',
    sequence: '序列',
    next: '下一',
    week: '周',
    mae: 'MAE',
    mse: 'MSE',
    rmse: 'RMSE',
    a: '参数 a',
    b: '参数 b',
  }
  return name
    .split('_')
    .map((word) => words[word] || (/^day\d+$/.test(word) ? `第${word.slice(3)}天` : word))
    .join(' ')
}
