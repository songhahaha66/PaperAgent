import type { SourceSelection } from '@/api/paper'
// Browser textarea offsets are UTF-16; backend ranges are Unicode code points.
export function sourceSelection(
  file: string,
  version: string,
  text: string,
  start: number,
  end: number,
): SourceSelection | null {
  if (start < 0 || end < start || (end === start && text.length > 0) || end > text.length)
    return null
  return {
    file,
    version,
    start: Array.from(text.slice(0, start)).length,
    end: Array.from(text.slice(0, end)).length,
    quote: text.slice(start, end),
  }
}
export function selectionLabel(selection: SourceSelection) {
  return `${selection.file}${selection.line ? `，第 ${selection.line} 行` : ''}`
}
export function sourceKey(
  url: string,
  sources: { key: string; url: string }[],
): string | undefined {
  const local = url.match(/(?:paperagent\.local\/citations\/|#literature-)([A-Za-z0-9_-]+)/)?.[1]
  if (local) return local
  const matches = sources.filter((s) => s.url === url)
  return matches.length === 1 ? matches[0]?.key : undefined
}
