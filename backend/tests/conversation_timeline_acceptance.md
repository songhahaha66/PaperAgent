> 2026-10-23 更新：会话现已迁移至工作区 `.system/research.sqlite` 的 chat_messages/chat_metadata 表，旧 JSON 保留归档。每次只更新当前消息；研究隔离测试比较业务表内容和产物字节，允许聊天表独立变化。下文记录迁移前的验收背景。

# Conversation execution timeline: dev review (v3)

Base: `b5f473e011a21d585486b7bb637d5ec9bd44f58e` (fetched dev). No AGENTS.md found in `/workspace` or the checkout. The starting checkout was clean. No commit, push, merge, deployment, browser test, regression test, pytest invocation, or model API call was performed.

## Implementation

- `chat_history.json` remains the conversation store. One assistant UUID is created before execution, with explicit `chat_task_id`, reply revision, and terminal status. Ordered segments contain Markdown text, safe execution references/projections, and semantic events. Research experiment identifiers remain `experiment_id`; they are never replaced with the chat task identifier.
- A synchronous reply writer serializes streamed text, semantic events, and actual execution boundaries using one lock. Text checkpoints occur every 500 ms or 4 KiB, plus every semantic boundary and terminal transition. Durable atomic replacement precedes publishing each delta. Only unpublished buffered text can be lost on a hard process stop.
- Existing `views.activity` UUIDs and durable model request sequences produce references immediately after their start/finish records commit, before the actual operation can proceed. `research_activity` remains a deferred invalidation signal and never creates rows. Existing execution scopes and parent call links are retained.
- History mutations use a per-file process/thread lock and a lock file, temporary write, fsync, atomic replacement, and directory fsync. They preserve concurrent messages/context. Corrupt history fails closed rather than replacing existing data with an empty conversation. Success, cancellation, and error share one reply finalizer.
- TaskManager sequence replay and ConnectionManager replay remain live optimizations. Deltas carry reply UUID/revision. Refresh binds the existing reply, replay ignores already-present revisions, older turns remain, stale HTTP history cannot overwrite a newer live revision, and completion reloads durable history. A stopped process's unfinished reply is reconciled to interrupted on display-history retrieval. Socket loss does not stop the execution.
- The ordered renderer uses a single compact legacy adapter: original content followed by explicit historical blocks. It never parses JSON-looking prose. Unknown events retain a generic accessible expandable record and readable content. Old content cannot acquire invented interleaving.
- Compact execution groups fold only directly adjacent operations. Running headings select the latest active operation, errors remain visible, manual expansion/collapse survives updates, nested calls retain indentation, and details reuse existing duration/status helpers. Final result Markdown remains outside folded details. Source, filename, output, and explicit result actions remain available.
- Execution details describe the selected operation and bounded read range or Python line count, and provide safe script/stdout/stderr links. Exceptions retain a bounded concrete diagnostic after removing credential values, provider URLs, prompt/config dumps, traceback source lines, and external/internal paths. Existing activity exception normalization uses `review_cache.error_detail`.
- An authorized batch endpoint resolves only references already persisted in the selected reply. It reads exact activity journal records or model request records, including records older than snapshot windows. The safe projection excludes prompts, raw inputs/output, credentials, provider host, raw errors, and arbitrary internal records. File actions require workspace containment, safe file types, and public paths; validation/consistency links are explicit exceptions. The UI chunks requests at 100 references and ignores stale detail responses.
- Removed the chat-bottom compact ResearchRuntime and the unused PaperPipelineCard/derived text-status system. ResearchRuntime inside ResearchProgress remains intact. `paper_history.chat_history` is a display-only projection for old comments. New quoted messages use the ordinary socket and intent graph; separate comment execution and polling are removed. No paper-comment messages are copied into `chat_history.json`. Historical confirmations remain disabled once a newer message exists. Ask/research permissions and context isolation remain; the new empty assistant placeholder is excluded from ask context.

## Validation actually performed

- Python compilation of all changed Python modules and the authored backend test module.
- Vue/TypeScript full type check (`npm run type-check`).
- JavaScript syntax checks (`node --check`) of authored/updated test sources.
- `git diff --check`.

Dependency restoration used `npm ci --ignore-scripts --no-audit --no-fund --cache /tmp/paperagent-npm-cache` because the supplied node_modules lacked pdfjs-dist. No tracked lockfile change was needed. The initial type check failed on that missing dependency; subsequent full checks succeeded after installation. Functional tests below are authored only and must be run by the parent.

## V3 terminal receipt correction

The parent found that assigning `task` in `run_ai_task`'s cancellation branch made it a local variable for the whole function. The normal-success receipt read that unassigned local and its send-error handler swallowed `UnboundLocalError`, leaving clients waiting after durable completion. V3 applies the parent's identical fix: the success receipt uses `ws_callback.writer.id` directly. A parametrized ask/run integration regression observes the actual transport, requires a completion receipt within five seconds, and verifies that the durable terminal checkpoint precedes the matching receipt. This regression is authored only; no tests were run by this implementation agent.

