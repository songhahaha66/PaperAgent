const assert = require('node:assert/strict')
const { loadModule } = require('./moduleHarness.cjs')
;(async () => {
  const { isDeepSeekEndpoint } = await loadModule('src/utils/modelProvider.ts', {}, { URL })
  for (const url of ['https://api.deepseek.com/v1', 'https://API.DEEPSEEK.COM:443']) {
    assert.equal(isDeepSeekEndpoint(url), true, url)
  }
  for (const url of [
    'https://api.deepseek.com.evil.example/v1',
    'https://evil.example/api.deepseek.com',
    'https://api.deepseek.com@evil.example',
    'https://evil.example/?host=api.deepseek.com',
    'https://user@api.deepseek.com',
    'https://api.deepseek.com:8443',
    'http://api.deepseek.com',
    'ftp://api.deepseek.com',
    '//api.deepseek.com',
    'api.deepseek.com',
    '',
  ]) assert.equal(isDeepSeekEndpoint(url), false, url)
  console.log('model provider URL regressions passed')
})().catch((error) => { console.error(error); process.exitCode = 1 })
