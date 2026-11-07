# Agent protocol and execution boundary

## Ownership

PaperAgent borrows Codex's Thread / Turn / Item model and shared tool execution boundary. The reference is [Codex source at 4be90eb](https://github.com/openai/codex/tree/4be90eb069e6e027423c29eedeab5cab9da349dd/codex-rs). This is a PaperAgent protocol, not wire compatibility with the Codex app server.

| Object | Owner | Meaning |
| --- | --- | --- |
| Thread | `agent/context.py`, `agent/store.py` | Persistent agent session. The main chat uses the workspace ID; a delegated session has its own ID and parent linkage. |
| Turn | `TurnWriter` or `session_turn` | One execution interval, with a terminal state. Child Turns share a root Turn ID for authorized history lookup. |
| Item | ToolRuntime or TurnWriter | A typed, ordered unit of conversation. Its identity, type and owning Thread/Turn are immutable. |
| ToolInvocation | `agent/runtime.py` | Tool name, stable call ID, arguments and access class entering the dispatcher. |
| ToolResult | ToolRuntime | Model-facing return value and actual execution outcome. It is not a UI row. |
| AgentEvent | `agent/store.py` | Committed lifecycle change carrying a public Item, Turn or Thread snapshot and a monotonically increasing project event sequence. |

Items include `userMessage`, `agentMessage`, `plan`, `toolCall`, `commandExecution`, `fileChange`, `collabToolCall`, `notice` and `inputRequest`. Planning is a typed checklist. File changes and commands are explicit kinds, not inferred from prose. `agentMessage.phase` distinguishes commentary and final answers.

No old activity, execution-reference or segment reader remains in the new path. Existing old records are not converted. Research domain state and artifact version/history rules remain separate from conversation history.

## Persistence and event flow

`agent/store.py` stores Threads, Turns, Items, events, bound tool calls and result receipts in `.system/agent.sqlite`. Workspace path translation keeps this database in backend-private project state, outside downloadable user files. Research business state and LangGraph checkpoints retain their existing owners.

A tool starts one Item, may update its safe details, and completes it exactly once as `completed`, `failed` or `interrupted`. Snapshots and events commit in one SQLite transaction; terminal snapshots and successful return receipts commit together. A Turn cannot complete with running Items. Coordinator interruption closes outstanding Items before the Turn.

The shared Turn context serializes persistence and publication across the main agent, delegated sessions and progress callbacks. `TurnWriter` maintains a rebuildable chat index and forwards committed events. If the index is stale, history reads authoritative Items and Turn events. Socket delivery is not part of the database transaction.

The WebSocket envelope is:

```json
{
  "type": "agent_event",
  "update": {
    "reply_id": "root-turn-id",
    "chat_task_id": "task-id",
    "thread_id": "main-thread-id",
    "turn_id": "root-turn-id",
    "event": {
      "seq": 42,
      "method": "item/completed",
      "thread_id": "owning-thread-id",
      "turn_id": "owning-turn-id",
      "item": {"id": "stable-item-id", "type": "toolCall", "status": "completed"}
    }
  }
}
```

The abbreviated Item above omits required tool fields. Lifecycle methods are `thread/started`, `turn/started`, `turn/completed`, `item/started`, `item/updated`, `item/completed` and `item/agentMessage/delta`. In this protocol message updates carry the current complete Item snapshot, including on `item/agentMessage/delta`; clients replace by ID rather than appending text. Token buffering flushes at 4 KiB or every 500 ms and is sealed before terminal completion.

Browser merging uses the durable `event.seq`, independently of the process-local transport cursor `(chat_task_id, seq)`. Refresh and reconnect reload persisted history; lower-sequence history cannot roll back newer live state. A child Turn completion cannot complete the root Turn. Opening details resolves Item IDs only within the authorized root Turn.

## One execution boundary

Planner registry calls, scheduled operations, LangChain child tools and experiment adapters enter `agent/runtime.py`. The registry validates and invokes handlers; it no longer defines parallel execution policies. Explicit internal synchronous calls are reserved for business housekeeping and do not create visible model tool calls.

The runtime bounds concurrent reads to four, makes writes exclusive with reads/writes, and has two independent delegation slots. A parent awaiting a child does not hold a write gate. Existing kernel and short file edit locks still guard actual sandbox ownership and publication; access gates do not replace these domain protections.

A call binds its argument signature before execution. Repeated calls with the same session/call identity reuse a recorded result; changing the arguments under that identity is rejected. Python recovery reconciles the existing run manifest before execution, preserving the existing no-replay rule when submission is uncertain. Scheduled action IDs use a separate namespace from provider tool-call IDs.

Return values, returned execution failures, exceptions and cancellation converge at this boundary. A returned failed computation remains a failed Item even when the handler successfully returns explanatory text. Safe progress, warnings, file links and bounded command output are projected into Items; raw arguments, model results and private configuration are not copied into the browser contract.

## Rendering

`conversationTimeline.ts` reduces events into typed Items. `JsonChatRenderer.vue` selects a renderer by type. `ConversationTool.vue` uses the same header and alignment for ordinary tools, commands and file changes, with detail content revealed below it. A log block has a surface because it is output content; the enclosing tool row does not acquire a different card style.

Only an explicit `collabToolCall.child_thread_id` plus `child_turn_id` introduces a nested child session. Internal export compilation updates the single export Item. Adjacent tools and plan updates are folded into a display-only summary group, bounded by authored messages, notices and input requests. The first Item ID stabilizes the group during streaming; grouping never changes persisted Items or implies execution parentage. Expanded rows share one indentation level. Only explicit child session links add agent hierarchy; neither business action IDs nor the Python call stack define it. Only current-Turn child content appears when a persistent child session is reused.

## Recovery limits

LangGraph still schedules and resumes execution. This refactor does not add a distributed worker queue, cross-process lease or transactional notification outbox. The API remains single-process. Durable snapshots recover display after missed socket updates; reconnect alone never restarts execution.

Receipt reuse does not promise arbitrary external side effects execute exactly once across a crash between the side effect and receipt commit. Existing Python manifests, frozen output receipts and file-version checks provide their own recovery guarantees. Event-log compaction and multi-process dispatch are separate future work.
