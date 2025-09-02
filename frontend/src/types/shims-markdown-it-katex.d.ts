declare module 'markdown-it-katex' {
  import type MarkdownIt from 'markdown-it'
  const plugin: (md: MarkdownIt, opts?: any) => void
  export default plugin
}
