const assert = require('node:assert/strict')
const fs = require('node:fs')
const path = require('node:path')
const postcss = require('postcss')
const { parse } = require('@vue/compiler-sfc')
const root = path.resolve(__dirname, '../src')
const theme = postcss.parse(fs.readFileSync(path.join(root, 'theme.css'), 'utf8'))
const definitions = new Set()
theme.walkDecls((decl) => {
  if (decl.prop.startsWith('--')) definitions.add(decl.prop)
})
function check(css, file) {
  postcss.parse(css).walkDecls((decl) => {
    assert.doesNotMatch(
      decl.value,
      /#[\da-f]{3,8}\b|\brgba?\(|\b(?:white|black)\b/i,
      `${file}: ${decl.prop} must consume theme tokens`,
    )
    if (decl.prop === 'border-radius')
      assert.doesNotMatch(decl.value, /\b\d+(?:px|%)/, `${file}: radius must describe its purpose`)
    assert.ok(
      !decl.prop.startsWith('--pa-') && !decl.prop.startsWith('--td-'),
      `${file}: theme values belong in theme.css`,
    )
    for (const [, token] of decl.value.matchAll(/var\((--pa-[\w-]+)/g))
      assert.ok(definitions.has(token), `${file}: undefined token ${token}`)
  })
}
function walk(folder) {
  for (const entry of fs.readdirSync(folder, { withFileTypes: true })) {
    const file = path.join(folder, entry.name)
    if (entry.isDirectory()) walk(file)
    else if (file.endsWith('.vue')) {
      const source = fs.readFileSync(file, 'utf8')
      for (const style of parse(source).descriptor.styles) check(style.content, file)
      assert.doesNotMatch(
        source,
        /(?:background|color|border|boxShadow|borderRadius):\s*['"]?\s*(?:#[\da-f]{3}|white\b|black\b)/i,
        `${file}: inline styles must consume tokens`,
      )
    }
  }
}
walk(root)
check(fs.readFileSync(path.join(root, 'studio.css'), 'utf8'), 'studio.css')
const light = new Map(),
  dark = new Map()
theme.walkRules((rule) => {
  if (rule.selector === ":root,\n:root[theme-mode='light']")
    rule.walkDecls((decl) => light.set(decl.prop, decl.value))
  if (rule.selector === ":root[theme-mode='dark']")
    rule.walkDecls((decl) => dark.set(decl.prop, decl.value))
})
for (const token of light.keys())
  if (token.startsWith('--pa-')) assert.ok(dark.has(token), `dark theme must define ${token}`)
assert.notEqual(light.get('--pa-surface'), dark.get('--pa-surface'))
assert.notEqual(light.get('--pa-ink'), dark.get('--pa-ink'))
console.log(
  'PASS: shared and page styles consume defined tokens, with complete dark palette coverage',
)
