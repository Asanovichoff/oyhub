# Design Patterns Worth Borrowing from Hermes Agent

Source: [NousResearch/hermes-agent](https://github.com/NousResearch/hermes-agent) (MIT). File references point to the repo so you can read the real implementation.

---

## 1. The Learning Loop (memory → skills → curator)

This is the headline feature, and it's three small systems, not one big one.

### 1a. Two-file bounded memory with a frozen snapshot
`tools/memory_tool.py`

- Two plain-text stores: `MEMORY.md` (facts the agent learned: environment quirks, project conventions) and `USER.md` (who the user is: preferences, style, habits). Entries delimited by `§`, character-limited (chars, not tokens — model-independent).
- **Frozen snapshot pattern**: both files are injected into the system prompt once at session start. Mid-session writes hit disk immediately (durable) but do NOT change the live system prompt — this keeps the prompt prefix stable so **prompt caching never invalidates mid-session**. Snapshot refreshes next session.
- The tool is a single `memory` tool with `add / replace / remove` actions; replace/remove match on a short unique substring, not IDs.
- Memory content is scanned for injection/exfiltration patterns before it enters the system prompt (see §8), because a poisoned memory entry persists across all future sessions.
- Drift detection: if the on-disk file was edited outside the tool (shell append, concurrent session), the tool refuses to write, snapshots to `.bak.<ts>`, and asks the operator to resolve — never silently clobbers.

**Takeaway:** memory doesn't need a vector DB. Two bounded markdown files + system-prompt injection + cache-preserving snapshot semantics gets you 80% of the value with none of the infra.

### 1b. Skills as procedural memory
`tools/skills_tool.py`, `agent/learn_prompt.py`, `tools/skill_usage.py`

- A skill is a directory with a `SKILL.md` (frontmatter + body). Compatible with the agentskills.io standard.
- Only the **60-char description** of every skill is loaded into the system prompt as an index; the body loads on demand. This is the routing trick: descriptions are hardline-limited because anything past char 60 is truncated and "never routes."
- Skills are created three ways: user-authored, hub-installed, or **agent-created after complex tasks** (autonomous). Agent-created skills are tracked with provenance (`skill_provenance.py`) and usage timestamps (`skill_usage.py`).
- `/learn` (`agent/learn_prompt.py`) is notable for what it *isn't*: no separate distillation engine. It builds one prompt that tells the live agent "gather the sources with the tools you already have, then author a SKILL.md via skill_manage following these standards." The authoring standards (section order, tool framing, description limits) are embedded verbatim in the prompt — house style enforced at generation time, not review time.

### 1c. The Curator — background maintenance agent
`agent/curator.py`

- An auxiliary-model agent that runs **on inactivity** (no cron daemon needed): when the main agent is idle and the last run was > `interval_hours` ago, a forked agent reviews the skill collection.
- It can pin, archive, consolidate, or patch **agent-created skills only** — strict invariants: never touches user/bundled skills, **never deletes (only archives, recoverable)**, pinned skills bypass all automation, and it uses a separate auxiliary client so it never pollutes the main session's prompt cache.
- Lifecycle states transition automatically from derived activity timestamps (unused skills decay toward archive).

**Takeaway:** self-improvement without a curator produces skill sprawl. The garbage-collection half of the loop is what makes autonomous skill creation safe to leave on.

---

## 2. Session Search: FTS5 over SQLite, zero LLM cost
`tools/session_search_tool.py`

- All conversations live in SQLite with an FTS5 full-text index. One tool, three modes inferred from the arguments (no `mode` parameter):
  - **Discovery** (`query`): BM25 search, dedupe by session lineage, return snippet + ±5-message window + "bookends" (first 3 and last 3 messages of the session, for orientation).
  - **Scroll** (`session_id` + `around_message_id`): windowed pagination by re-anchoring.
  - **Browse** (no args): recent sessions chronologically.
- Subtle ranking fix worth copying: cron/automation sessions are **demoted, not excluded** — high-volume scheduled jobs otherwise dominate BM25 and cause "recall blindness" where the user's own conversations never surface.

**Takeaway:** cross-session recall is a solved problem with SQLite FTS5. No embeddings, no LLM calls, actual messages returned. The bookends idea (session start/end context around a hit) is cheap and very effective.

---

## 3. Programmatic Tool Calling (PTC) — the biggest context-cost trick
`tools/code_execution_tool.py`

- The LLM writes a Python script that calls agent tools **via RPC** (Unix domain socket locally; file-based request/response polling for remote backends like Docker/SSH/Modal).
- A generated `hermes_tools.py` stub exposes an allowlisted subset (~7 tools) as ordinary Python functions inside the sandbox.
- **Only the script's stdout returns to the LLM. Intermediate tool results never enter the context window.** A 50-step read-filter-transform pipeline costs one inference turn.
- These iterations are *refunded* against the iteration budget (see §5) since they don't consume inference.

**Takeaway:** if your agent does data-heavy multi-step work, this pattern (Anthropic calls it "code execution with tool calls") is the single biggest context saver in the repo.

---

## 4. Subagent delegation with strict isolation
`tools/delegate_tool.py`

- `delegate_task` spawns child agent instances: fresh conversation, own terminal session, restricted toolset, focused system prompt built from the goal + explicit context. Parent blocks; batch mode runs children in parallel.
- The parent's context sees only the delegation call and the summary result — never child tool calls.
- A hard blocklist for children: no `delegate_task` (no recursion), no `clarify` (no user interaction from a child), no `memory` (no writes to shared MEMORY.md), no `send_message` (no side effects). 

**Takeaway:** the blocklist is the interesting part. Children that can recurse, interrupt the user, or write shared state are the classic multi-agent failure modes; block them structurally rather than by prompt.

---

## 5. Guardrails as pure, side-effect-free policy modules

Three separable mechanisms, all deliberately split from the runtime:

- **Loop detection** (`agent/tool_guardrails.py`): tracks per-turn tool-call observations (hashes of name+args) and returns *decisions*; runtime decides whether that becomes a warning, synthetic tool result, or a halt. Maintains an explicit set of idempotent tools (reads, searches) where repetition is harmless vs. mutating tools where it isn't.
- **Iteration budget** (`agent/iteration_budget.py`): thread-safe consume/refund counter per agent. Parent default 90, subagents 50 each (independent budgets). PTC turns are refunded.
- **Verify-on-stop** (`agent/verification_stop.py`): if the model tries to end its turn right after editing code without producing fresh verification evidence (a test run, a build), it gets a bounded nudge to verify. Explicitly suppressed for doc/markdown-only edits — a README change must never demand a test script. "Policy-only: it never runs checks itself."

**Takeaway:** guardrails as pure functions over an observation ledger (decide, don't act) keeps them testable and lets each surface (CLI, gateway, batch) choose enforcement.

---

## 6. Context compression that respects the cache
`agent/context_compressor.py`, `agent/conversation_compression.py`

- Auxiliary (cheap) model summarizes **middle** turns; head (system prompt + early context) and a token-budgeted tail are protected.
- Summaries are labeled `[CONTEXT COMPACTION — REFERENCE ONLY]` and use *historical* headings ("Historical Remaining Work" instead of "Next Steps") — because a summary that says "Next Steps: X" reads as an active instruction and the model will go do X again.
- Iterative: repeated compactions update the existing summary rather than stacking summaries-of-summaries.
- Cheap pre-pass prunes bulky tool outputs before any LLM summarization.

**Takeaway:** the "historical headings" detail is hard-won. Summaries that look like instructions cause re-execution loops.

---

## 7. Central tool registry with lazy loading
`tools/registry.py`, `toolsets.py`

- Every tool file self-registers at module level: schema, handler, toolset membership, and an availability `check_fn` (e.g. computer-use registers only if the driver is installed; Home Assistant only if `HASS_TOKEN` is set).
- Registry discovers registration via a cheap AST scan (does this module call `registry.register()` at top level?) so it can lazy-import only needed modules.
- **Toolsets** are named, composable groups ("research", "full_stack", per-platform sets). Conditional tools stay out of the schema entirely when unavailable — no dead tools burning schema tokens.

**Takeaway:** availability-gated registration + composable toolsets is the clean answer to "40+ tools but only ~20 relevant per surface."

---

## 8. Threat patterns as a single shared module
`tools/threat_patterns.py`, `tools/url_safety.py`, `tools/skills_guard.py`

- One source of truth for injection/exfiltration regexes, consumed with different scopes by: the memory tool (strict — content persists in system prompt), context-file scanner, and tool-result delimiter system.
- Skills get an AST audit (`skills_ast_audit.py`) before agent-created code runs; terminal commands go through an approval/allowlist layer (`tools/approval.py`).

**Takeaway:** anything that flows *into the system prompt* (memory, context files, skills) is scanned at write time, with strictness proportional to persistence.

---

## 9. Smaller ideas worth noting

- **Auxiliary model everywhere** (`agent/auxiliary_client.py`): a second cheap/fast model handles summarization, curation, title generation — the main model's context and cache are never disturbed by housekeeping.
- **One gateway process, many surfaces** (`gateway/`): Telegram/Discord/Slack/WhatsApp/Signal share a session store, so a conversation continues across platforms. Slash commands are shared between CLI and messaging.
- **Cron with platform delivery** (`cron/`, `tools/cronjob_tools.py`): scheduled jobs are natural-language prompts whose results deliver to any connected platform.
- **Terminal backends as pluggable environments** (`tools/environments/`): local, Docker, SSH, Singularity, Modal, Daytona behind one `terminal` interface; serverless backends hibernate between sessions.
- **Learning graph** (`agent/learning_graph.py`): skills + memory chunks rendered as a graph ("which learned skills connect to what I remember?") — makes the learning loop *visible*, which builds user trust.
- **Interrupt-and-redirect**: a new user message mid-task interrupts the loop instead of queueing — small UX detail, large perceived-responsiveness gain.

---

## Suggested adoption order for your own agent

1. **Frozen-snapshot memory** (two markdown files + tool) — a day of work, immediate quality-of-life gain, cache-safe.
2. **SQLite FTS5 session search** with the discovery/scroll/browse shape.
3. **Skill index in system prompt** (60-char descriptions, load-on-demand bodies) + a `/learn` command that prompts the agent to author its own skills.
4. **Loop guardrails + iteration budget** — pure-policy module, easy to test.
5. **Context compression** with historical headings and protected head/tail.
6. **PTC / execute_code** — highest payoff, highest engineering cost (RPC transport, sandboxing).
7. **Curator** — only needed once agent-created skills accumulate; add last.
