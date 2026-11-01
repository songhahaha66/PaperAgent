const assert=require('node:assert/strict')
const {environment}=require('./workInteractions.test.cjs')
;(async()=>{
  const env=await environment()
  const quoted = await env.work('quoted')
  await quoted.state.loadWork()
  quoted.state.inputValue.value='保留已有输入'
  const context={work_id:'quoted',research_id:'research-1',selection:{file:'paper.tex',version:'source-version',start:0,end:2,quote:'原文'},read_quote:null,read_version:null,thread_id:null,quote:'原文'}
  await quoted.state.attachPaperQuote(context)
  assert.equal(quoted.state.inputValue.value,'保留已有输入')
  const second={...context,selection:{...context.selection,start:3,end:6,quote:'第二段'},quote:'第二段'}
  await quoted.state.attachPaperQuote(second)
  await quoted.state.attachPaperQuote(context)
  assert.equal(quoted.state.paperContexts.value.length,2,'New quotes append; identical quotes do not repeat')
  await quoted.state.sendMessage()
  assert.equal(quoted.sent.length,1)
  assert.equal(quoted.sent[0].message,'保留已有输入')
  assert.equal(quoted.sent[0].paperContext.references.length,2)
  assert.equal(quoted.sent[0].paperContext.references[0].selection.version,'source-version')
  assert.equal(quoted.sent[0].paperContext.references[1].quote,'第二段')
  assert.equal(quoted.sent[0].action,'auto')
  assert.equal(quoted.state.paperContexts.value.length,0)
  quoted.stop()

  const rejected = await env.work('rejected')
  await rejected.state.loadWork()
  let requestId
  const socket = rejected.sockets[0]
  socket.sendMessage = (_message, _model, _action, _scope, _revision, _version, id) => { requestId = id }
  rejected.state.inputValue.value = '修改这两处'
  await rejected.state.attachPaperQuote({...context, work_id: 'rejected'})
  await rejected.state.attachPaperQuote({...second, work_id: 'rejected'})
  await rejected.state.sendMessage()
  assert.equal(rejected.state.paperContexts.value.length, 0)
  socket.message({type: 'error', scope: 'command', request_id: requestId, message: '引用已过期'})
  assert.equal(rejected.state.inputValue.value, '修改这两处', 'Rejected references restore the draft')
  assert.deepEqual(Array.from(rejected.state.paperContexts.value, r => r.quote), ['原文', '第二段'])
  assert.equal(rejected.state.isStreaming.value, false)
  rejected.stop()
  console.log('PASS: quoting preserves input and primary send enters the unified message graph with versioned references')
})().catch(error=>{console.error(error);process.exitCode=1})
