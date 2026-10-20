import { readFileSync, readdirSync } from 'node:fs'
import { fileURLToPath } from 'node:url'
import type { Plugin } from 'vite'

// PDF.js workers fetch these resources by filename, so retain their names in
// development and production rather than importing individually hashed URLs.
export function pdfAssets(): Plugin {
  const files = new Map<string, Buffer>()
  for (const directory of ['cmaps', 'standard_fonts', 'wasm']) {
    const root = new URL(`../node_modules/pdfjs-dist/${directory}/`, import.meta.url)
    for (const name of readdirSync(root)) {
      files.set(`/pdfjs/${directory}/${name}`, readFileSync(fileURLToPath(new URL(name, root))))
    }
  }
  return {
    name: 'pdfjs-resources',
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
