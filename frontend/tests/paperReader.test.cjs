const assert = require('node:assert/strict')
const { loadModule, vue, flush } = require('./moduleHarness.cjs')
const { mountSetup } = require('./vueHost.cjs')
function deferred() {
  let resolve
  const promise = new Promise(done => { resolve = done })
  return {promise, resolve}
}
async function pdfFixture() {
  const requests=[], jobs=[], rendered=[]
  let disconnected = false
  const mod=await loadModule('src/components/PaperPdfReader.vue', {
    vue,
    '@/stores/auth':{useAuthStore:()=>({token:'test-token'})},
    'pdfjs-dist/build/pdf.worker.min.mjs?url':{default:'worker.js'},
    'pdfjs-dist':{
      GlobalWorkerOptions:{},TextLayer:class {},
      getDocument:({data})=>{
        const result=deferred()
        const job={...result,bytes:Array.from(data),destroyed:0,
          destroy(){this.destroyed++;return this.destroyGate?.promise}}
        jobs.push(job)
        return job
      },
    },
  }, {
    ResizeObserver: class {
      observe() {}
      disconnect() { disconnected = true }
    },
    fetch:(url,options)=>{
      const result=deferred()
      requests.push({...result,url,options})
      return result.promise
    },
  })
  const props=vue.reactive({workId:'work/id',version:'pdf-v1'})
  const mounted=mountSetup(mod.default,props,{emit(){},expose(){}})
  const reader=mounted.state
  await flush()
  return {props,reader,requests,jobs,rendered,
    get disconnected(){return disconnected},
    respond(index,byte){requests[index].resolve({ok:true,arrayBuffer:async()=>Uint8Array.of(byte).buffer})},
    decode(index,pages){jobs[index].resolve({numPages:pages,getPage:async page=>{rendered.push({index,page});return {}}})},
    stop(){mounted.unmount()},
  }
}
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
  const mod = await loadModule('src/components/PaperEditor.vue', {vue,'@/api/paper':{paperProjectAPI:api}}, {sessionStorage, setTimeout, clearTimeout, crypto:require('node:crypto').webcrypto})
  const props = vue.reactive({workId:'A',latex:true})
  const emitted=[]
  const mounted = mountSetup(mod.default,props,{emit:(...args)=>emitted.push(args),expose(){}})
  const editor = mounted.state
  const doc = {research_id:'study',main:'paper.tex',version:'v',files:[{path:'paper.tex',text:'unique target',version:'f'}],threads:[],executions:[],changes:[],sources:[],compile:{state:'stale',message:'旧 PDF 已保留'}}
  reads[0].resolve(doc);await flush()
  assert.equal(editor.draft.value,'unique target')
  editor.draft.value='unsaved manual edit'
  const refresh=editor.load();reads[1].resolve({...doc,version:'new'});await refresh
  assert.equal(editor.draft.value,'unsaved manual edit','Refreshing must retain unsaved user edits')
  await editor.captureRead('target')
  assert.equal(editor.quickComment.value,true,'Selecting a passage opens the quote action without a submit form')
  assert.equal(editor.selection.value,null,'Reading text is not an automatic edit target')
  assert.equal(editor.candidates.value.length,1)
  assert.equal(editor.threadId.value,'','A new selection must not reply to a previously opened thread')
  editor.addToChat()
  assert.equal(comments.length,0,'Adding a quotation does not execute a comment')
  assert.equal(emitted.at(-1)[0],'quote')
  assert.equal(emitted.at(-1)[1].read_quote,'target')
  assert.equal(emitted.at(-1)[1].selection,null,'PDF/read quotes must not invent source offsets')
  props.workId='B';await flush();props.workId='C';await flush()
  reads[3].resolve({...doc,version:'C'});await flush();reads[2].resolve({...doc,version:'B'});await flush()
  assert.equal(editor.project.value.version,'C','Cross-project stale responses are discarded')
  assert.equal(editor.draft.value,'unique target','Cross-project local drafts do not leak')
  mounted.unmount()
  console.log('PASS: versioned offsets, source ambiguity, confirmed read selections, draft preservation and project guards')

  let pdf=await pdfFixture()
  assert.match(pdf.requests[0].url,/work%2Fid\/download\/paper\.pdf\?version=pdf-v1$/)
  assert.equal(pdf.requests[0].options.cache,'no-store')
  assert.equal(pdf.requests[0].options.headers.Authorization,'Bearer test-token')
  pdf.respond(0,1);await flush();pdf.decode(0,3);await flush()
  assert.equal(pdf.reader.count.value,3)
  pdf.props.version='pdf-v2 / new';await flush()
  assert.match(pdf.requests[1].url,/\?version=pdf-v2%20%2F%20new$/)
  assert.notEqual(pdf.requests[1].url,pdf.requests[0].url)
  assert.equal(pdf.requests[1].options.cache,'no-store')
  assert.equal(pdf.jobs[0].destroyed,1)
  pdf.respond(1,2);await flush();pdf.decode(1,4);await flush()
  assert.deepEqual(pdf.jobs[1].bytes,[2])
  assert.equal(pdf.reader.count.value,4)
  assert.equal(pdf.rendered.at(-1).index,1)
  pdf.stop()

  // An obsolete load waiting for destroy must not erase a newer document or fetch again.
  pdf=await pdfFixture()
  pdf.respond(0,1);await flush();pdf.decode(0,3);await flush()
  pdf.jobs[0].destroyGate=deferred()
  pdf.props.version='pdf-v2';await flush()
  assert.equal(pdf.requests.length,1)
  pdf.props.version='pdf-v3';await flush()
  assert.equal(pdf.requests.length,2)
  assert.match(pdf.requests[1].url,/version=pdf-v3$/)
  pdf.respond(1,3);await flush();pdf.decode(1,5);await flush()
  pdf.jobs[0].destroyGate.resolve();await flush()
  assert.equal(pdf.requests.length,2)
  assert.equal(pdf.reader.count.value,5)
  await pdf.reader.render()
  assert.equal(pdf.rendered.at(-1).index,1)
  pdf.stop()

  // Late fetch responses never enter PDF.js or replace the current document.
  pdf=await pdfFixture()
  pdf.props.version='pdf-v2';await flush()
  pdf.respond(1,2);await flush();pdf.decode(0,6);await flush()
  pdf.respond(0,1);await flush()
  assert.equal(pdf.jobs.length,1)
  assert.deepEqual(pdf.jobs[0].bytes,[2])
  assert.equal(pdf.reader.count.value,6)
  pdf.stop()

  // A decoder that resolves after destruction is stale too.
  pdf=await pdfFixture()
  pdf.respond(0,1);await flush()
  pdf.props.version='pdf-v2';await flush()
  pdf.respond(1,2);await flush();pdf.decode(1,7);await flush()
  pdf.decode(0,3);await flush()
  assert.equal(pdf.reader.count.value,7)
  assert.equal(pdf.rendered.at(-1).index,1)
  pdf.stop()

  // A response arriving after the component is unmounted must not start a decoder.
  pdf=await pdfFixture()
  pdf.stop()
  assert.equal(pdf.disconnected,true)
  pdf.respond(0,1);await flush()
  assert.equal(pdf.jobs.length,0)
  console.log('PASS: PDF versions bypass response caches and stale loads cannot replace the current document')
})().catch(error => {console.error(error);process.exitCode=1})