The parent reported that v2's frontend `test:messages` passed, including the stream completion marker. V3 does not change frontend behavior. The parent continues backend/browser acceptance; environment-specific XeLaTeX format preparation requires no product change.

## V2 review corrections

- Existing research-session and pause fixtures now use the actual file-backed history manager/service contract. They inspect typed `reply_update` events and real committed assistant messages, preserve research status/ownership/error privacy assertions, verify partial and terminal writes share one UUID, and compare all research files byte-for-byte while allowing only `chat_history.json` and its lock file to change. No production fallback was added for incomplete mocks.
- `paper_confirm` already had an explicit confirmation branch. New host-renderer tests used exact untrimmed button text, unlike the existing tests; template whitespace caused the missing-button lookups. V2 trims button labels, asserts existence before clicking, and retains historical-confirmation protections. Filename/result link tests use the same correction.
- `conversationStream.test.cjs` now has a 10-second failure deadline and an explicit success marker, so an unresolved Promise cannot silently exit Node before its assertions complete.
- Source inspection also found the safe file filter dropping existing semantic `research:feedback` and `research:consistency` refs. V2 retains exactly those two refs and routes them to research details rather than attempting to open them as filesystem paths. Authored cases cover safe files, result IDs, symbolic research sources, and rejection of unknown/private refs.
- Operation detail and sanitized diagnostic cases now exercise concrete failure reasons, file-read bounds, Python line counts, code/error-output links, secret redaction, prompt/config omission, and stale detail-response protection.

The parent reported that v1's new backend timeline cases and the existing task-manager/comment-history cases passed; v1's old integration fixtures and new frontend button lookups required these changes. No v2 functional tests have been run by this implementation agent.

## Parent acceptance

Suggested offline suites (not executed here):

```sh
cd backend
pytest tests/test_conversation_timeline.py tests/test_research_session.py tests/test_web_controls.py tests/test_task_manager.py tests/test_paper_comment_history.py
cd ../frontend
npm run test:messages
```

The retired PaperPipelineCard test was replaced with meaningful timeline/component/stream behavior cases; the existing JsonChatRenderer severity/keyboard/unknown-event/confirmation checks remain, with only its historical DOM key adapted to stable segment identity.

| Case | Authored evidence / parent inspection |
| --- | --- |
| Text / tool / text, ordinary JSON prose | backend interleaving checkpoint test; frontend timeline and renderer host tests |
| Stable finish update; replay dedup; mid-run refresh | backend same-row and replay cases; frontend delta and stream fixtures |
| Two successive turns and older messages | backend distinct-reply case; renderer historical-confirmation case; stream preserved-history case |
| Parallel/nested operations and model sequence identity | backend concurrent child-call case; execution component nesting display |
| Cancellation/error/empty reply and terminal authority | parameterized backend finalization; frontend error visibility and terminal-history fixture |
| Old history and unknown events | legacy adapter and existing JsonChatRenderer tests |
| Older-than-snapshot detail resolution | journal fixture with 309 later rows and no matching snapshot activity |
| Sources/results/output links | safe projection, renderer source controls, execution file/result actions |
| Paper comments | existing backend comment-history suite plus frontend display-history fixture |
| Safe exposure and permissions | secret-bearing raw record projection test, containment/symlink checks, denied work-authorization case |
| Atomic concurrent writes | spawned-process writer/read test, replace-failure and corrupt-input cases |

Parent browser acceptance should use offline/stub execution data: confirm exact visible text/tool/text ordering, compact running and collapsed completed rows, expanding older-turn details, a mid-run refresh/reconnect, errors and cancellation, safe source/output/result actions, preserved paper-comment polling, and retained ResearchProgress controls. No real model API is needed.

## Remaining limits

- Historical messages lacking segments retain their original text-then-block ordering; their original interleaving is unrecoverable.
- History still uses coarse JSON replacement at bounded checkpoints. Very large histories can impose disk I/O overhead; this patch intentionally introduces no database or execution store.
- Task ownership/recovery assumes the repository's existing single-process TaskManager. File locking handles concurrent filesystem writers but does not add distributed live-task ownership.
- A crash between an activity journal commit and the following chat checkpoint can leave an unreferenced journal record. It cannot publish an unpersisted execution reference. Unpublished buffered text can also be lost.
- Details intentionally provide safe status, timing, links, and result IDs rather than unrestricted raw input/output/prompt debugging. Existing ResearchProgress detail/history remains the richer research view.
- Functional, regression, and browser acceptance remain for the parent. No claim is made that authored test cases have passed.
