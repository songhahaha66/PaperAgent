import { readFileSync, readdirSync } from 'node:fs'
import { fileURLToPath } from 'node:url'
import { createHash } from 'node:crypto'
import type { Plugin } from 'vite'

// PDF.js workers fetch these resources by filename, so retain their names in
// development and production rather than importing individually hashed URLs.
export function pdfAssets(): Plugin {
  const files = new Map<string, Buffer>()
  // Some static hosts serve .mjs as application/octet-stream, which browsers
  // reject for module workers. A hashed .js URL has the correct MIME type.
  const worker = readFileSync(new URL('../node_modules/pdfjs-dist/build/pdf.worker.min.mjs', import.meta.url))
  const hash = createHash('sha256').update(worker).digest('hex').slice(0, 16)
  const workerPath = `/pdfjs/pdf.worker-${hash}.js`
  files.set(workerPath, worker)
  for (const directory of ['cmaps', 'standard_fonts', 'wasm']) {
    const root = new URL(`../node_modules/pdfjs-dist/${directory}/`, import.meta.url)
    for (const name of readdirSync(root)) {
      files.set(`/pdfjs/${directory}/${name}`, readFileSync(fileURLToPath(new URL(name, root))))
    }
  }
  return {
    name: 'pdfjs-resources',
    resolveId(id) {
      if (id === 'virtual:pdf-worker-url') return '\0virtual:pdf-worker-url'
    },
    load(id) {
      if (id === '\0virtual:pdf-worker-url')
        return `export default import.meta.env.BASE_URL + ${JSON.stringify(workerPath.slice(1))}`
    },
    configureServer(server) {
      server.middlewares.use((request, response, next) => {
        const path = (request.url || '').split('?')[0]!
        const file = files.get(path)
        if (!file) return next()
        response.setHeader(
          'Content-Type',
          path.endsWith('.js')
            ? 'text/javascript'
            : path.endsWith('.wasm')
              ? 'application/wasm'
              : 'application/octet-stream',
        )
        response.end(file)
      })
    },
    generateBundle() {
      for (const [path, source] of files) {
        this.emitFile({ type: 'asset', fileName: path.slice(1), source })
      }
    },
  }
}
