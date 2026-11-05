const assert = require('node:assert/strict')
const { loadModule, vue, flush } = require('./moduleHarness.cjs')
const { mountSetup } = require('./vueHost.cjs')

;(async () => {
  const module = await loadModule('src/components/MarkdownRenderer.vue', {
    vue,
    'markdown-it': await import('markdown-it'),
    '@mdit/plugin-katex': await import('@mdit/plugin-katex'),
    // Inspect the math plugin's output before the existing DOM sanitizer.
    dompurify: { default: { sanitize: (html) => html } },
    'katex/dist/katex.min.css': {},
    '@/stores/auth': { useAuthStore: () => ({ token: '' }) },
    '@/api/workspace': { workspaceFileAPI: {} },
  })
  const props = vue.reactive({ content: '' })
  const mounted = mountSetup(module.default, props, { expose() {}, emit() {} })
  async function render(content) {
    props.content = content
    await flush()
    return mounted.state.renderedContent.value
  }
  try {
    const inline = await render(String.raw`Inline $x \le y$ and **bold**.`)
    assert.match(inline, /class="katex"/)
    assert.doesNotMatch(inline, /katex-error/)
    assert.match(inline, /<strong>bold<\/strong>/)

    const block = await render(String.raw`$$
\frac{1}{2} + \sqrt{x}
$$`)
    assert.match(block, /class="katex-display"/)
    assert.doesNotMatch(block, /katex-error/)

    const code = await render('`$x$` and \\$5')
    assert.match(code, /<code>\$x\$<\/code>/)
    assert.doesNotMatch(code, /class="katex"/)

    const invalid = await render(String.raw`$\unknowncommand{<img src=x onerror=alert(1)>}$`)
    assert.doesNotMatch(invalid, /<img\b/i)
    assert.match(invalid, /&lt;img/)

    const untrusted = await render(String.raw`$\href{javascript:alert(1)}{click}$`)
    assert.doesNotMatch(untrusted, /<a\b/i)

    // An incomplete streaming formula must not stop the next update rendering.
    await render(String.raw`$$\frac{1}{`)
    assert.doesNotMatch(await render('$x^2$'), /katex-error/)
    assert.equal(await render(''), '')
    console.log('PASS: MarkdownRenderer preserves inline/block math, code, malformed input and streaming updates')
  } finally {
    mounted.unmount()
  }
})().catch((error) => {
  console.error(error)
  process.exitCode = 1
})
