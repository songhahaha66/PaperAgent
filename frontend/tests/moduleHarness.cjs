const assert = require('node:assert/strict')
const fs = require('node:fs')
const path = require('node:path')
const vm = require('node:vm')
const { createRequire } = require('node:module')
const frontend = path.resolve(__dirname, '..')
const projectRequire = createRequire(path.join(frontend, 'package.json'))
const vue = projectRequire('vue')
const pinia = projectRequire('pinia')
const ts = projectRequire('typescript')
async function loadModule(relativePath, imports, globals = {}, inlineTemplate = false) {
  const context = vm.createContext({ console: { log() {}, error() {} }, ...globals })
  async function sourceModule(relative) {
    let source = fs.readFileSync(path.join(frontend, relative), 'utf8')
    if (relative.endsWith('.vue')) {
      const { parse, compileScript } = projectRequire('@vue/compiler-sfc')
      source = compileScript(parse(source).descriptor, { id: relative, inlineTemplate }).content
    }
    const js = ts.transpileModule(source, {
      compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.ESNext },
    }).outputText
    const module = new vm.SourceTextModule(js, {
      context,
      initializeImportMeta(meta) {
        meta.env = {}
      },
    })
    await module.link(async (id) => {
      if (imports[id]) {
        const values = imports[id]
        return new vm.SyntheticModule(
          Object.keys(values),
          function () {
            for (const [key, value] of Object.entries(values)) this.setExport(key, value)
          },
          { context },
        )
      }
      if (id.endsWith('.vue'))
        return new vm.SyntheticModule(
          ['default'],
          function () {
            this.setExport('default', { render: () => null })
          },
          { context },
        )
      if (id.startsWith('@/utils/') || id.startsWith('@/composables/'))
        return sourceModule(`src/${id.slice(2)}.ts`)
      assert.fail(`Unexpected import ${id}`)
    })
    return module
  }
  const module = await sourceModule(relativePath)
  await module.evaluate()
  return module.namespace
}
async function flush() {
  await vue.nextTick()
  await new Promise(setImmediate)
}
module.exports = { loadModule, vue, pinia, flush }
