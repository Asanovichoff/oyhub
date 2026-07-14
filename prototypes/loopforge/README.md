# LoopForge

Iterative writing refinement built on two ideas from the Hermes Agent architecture:

- **Loop engineering** — the LLM doesn't answer once. It drafts, a critic scores the draft, a reviser fixes the flagged issues, and the loop repeats until a stop rule fires. The best-scoring draft (not the last one) is finalized.
- **Harness engineering** — the loop runs inside a harness that owns memory, budgets, and learning write-back. Memory lives in your **Obsidian vault** as plain markdown.

Zero dependencies (stdlib only). Works with any OpenAI-compatible API: OpenAI, OpenRouter, Nous Portal, local vLLM/Ollama.

## How the loop works

```
snapshot memory ──► generate draft
                        │
              ┌─────────▼─────────┐
              │  critic scores it │◄────────────┐
              └─────────┬─────────┘             │
                        │                       │
        stop rule fires?│no ──► revise draft ───┘
                        │yes
                        ▼
        finalize BEST-scoring draft
                        │
                        ▼
        write lesson + session note to vault
```

Stop rules, checked in order each iteration:

1. **Threshold** — critic's overall score ≥ `score_threshold` (default 8.5/10). Success.
2. **Plateau** — improvement < `plateau_epsilon` for `plateau_patience` consecutive steps. Diminishing returns; more loops won't help.
3. **Budget** — `max_iterations` reached (default 5). Hard cap so a bad task can't loop forever.

## The Obsidian memory layer

Point `LOOPFORGE_VAULT` at your vault. LoopForge maintains:

```
YourVault/
└── LoopForge Memory/
    ├── Style.md        # your voice & preferences — you edit this
    ├── Lessons.md      # recurring weaknesses the critic found — agent appends
    └── Sessions/       # one note per run: prompt, final text, loop history
```

Frozen-snapshot semantics (borrowed from Hermes): `Style.md` + `Lessons.md` are read once at run start and injected into the generator's system prompt. Lessons written during a run land on disk immediately but only affect the *next* run — the prompt prefix stays stable, so provider prompt caching keeps working. Session notes carry `[[Style]]`/`[[Lessons]]` wiki-links, so Obsidian's graph view shows what memory shaped which piece of writing.

The learning loop closes across sessions: critic notices "opens every paragraph with 'However'" → saved to `Lessons.md` → next run's generator is told to avoid it → first drafts get better → fewer iterations needed.

## Usage

```bash
export LOOPFORGE_API_KEY=sk-...
export LOOPFORGE_BASE_URL=https://openrouter.ai/api/v1   # or any compatible endpoint
export LOOPFORGE_MODEL=openai/gpt-4o
export LOOPFORGE_VAULT=~/Documents/MyVault

python -m loopforge "Write a 300-word blog intro about loop engineering" \
    --title "Blog intro" --threshold 8.5 --max-iterations 5
```

Final text goes to stdout; loop stats to stderr. Use `--critic-model` to grade with a different model than the generator — a model scoring its own prose is biased toward itself.

As a library:

```python
from loopforge.config import ForgeConfig
from loopforge.harness import Forge

cfg = ForgeConfig()
outcome = Forge(cfg).run("Write a product one-pager for ...")
print(outcome.result.final, outcome.result.stop_reason)
```

## Tests

```bash
python -m pytest tests/ -q
```

Nine tests cover all three stop rules, best-not-last finalization, snapshot injection, char-budgeted trimming (newest lessons win), lesson dedupe, session-note wiki-links, and frozen-snapshot semantics. All run offline against a scripted fake LLM.

## Design lineage

| LoopForge piece | Hermes pattern it borrows |
|---|---|
| `vault.py` frozen snapshot | `tools/memory_tool.py` MEMORY.md/USER.md snapshot |
| `engine.py` iteration budget | `agent/iteration_budget.py` |
| Plateau stop rule | curator-style "don't waste cycles" policy |
| `Lessons.md` write-back | autonomous skill/memory creation |
| Char limits, not tokens | Hermes memory bounds (model-independent) |
| Separate critic model | Hermes auxiliary-model pattern |
