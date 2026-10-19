const assert=require('node:assert/strict')
const {loadModule,vue,flush}=require('./moduleHarness.cjs')
;(async()=>{
 const legacy={id:'archived-experiment',research_id:'old-study',task_id:'Q1',revision:1,input_current:false,status:'winner',branches:[0,1,2].map(index=>({index,name:`method ${index}`,status:'success',metric:index+.1,run_ids:[]})),budget:{used:{},limits:{}}}
 let writes=0
 const module=await loadModule('src/components/ResearchComparisons.vue',{vue,'@/api/comparisons':{comparisonAPI:{read:async()=>({comparisons:[legacy],files:[],policy:{enabled:true,spec:null}}),policy:async()=>writes++}},'@/lang/studio':{useStudioCopy:()=>({isZh:vue.ref(true)})}})
 const scope=vue.effectScope(),props=vue.reactive({workId:'study',recordId:'archived-experiment'})
 const viewer=scope.run(()=>module.default.setup(props,{expose(){},emit(){}}));await flush()
 assert.equal(viewer.records.value.length,1)
 assert.equal(viewer.records.value[0].input_current,false,'historical records remain readable')
 assert.equal(viewer.records.value[0].branches.length,3,'viewing is not restricted to two candidates')
 assert.equal(writes,0,'opening historical results never changes the comparison policy')
 props.configure=true;await flush();assert.equal(viewer.records.value.length,0,'capability settings do not duplicate result content')
 scope.stop()
 console.log('PASS: historical experiment results open by ID without mutating policy or assuming a pair of candidates')
})().catch(e=>{console.error(e);process.exitCode=1})
