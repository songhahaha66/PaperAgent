export function isDeepSeekEndpoint(value: string): boolean {
  try {
    const url = new URL(value)
    return (
      url.protocol === 'https:' &&
      url.hostname === 'api.deepseek.com' &&
      !url.port &&
      !url.username &&
      !url.password
    )
  } catch {
    return false
  }
}
