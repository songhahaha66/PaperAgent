/** SQLAlchemy's legacy work timestamps are naive UTC; chat history timestamps are local. */
export function parseWorkDate(value: string): Date {
  const normalized = value.trim().replace(' ', 'T')
  return new Date(/(?:Z|[+-]\d{2}:?\d{2})$/i.test(normalized) ? normalized : `${normalized}Z`)
}
export function formatWorkDate(value: string, locale = navigator.language): string {
  const date = parseWorkDate(value)
  if (Number.isNaN(date.getTime())) return '—'
  return date.toLocaleString(locale, {
    month: 'short',
    day: 'numeric',
    hour: '2-digit',
    minute: '2-digit',
  })
}
