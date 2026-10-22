const assert = require('node:assert/strict')
const { loadModule, vue, pinia, flush } = require('./moduleHarness.cjs')
async function environment() {
  const width = vue.ref(1400),
    isMobile = vue.ref(false)
  const auth = vue.reactive({ token: 'test', currentUser: { id: 7 } })
  const storeModule = await loadModule('src/stores/workPanels.ts', {
    vue,
    pinia,
    '@/stores/auth': { useAuthStore: () => auth },
  })
  const store = storeModule.useWorkPanelsStore(pinia.createPinia())
  async function work(id, deferred = false, loadFailures = [], taskAPI = {}, readiness = async () => ({roles:{jev:true}})) {
    const cleanup = [],
      sockets = [],
      sent = [],
      connected = [], paperComments = []
    const scope = vue.effectScope()
    class Socket {
      constructor(workId) {
        this.workId = workId
        sockets.push(this)
      }
      onMessage(callback) {
        this.message = callback
      }
      onReconnect(callback) {
        this.reconnect = callback
      }
      onDisconnect(callback) {
        this.disconnected = callback
      }
      connect() {
        return deferred ? new Promise((resolve) => connected.push(resolve)) : Promise.resolve()
      }
      sendMessage(message, _model, action, subproblemId, revision) {
        sent.push({ workId: this.workId, message, action, subproblemId, revision })
      }
      disconnect() {
        this.closed = true
      }
    }
    const route = vue.reactive({ params: { work_id: id } })
    const module = await loadModule(
      'src/views/Work.vue',
      {
        vue: { ...vue, onMounted() {}, onUnmounted: (callback) => cleanup.push(callback) },
        'vue-router': { useRoute: () => route, useRouter: () => ({ push() {} }) },
        'tdesign-vue-next': {
          DialogPlugin: {},
          MessagePlugin: { error() {}, success() {}, info() {}, warning() {} },
        },
        '@/stores/auth': { useAuthStore: () => auth },
        '@/stores/workPanels': { useWorkPanelsStore: () => store },
        '@/stores/workHistory': { useWorkHistoryStore: () => ({ upsert() {} }) },
        '@/lang/studio': {
          useStudioCopy: () => ({
            isZh: vue.ref(true),
            ui: vue.ref({
              incompleteTitle: 'This research is incomplete',
              restartHint: 'Start research again',
              pausedTitle: 'Progress saved',
              pausedHint: 'Continue unfinished tasks',
              pause: '暂停',
            }),
          }),
        },
        '@/composables/useBreakpoint': {
          useBreakpoint: () => ({ isMobile, width }),
        },
        '@/api/paper':{paperProjectAPI:{comment:async(_work,body)=>{paperComments.push(body);return {id:body.request_id,thread_id:'thread'}},read:async()=>({executions:paperComments.map(body=>({id:body.request_id,state:'completed',answer:'基于当前原文的回复'}))})}},
        '@/api/researchSetup': {researchSetupAPI:{settings:async()=>({})}},
        '@/api/modelConfig': {modelConfigAPI:{readiness}},
        '@/api/chat': {
          WebSocketChatHandler: Socket,
          chatAPI: {
            getResearchSnapshot: async () => ({
              schema_version: 4,
              work_id: id,
              work_status: 'completed',
              has_research: true,
              research_id: 'research-1',
              graph_status: 'done',
              engine_version: 'research-v3',
              research_revision: 1,
              commit_seq: 0,
              snapshot_at: 1,
              questions: [],
              experiments: [],
              runtime: { actions: [], models: [], decisions: [], history: [], models_updated_at: 0, updated_at: 1 },
              results: [],
              runs: [],
              claims: [],
              artifacts: ['paper.pdf'],
              validation_count: 0,
              truncated: false,
            }),
            getWorkChatHistory: async () => ({
              messages: [
                {
                  id: 'saved',
                  role: 'assistant',
                  content: 'Saved result',
                  timestamp: '2026-10-16T06:00:00Z',
                },
              ],
            }),
            getTaskStatus: async () => ({ has_task: false }),
            ...taskAPI,
          },
        },
        '@/api/workspace': {
          workspaceAPI: {
            getWork: async () => {
              if (loadFailures.length) throw loadFailures.shift()
              return {
                id: 1,
                work_id: id,
                title: 'Research',
                status: 'completed',
                output_mode: 'latex',
              }
            },
          },
          attachmentAPI: {},
          workspaceFileAPI: {
            listFiles: async () => [
              { type: 'file', path: 'paper.pdf', name: 'paper.pdf', size: 100 },
            ],
            readFile: async () => ({ content: '{"items":[]}' }),
          },
        },
      },
      {
        document: { querySelector: () => null },
        window: { dispatchEvent() {}, addEventListener() {}, removeEventListener() {} },
        localStorage: { getItem: () => null },
        CustomEvent: class {},
        setTimeout,
        clearTimeout,
        setInterval,
        clearInterval,
        URL,
        crypto: require('node:crypto').webcrypto,
      },
    )
    const state = scope.run(() => module.default.setup({}, { expose() {} }))
    return {
      state,
      sockets,
      sent,
      route,
      connected,
      paperComments,
      stop() {
        cleanup.forEach((callback) => callback())
        scope.stop()
      },
    }
  }
  return { store, work, width, isMobile }
}
module.exports={environment}
if (require.main === module) (async () => {
  const env = await environment()
  for (const [status, expected] of [
    [403, 'forbidden'],
    [404, 'missing'],
    [503, 'failed'],
  ]) {
    const inaccessible = await env.work(`error-${status}`, false, [{ response: { status } }])
    await inaccessible.state.loadWork()
    assert.equal(inaccessible.state.loadError.value, expected)
    assert.equal(inaccessible.state.loading.value, false)
    assert.equal(inaccessible.state.currentWork.value, null)
    assert.equal(inaccessible.sockets.length, 0, 'failed work loading never attaches a task socket')
    await inaccessible.state.loadWork()
    assert.equal(
      inaccessible.state.loadError.value,
      null,
      'successful retry clears the error state',
    )
    assert.equal(inaccessible.state.currentWork.value.title, 'Research')
    inaccessible.stop()
  }
  console.log(
    'PASS: forbidden, missing and unavailable research leave loading and recover on retry',
  )
  const progressWork = await env.work('progress')
  await progressWork.state.loadWork()
  progressWork.state.researchSnapshot.value = {
    schema_version: 4, work_id: 'progress', work_status: 'completed',
    research_id: 'study', research_revision: 1, engine_version: 'research-v3',
    questions: [{ id: 'question:Q1', title: '需求预测', question: '怎样预测需求？',
      experiment_ids: ['Q1'], judgment: '', artifact_refs: [], limitations: [],
      next_step: '', judgments: [], issues: [], sources: [] }],
    experiments: [{ task_id: 'Q1', question_id: 'question:Q1', title: '需求预测实验',
      progress: { state: 'accepted' } }],
    results: [{ id: 'R1', subproblem_id: 'Q1', name: '预测需求', value: '8', unit: '',
      kind: 'number', run_id: 'run_one', figure_path: null }],
    artifacts: [], runs: [], claims: [], delivery: { ready: true, blockers: [] }, delivery_stages: {},
  }
  assert.equal(progressWork.state.panelSession.value.secondaryActiveId, 'progress', 'desktop starts with research beside chat')
  progressWork.state.openProgress()
  assert.equal(progressWork.state.panelSession.value.activeId, 'chat')
  assert.equal(progressWork.state.panelSession.value.secondaryActiveId, 'progress')
  assert.equal(progressWork.state.panelSession.value.splitRatio, 0.45)
  assert.equal(progressWork.state.stageText.value, '已有实验结果，问题尚未形成判断', 'available results do not imply a question judgment')
  progressWork.state.researchSnapshot.value.questions[0].judgment = '当前样本的预测值已记录，外推范围尚待验证。'
  assert.equal(progressWork.state.stageText.value, '已记录问题判断，论文产物尚不可用')
  progressWork.state.openProgress()
  assert.equal(
    progressWork.state.panelSession.value.panels.filter((tab) => tab.kind === 'progress').length,
    1,
  )
  assert.equal(progressWork.sent.length, 0, 'viewing progress never resumes research')
  await progressWork.state.preparePlanRevision('先比较新方法的影响', 'Q1')
  assert.equal(progressWork.sent.at(-1).action, 'revise_research')
  assert.equal(progressWork.sent.at(-1).revision.scope[0], 'Q1')
  assert.equal(progressWork.state.messageAction, undefined, 'the composer has no persistent revision mode')
  assert.equal(progressWork.state.questionScope.value, null)
  progressWork.state.handleStreamMessage({ type: 'complete', work_status: 'paused' }, progressWork.state.chatMessages.value.at(-1).id)
  await progressWork.state.sendMessage('继续')
  assert.equal(progressWork.sent.at(-1).action, 'auto', 'ordinary continuation cannot inherit the preceding revision action')
  assert.equal(progressWork.sent.at(-1).revision, undefined)
  progressWork.stop()
  env.width.value = 620
  env.isMobile.value = true
  const narrowWork = await env.work('narrow')
  narrowWork.state.openProgress()
  assert.equal(narrowWork.state.panelSession.value.activeId, 'progress')
  assert.equal(narrowWork.state.panelSession.value.secondaryActiveId, null)
  assert.ok(narrowWork.state.panelSession.value.panels.some((tab) => tab.kind === 'chat'))
  narrowWork.stop()
  env.width.value = 1400
  env.isMobile.value = false
  console.log(
    'PASS: progress opens beside chat on desktop, as a tab on narrow screens, without starting a run',
  )

  let actionStatus = 'completed'
  let pauseCount = 0
  const primary = await env.work('primary-action', false, [], {
    getResearchSnapshot: async () => ({
      schema_version: 4, work_id: 'primary-action', work_status: actionStatus,
      has_research: true, research_id: 'research-1', engine_version: 'research-v3',
      artifacts: ['paper.pdf'], questions: [], experiments: [], results: [], runs: [], claims: [],
      research_revision: 1, commit_seq: 0, snapshot_at: 1,
      runtime: { actions: [], models: [], decisions: [], history: [], models_updated_at: 0, updated_at: 1 },
    }),
    pauseWork: async () => {
      pauseCount++
      actionStatus = 'paused'
      return { work_status: actionStatus }
    },
  })
  await primary.state.loadWork()
  assert.equal(primary.state.primaryAction.value.kind, 'download')
  assert.equal(primary.state.primaryAction.value.label, '下载')
  assert.ok(!primary.state.workActionOptions.value.some((option) => option.value === 'download'))
  actionStatus = 'waiting'
  primary.state.currentWork.value.status = 'waiting'
  assert.equal(primary.state.primaryAction.value.kind, 'confirm', 'an existing artifact cannot obscure plan confirmation')
  assert.ok(primary.state.workActionOptions.value.some((option) => option.value === 'download'), 'existing output remains available in the secondary menu')
  primary.state.loading.value = true
  await primary.state.handlePrimaryAction()
  assert.equal(primary.sent.length, 0, 'loading cannot submit an unready plan')
  primary.state.loading.value = false
  actionStatus='completed'
  await primary.state.handlePrimaryAction()
  assert.equal(primary.sent.length,0,'a stale waiting button cannot confirm an already completed operation')
  actionStatus='waiting'
  primary.state.currentWork.value.status='waiting'
  await primary.state.handlePrimaryAction()
  assert.equal(primary.sent.length, 0, 'missing information requires an actual user reply, never a synthesized confirmation')
  await primary.state.sendMessage('Necessary information supplied.', 'auto')
  assert.equal(primary.sent.at(-1).action, 'auto')
  assert.equal(primary.state.primaryAction.value.kind, 'pause', 'execution takes precedence over confirmation and existing output')
  assert.equal(primary.state.primaryAction.value.label, '暂停')
  primary.state.pausing.value = true
  await primary.state.handlePrimaryAction()
  assert.equal(pauseCount, 0, 'a pending pause cannot be submitted twice')
  primary.state.pausing.value = false
  await primary.state.handlePrimaryAction()
  assert.equal(pauseCount, 1)
  assert.equal(primary.state.primaryAction.value.kind, 'resume')
  assert.equal(primary.state.primaryAction.value.label, '继续')
  await primary.state.handlePrimaryAction()
  assert.equal(primary.sent.at(-1).action, 'resume')
  assert.equal(primary.state.primaryAction.value.kind, 'pause')
  actionStatus = 'completed'
  primary.state.handleStreamMessage({ type: 'complete', work_status: 'completed' }, primary.state.chatMessages.value.at(-1).id)
  assert.equal(primary.state.primaryAction.value.kind, 'download')
  primary.state.currentWork.value.status = 'paused'
  primary.state.researchSnapshot.value.engine_version = 'legacy'
  assert.notEqual(primary.state.primaryAction.value.kind, 'resume', 'unsupported research must never offer resume')
  const restart = primary.state.workActionOptions.value.find((option) => option.value === 'restart')
  const sentBeforeRestart = primary.sent.length
  restart.onClick()
  assert.equal(primary.state.messageAction, undefined)
  assert.equal(primary.sent.length, sentBeforeRestart, 'secondary restart only prepares a new research')
  primary.state.workspaceFiles.value = []
  assert.equal(primary.state.primaryAction.value, null, 'missing output cannot offer a download')
  primary.stop()
  console.log('PASS: one primary action follows confirmation, execution, recovery and available output')

  const native = await env.work('native-latex')
  await native.state.loadWork()
  native.state.currentWork.value.output_mode = 'latex'
  native.state.workspaceFiles.value = [
    { type: 'file', path: 'paper.md', name: 'paper.md', size: 200 },
    { type: 'file', path: 'paper.tex', name: 'paper.tex', size: 500 },
    { type: 'file', path: 'paper.pdf', name: 'paper.pdf', size: 800 },
  ]
  native.state.researchSnapshot.value = { ...native.state.researchSnapshot.value, has_research: true, artifacts: ['paper.tex'] }
  assert.equal(
    native.state.primaryArtifact.value.path,
    'paper.tex',
    'unpublished old PDFs cannot replace current native source',
  )
  assert.equal(native.state.paperPath.value, 'paper.tex')
  native.state.researchSnapshot.value.artifacts.push('paper.pdf')
  assert.equal(
    native.state.paperPath.value,
    'paper.pdf',
    'successful compilation promotes the PDF preview',
  )
  native.state.researchSnapshot.value.artifacts = []
  assert.equal(native.state.primaryArtifact.value, null)
  native.state.workspaceFiles.value = [
    { type: 'file', path: 'paper.md', name: 'paper.md', size: 200 },
  ]
  assert.equal(
    native.state.paperPath.value,
    'paper.tex',
    'LaTeX never silently falls back to Markdown',
  )
  native.stop()
  console.log(
    'PASS: native LaTeX previews published source/PDF and rejects Markdown or stale exports',
  )

  const first = await env.work('first')
  await first.state.loadWork()
  assert.equal(
    first.state.panelSession.value.activeId,
    'chat',
    'completed work still opens its conversation by default',
  )
  first.state.inputValue.value = 'Unsent draft'
  first.state.handleWorkspaceFileSelect('paper.tex')
  assert.equal(first.state.panelSession.value.secondaryActiveId, 'file:paper.tex')
  assert.equal(first.state.displayPanels.value.find((p) => p.id === 'file:paper.tex').label, '论文')
  first.state.handleWorkspaceFileSelect('paper.pdf')
  assert.equal(
    first.state.displayPanels.value.find((p) => p.id === 'file:paper.pdf').label,
    '论文（PDF）',
  )
  first.state.handleWorkspaceFileSelect('code/model.py')
  first.state.panelSession.value.scroll['file:code/model.py'] = 460
  first.stop()
  const second = await env.work('second')
  assert.equal(second.state.inputValue.value, '')
  second.state.inputValue.value = 'Another draft'
  second.stop()
  const restored = await env.work('first')
  await restored.state.loadWork()
  assert.equal(restored.state.inputValue.value, 'Unsent draft')
  assert.equal(restored.state.panelSession.value.secondaryActiveId, 'file:code/model.py')
  assert.equal(restored.state.panelSession.value.scroll['file:code/model.py'], 460)
  console.log('PASS: keyed work instances restore their own draft, tabs and document position')

  const keyEvent = (extra) => ({
    key: 'Enter',
    shiftKey: false,
    isComposing: false,
    preventDefault() {},
    ...extra,
  })
  restored.state.handleComposerKeydown('', { e: keyEvent({ shiftKey: true }) })
  restored.state.handleComposerKeydown('', { e: keyEvent({ isComposing: true }) })
  await flush()
  assert.equal(restored.sent.length, 0, 'newlines and IME confirmation do not submit')
  restored.state.handleComposerKeydown('', { e: keyEvent() })
  await flush()
  assert.deepEqual(restored.sent, [
    { workId: 'first', message: 'Unsent draft', action: 'auto', subproblemId: undefined, revision: undefined },
  ])
  assert.equal(restored.state.inputValue.value, '')
  assert.equal(restored.state.isStreaming.value, true)
  assert.equal(restored.state.panelSession.value.activeId, 'chat')
  env.store.split('first', 'file:code/model.py', 'right')
  env.store.open('first', 'files', undefined, 'secondary')
  restored.sockets[0].message({ type: 'content', content: 'Still streaming' })
  assert.equal(restored.state.chatMessages.value.at(-1).content, 'Still streaming')
  assert.equal(restored.sockets.length, 1, 'switching and splitting tabs keeps the same stream')
  assert.equal(restored.state.panelSession.value.secondaryActiveId, 'files')
  restored.sockets[0].message({ type: 'complete', work_status: 'completed' })
  assert.equal(restored.state.isStreaming.value, false)
  assert.equal(restored.state.panelSession.value.secondaryActiveId, 'files')
  assert.equal(restored.state.researchSnapshot.value.work_id, 'first')
  restored.state.askSubproblem('Q2')
  await restored.state.sendMessage('解释这些结果')
  assert.equal(restored.sent.at(-1).action, 'auto')
  assert.equal(restored.sent.at(-1).subproblemId, 'Q2')
  const replyId = restored.state.chatMessages.value.at(-1).id
  restored.state.handleStreamMessage(
    {
      type: 'json_block',
      block: { type: 'research_snapshot', content: { schema_version: 4, work_id: 'another-work' } },
    },
    replyId,
  )
  assert.equal(
    restored.state.researchSnapshot.value.work_id,
    'first',
    'other work evidence is ignored',
  )
  restored.state.handleStreamMessage({ type: 'complete', work_status: 'completed' }, replyId)
  await restored.state.sendMessage('重新研究这个问题', 'run')
  assert.equal(restored.sent.at(-1).action, 'run')
  restored.stop()
  console.log('PASS: one-line submission respects IME and streaming continues across split tabs')
  console.log('PASS: scoped follow-up and explicit research send different backend actions')

  const recovery = await env.work('recovery')
  await recovery.state.loadWork()
  recovery.state.chatMessages.value = [
    {
      id: 'old-failure',
      role: 'assistant',
      content: '',
      json_blocks: [{ type: 'paper_solved', content: 'Q1 未完成，登记结果 0 项' }],
    },
  ]
  assert.equal(
    recovery.state.needsRecovery.value,
    false,
    'historical failure cannot override a completed work',
  )
  assert.equal(recovery.state.canResume.value, false)
  recovery.state.currentWork.value.status = 'paused'
  recovery.state.researchSnapshot.value = {
    ...recovery.state.researchSnapshot.value,
    engine_version: null,
    graph_status: 'done',
    work_status: 'paused',
    validation_count: 6,
  }
  assert.equal(
    recovery.state.needsRecovery.value,
    true,
    'exporting a paper does not make incomplete work successful',
  )
  assert.equal(recovery.state.recoveryUnavailable.value, true)
  assert.equal(recovery.state.recoveryHint.value, 'Start research again')
  assert.equal(
    recovery.state.canResume.value,
    false,
    'unsupported records must not offer an action the backend rejects',
  )
  recovery.state.questionScope.value = 'Q1'
  recovery.state.prepareNewResearch()
  assert.equal(recovery.state.messageAction, undefined)
  assert.equal(recovery.state.questionScope.value, null)
  assert.equal(
    recovery.sent.length,
    0,
    'preparing a new research must not submit or overwrite anything',
  )
  recovery.state.researchSnapshot.value.engine_version = 'research-v3'
  assert.equal(recovery.state.canResume.value, true, 'current protocol work remains resumable')
  recovery.state.researchSnapshot.value.research_id = null
  assert.equal(recovery.state.canResume.value, false, 'a missing execution record is not resumable')
  assert.equal(recovery.state.recoveryUnavailable.value, true)
  recovery.state.researchSnapshot.value.research_id = 'research-1'
  recovery.state.isStreaming.value = true
  assert.equal(recovery.state.canResume.value, false, 'active work cannot be resumed concurrently')
  recovery.stop()
  console.log('PASS: recovery controls honor current work state and supported research protocol')

  const progress = await env.work('progress')
  await progress.state.loadWork()
  const snapshot = {
    ...progress.state.researchSnapshot.value,
    commit_seq: 10,
    snapshot_at: 102,
    work_status: 'in_progress',
    questions: [{ id: 'question:E2', title: '验证预测误差', question: '固定区间的预测误差是多少？',
      experiment_ids: ['E2'], sources: [], judgment: '', next_step: '', issues: [],
      artifact_refs: [], limitations: [], judgments: [] }],
    experiments: [{ id: 'E2', task_id: 'E2', question_id: 'question:E2', title: '固定区间误差验证',
      objective: '计算固定区间的预测误差', kind: 'compute', revision: 1, dependencies: [],
      execution_status: 'running', acceptance: 'pending', validity: 'current',
      progress: { state: 'running', waiting_for: [], issue_ids: [], executions: 1, attempt_limit: 3, rework: false },
      results: [], artifacts: [], summary: '', reason: '验证误差', history: [] }],
    runtime: {
      actions: [{ id: 'call-validation', action_id: 'action-validation', observation_id: 'observation-validation',
        question_id: 'question:E2', experiment_id: 'E2', task_id: 'E2', parent_call_id: null,
        capability: 'experiments.execute', label: '执行实验', status: 'running', started_at: 100 }],
      models: [{ id: 'model:2', action_id: 'action-validation', observation_id: 'observation-validation',
        question_id: 'question:E2', experiment_id: 'E2', task_id: 'E2', parent_call_id: 'call-validation',
        kind: 'model', label: '等待模型响应', status: 'sent', started_at: 101, updated_at: 101 }],
      decisions: [], history: [], updated_at: 100, models_updated_at: 101,
    },
  }
  progress.state.currentWork.value.status = 'in_progress'
  progress.state.applyResearchSnapshot(snapshot)
  assert.equal(progress.state.stageText.value, '等待模型响应')
  assert.equal(progress.state.researchSnapshot.value.runtime.current.label, '等待模型响应')
  assert.equal(progress.state.researchSnapshot.value.runtime.current.question_id, 'question:E2')
  assert.equal(progress.state.researchSnapshot.value.experiments[0].title, '固定区间误差验证')
  assert.doesNotMatch(progress.state.stageText.value, /Q2|E2/)
  progress.state.chatMessages.value = [
    {
      id: 'progress-message', role: 'assistant', content: '',
      json_blocks: [{ type: 'paper_phase', content: { stage: 'analyze', status: 'in_progress' } }],
    },
  ]
  assert.equal(
    progress.state.stageText.value,
    '等待模型响应',
    'old phase events cannot reset recorded experiment progress',
  )
  const olderSnapshot = {
    ...snapshot, commit_seq: 9, snapshot_at: 99, work_status: 'completed',
    questions: [], experiments: [],
    runtime: { actions: [], models: [], decisions: [], history: [], updated_at: 90, models_updated_at: 90 },
  }
  progress.state.handleStreamMessage({
    type: 'json_block', block: { type: 'research_snapshot', content: olderSnapshot },
  }, 'progress-message')
  assert.equal(progress.state.researchSnapshot.value.commit_seq, 10, 'an older streamed snapshot cannot rewind progress')
  assert.equal(progress.state.currentWork.value.status, 'in_progress', 'a stale snapshot cannot end active research')
  assert.equal(progress.state.researchSnapshot.value.runtime.current.id, 'model:2')
  await progress.state.loadResearchSnapshot()
  assert.equal(
    progress.state.researchSnapshot.value.commit_seq,
    10,
    'older HTTP/stream snapshots cannot rewind progress',
  )
  assert.equal(progress.state.researchSnapshot.value.runtime.current.id, 'model:2', 'an older HTTP snapshot cannot remove the current model request')
  assert.equal(progress.state.currentWork.value.status, 'in_progress')
  progress.state.currentWork.value.status = 'paused'
  assert.equal(progress.state.stageText.value, '研究已暂停，进度已保留')
  progress.stop()
  console.log(
    'PASS: work progress uses experiment and model records and rejects stale snapshots across recovery',
  )

  let resolveReadiness
  const ready=new Promise(resolve=>{resolveReadiness=resolve})
  const beforeReady=await env.work('leaving-before-readiness',true,[],{},()=>ready)
  const pending=beforeReady.state.sendMessage('Do not send after leaving before readiness')
  beforeReady.stop();resolveReadiness({roles:{jev:true}});await pending
  assert.equal(beforeReady.sent.length,0)
  assert.equal(beforeReady.sockets.length,0,'Unmounting before readiness resolves must not create a socket')

  const leaving = await env.work('leaving', true)
  const send = leaving.state.sendMessage('Do not send after leaving')
  await flush()
  assert.equal(leaving.connected.length,1,'Readiness completed and connection is pending before leaving')
  leaving.stop()
  leaving.connected[0]()
  await send
  assert.equal(leaving.sent.length, 0)
  assert.equal(leaving.sockets[0].closed, true)
  console.log('PASS: a late socket connection cannot submit into an unmounted work')

  const running = await env.work('running', false, [], {
    getTaskStatus: async () => ({
      has_task: true,
      status: 'running',
      action: 'ask',
      started_at: 1,
    }),
    pauseWork: async () => ({ work_status: 'paused' }),
  })
  await running.state.loadWork()
  assert.equal(running.sockets.length, 1, 'restoring a running task creates one stream')
  assert.equal(running.state.runningAction.value, 'ask')
  running.sockets[0].reconnect({ type: 'reconnect' })
  assert.equal(running.state.connectionState.value, 'reconnecting')
  running.sockets[0].message({ type: 'content', content: 'Recovered output' })
  assert.equal(running.state.chatMessages.value.at(-1).content, 'Recovered output')
  running.sockets[0].reconnect({ type: 'reconnect_complete' })
  assert.equal(running.state.connectionState.value, 'live')
  await running.state.pauseTask()
  assert.equal(running.state.isStreaming.value, false)
  assert.equal(running.state.chatMessages.value[0].id, 'saved', 'pausing reloads saved history')
  const count = running.state.chatMessages.value.length
  running.stop()
  running.sockets[0].message({ type: 'content', content: 'Late output' })
  assert.equal(running.state.chatMessages.value.length, count)
  assert.equal(running.sockets[0].closed, true)
  console.log('PASS: task recovery, connection indicators and pause stay independent of pane state')

  let resolveTask
  const delayedTask = new Promise((resolve) => {
    resolveTask = resolve
  })
  const lost = await env.work('lost', false, [], { getTaskStatus: () => delayedTask })
  const reconnect = lost.state.reconnectTask()
  lost.stop()
  resolveTask({ has_task: true, status: 'running' })
  await reconnect
  assert.equal(lost.sockets.length, 0, 'late recovery cannot attach a socket after navigation')
  assert.equal(lost.state.chatMessages.value.length, 0)
  console.log('PASS: late recovery status cannot revive an unmounted work')

  const literature = await env.work('literature')
  literature.state.openLiteratureEvidence({ detail: { workId:'other', evidenceId:'C1' } })
  assert.equal(literature.state.citationRequest.value,null)
  literature.state.openLiteratureEvidence({ detail: { workId:'literature', evidenceId:'C1' } })
  assert.equal(literature.state.citationRequest.value.id,'C1')
  assert(literature.state.panelSession.value.panels.some(p=>p.kind==='paper'))
  assert(!literature.state.panelSession.value.panels.some(p=>p.kind==='materials'))
  literature.state.citeLiterature('C2')
  assert.equal(literature.state.citationRequest.value.id,'C2')
  assert(literature.state.panelSession.value.panels.some(p=>p.kind==='paper'))
  assert.equal(literature.sent.length,0,'opening provenance never starts a model or research task')
  literature.stop()
  console.log('PASS: citation traces open registered sources in the current paper project and prepare a reviewable paper edit')
})().catch((error) => {
  console.error(error)
  process.exitCode = 1
})
