const assert=require('node:assert/strict')
const {loadModule,vue,flush}=require('./moduleHarness.cjs')
;(async()=>{
 const legacy={id:'archived-experiment',research_id:'old-study',task_id:'Q1',revision:1,input_current:false,status:'winner',branches:[0,1,2].map(index=>({index,name:`method ${index}`,status:'valid',metric:index+.1,run_ids:[]}))}
 const module=await loadModule('src/components/ExperimentResult.vue',{vue,'@/api/experiments':{experimentAPI:{read:async()=>({comparisons:[legacy]})}}})
 const scope=vue.effectScope(),props=vue.reactive({workId:'study',recordId:'archived-experiment'})
 const viewer=scope.run(()=>module.default.setup(props,{expose(){},emit(){}}));await flush()
 assert.equal(viewer.record.value.id,legacy.id)
 assert.equal(viewer.record.value.input_current,false)
 assert.equal(viewer.record.value.branches.length,3)
 props.recordId='absent';await flush();assert.equal(viewer.record.value,null);assert(viewer.error.value)
 scope.stop()
 console.log('PASS: experiments open by ID and preserve historical branches without a policy management page')
})().catch(e=>{console.error(e);process.exitCode=1})
