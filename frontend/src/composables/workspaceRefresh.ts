/** Coalesce workspace invalidations; each resource has at most one request in flight. */
export function createWorkspaceRefresh<K extends string>(
  loaders: Record<K, () => Promise<unknown>>,
  isDisposed: () => boolean,
) {
  type Entry = { timer?: ReturnType<typeof setTimeout>; running?: Promise<void>; dirty: boolean }
  const entries = new Map<K, Entry>()
  let disposed = false
  const stopped = () => disposed || isDisposed()
  function entry(key: K) {
    let value = entries.get(key)
    if (!value) entries.set(key, (value = { dirty: false }))
    return value
  }
  function run(key: K): Promise<void> {
    if (stopped()) return Promise.resolve()
    const state = entry(key)
    if (state.timer !== undefined) clearTimeout(state.timer)
    state.timer = undefined
    state.dirty = true
    if (state.running) return state.running
    state.running = (async () => {
      // Yield so even synchronously resolved loaders share the same request.
      await Promise.resolve()
      while (state.dirty && !stopped()) {
        state.dirty = false
        await loaders[key]()
      }
    })().finally(() => { state.running = undefined })
    return state.running
  }
  function request(keys: K[]) {
    return Promise.all([...new Set(keys)].map(run)).then(() => {})
  }
  function schedule(keys: K[], delay = 120) {
    if (stopped()) return
    for (const key of new Set(keys)) {
      const state = entry(key)
      if (state.running) { state.dirty = true; continue }
      if (state.timer !== undefined) continue
      state.timer = setTimeout(() => {
        state.timer = undefined
        void run(key).catch((error) => console.error('Workspace refresh failed:', error))
      }, delay)
    }
  }
  function dispose() {
    disposed = true
    for (const state of entries.values()) {
      if (state.timer !== undefined) clearTimeout(state.timer)
      state.dirty = false
    }
  }
  return { request, schedule, dispose }
}
