import type { SourceSelection } from '@/api/paper'
export interface PaperRect {
  left: number
  top: number
  right: number
  bottom: number
}
export type PaperAnchor = () => PaperRect | null
export interface TextAnnotation {
  id: string
  kind: 'comment' | 'change'
  quote: string
  label: string
  threadId?: string
  selection: SourceSelection
}

// A repeated or transformed quotation needs an explicit source selection.
// Never underline the first of several matches as though it were the binding.
export function uniqueTextSpan(text: string, quote: string) {
  if (!quote.trim()) return null
  const start = text.indexOf(quote)
  return start < 0 || text.indexOf(quote, start + 1) >= 0
    ? null
    : { start, end: start + quote.length }
}

export function placePaperPopover(
  anchor: PaperRect,
  viewport: { width: number; height: number },
  size: { width: number; height: number },
) {
  const margin = 12,
    gap = 10
  const width = Math.min(size.width, Math.max(0, viewport.width - margin * 2))
  const maxHeight = Math.min(480, Math.max(0, viewport.height - margin * 2))
  const height = Math.min(size.height, maxHeight)
  const below = anchor.bottom + gap
  const above = anchor.top - gap - height
  return {
    left: Math.max(margin, Math.min(anchor.left, viewport.width - width - margin)),
    top: Math.max(
      margin,
      Math.min(above >= margin ? above : below, viewport.height - height - margin),
    ),
    width,
    maxHeight,
  }
}

export function elementAnchor(element: Element | null): PaperAnchor | null {
  return element ? () => (element.isConnected ? element.getBoundingClientRect() : null) : null
}
