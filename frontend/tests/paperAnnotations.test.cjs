const assert = require('node:assert/strict')
const { loadModule } = require('./moduleHarness.cjs')

;(async () => {
  const { uniqueTextSpan, placePaperPopover, elementAnchor } = await loadModule(
    'src/utils/paperAnnotations.ts',
    {},
  )
  assert.deepEqual(
    { ...uniqueTextSpan('一段😀带批注的正文。', '带批注的正文') },
    { start: 4, end: 10 },
  )
  assert.equal(
    uniqueTextSpan('重复文字，以及重复文字', '重复文字'),
    null,
    'Never guess which repeated passage an annotation belongs to',
  )
  assert.equal(uniqueTextSpan('正文已修改', '旧正文'), null)
  assert.equal(uniqueTextSpan('任何正文', ' '), null)

  let box = placePaperPopover(
    { left: 180, right: 360, top: 320, bottom: 340 },
    { width: 800, height: 600 },
    { width: 320, height: 120 },
  )
  assert.equal(box.left, 180)
  assert.equal(box.top, 190, 'The small composer belongs directly above the selected text')
  box = placePaperPopover(
    { left: 760, right: 790, top: 15, bottom: 35 },
    { width: 800, height: 600 },
    { width: 320, height: 120 },
  )
  assert.equal(box.left, 468, 'Keep the bubble inside its paper pane')
  assert.equal(box.top, 45, 'Flip below selections near the top of the pane')
  box = placePaperPopover(
    { left: 290, right: 310, top: 170, bottom: 190 },
    { width: 320, height: 240 },
    { width: 400, height: 900 },
  )
  assert.equal(box.width, 296)
  assert.equal(box.maxHeight, 216)
  assert.ok(box.left >= 12 && box.left + box.width <= 308)
  assert.ok(box.top >= 12 && box.top + box.maxHeight <= 228)

  const element = {
    isConnected: true,
    getBoundingClientRect: () => ({ left: 1, top: 2, right: 3, bottom: 4 }),
  }
  const anchor = elementAnchor(element)
  assert.equal(anchor().top, 2)
  element.isConnected = false
  assert.equal(anchor(), null, 'Do not retain an orphaned bubble after its text is replaced')
  assert.equal(elementAnchor(null), null)
  console.log(
    'PASS: exact annotation targets, contextual placement, narrow paper panes and detached anchors',
  )
})().catch((error) => {
  console.error(error)
  process.exitCode = 1
})
