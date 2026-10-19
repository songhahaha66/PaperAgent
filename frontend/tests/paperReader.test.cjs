const assert = require('node:assert/strict')
const { loadModule, vue, flush } = require('./moduleHarness.cjs')
;(async () => {
  const util = await loadModule('src/utils/paperSelection.ts', {})
  const chosen = util.sourceSelection('part.tex', 'v', '开头😀重复。重复。', 7, 9)
  assert.equal(chosen.quote, '重复')
  assert.equal(chosen.start, 6, 'UTF-16 offsets convert to Unicode code points')
  assert.equal(util.sourceSelection('empty.tex', 'v', '', 0, 0).quote, '')
  assert.equal(util.sourceKey('https://paperagent.local/citations/C4', []), 'C4')
  assert.equal(util.sourceKey('https://example.org', [{key:'C1',url:'https://example.org'},{key:'C2',url:'https://example.org'}]), undefined, 'An ambiguous source URL must not guess the first quote')
  const reads = [], comments = [], locations = []
  const api = {
    read: work => new Promise(resolve => reads.push({work,resolve})),
    locate: async (work, quote) => { locations.push(quote); return {candidates:[{file:'paper.tex',version:'v',start:8,end:10,quote}]} },
    comment: async (work, body) => { comments.push(body); return {id:body.request_id,thread_id:'thread'} },
  }
  const storage = new Map()
  const sessionStorage = {getItem:key => storage.get(key) || null, setItem:(key,value)=>storage.set(key,value)}
  const scope = vue.effectScope()
  const mod = await loadModule('src/components/PaperEditor.vue', {vue,'@/api/paper':{paperAPI:{read:async()=>({feedback:[]})},paperProjectAPI:api}}, {sessionStorage, setTimeout, clearTimeout, crypto:require('node:crypto').webcrypto})
  const props = vue.reactive({workId:'A',latex:true})
  const editor = scope.run(() => mod.default.setup(props,{emit(){},expose(){}}))
  const doc = {research_id:'study',main:'paper.tex',version:'v',files:[{path:'paper.tex',text:'unique target',version:'f'}],threads:[],executions:[],changes:[],sources:[],compile:{state:'stale',message:'旧 PDF 已保留'}}
  reads[0].resolve(doc);await flush()
  assert.equal(editor.draft.value,'unique target')
  editor.draft.value='unsaved manual edit'
  const refresh=editor.load();reads[1].resolve({...doc,version:'new'});await refresh
  assert.equal(editor.draft.value,'unsaved manual edit','Refreshing must retain unsaved user edits')
  await editor.captureRead('target')
  assert.equal(editor.selection.value,null,'Reading text is not an automatic edit target')
  assert.equal(editor.candidates.value.length,1)
  props.workId='B';await flush();props.workId='C';await flush()
  reads[3].resolve({...doc,version:'C'});await flush();reads[2].resolve({...doc,version:'B'});await flush()
  assert.equal(editor.project.value.version,'C','Cross-project stale responses are discarded')
  assert.equal(editor.draft.value,'unique target','Cross-project local drafts do not leak')
  scope.stop()
  console.log('PASS: versioned offsets, source ambiguity, confirmed read selections, draft preservation and project guards')
})().catch(error => {console.error(error);process.exitCode=1})
