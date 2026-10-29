export function isImageFile(path: string): boolean {
  return /\.(jpe?g|png|gif|bmp|svg|webp)$/i.test(path)
}

// Only root-level paper artifacts are the main paper; uploaded names stay intact.
export function paperLabel(path: string, isZh = true): string | null {
  const title = isZh ? '论文' : 'Paper'
  switch (path) {
    case 'paper.md':
    case 'paper.tex':
      return title
    case 'paper.pdf':
      return `${title}${isZh ? '（PDF）' : ' (PDF)'}`
    case 'paper.docx':
      return `${title}${isZh ? '（Word）' : ' (Word)'}`
    default:
      return null
  }
}
