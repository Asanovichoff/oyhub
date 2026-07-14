"""CLI: python -m loopforge "Write a 200-word intro about X" [options]"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from .config import ForgeConfig
from .harness import Forge


def main() -> int:
    p = argparse.ArgumentParser(
        prog="loopforge",
        description="Iterative writing refinement: generate → critique → revise → finalize.",
    )
    p.add_argument("task", help="The writing task/prompt.")
    p.add_argument("--title", default="", help="Title for the vault session note.")
    p.add_argument("--vault", default=None, help="Path to your Obsidian vault.")
    p.add_argument("--model", default=None, help="Generator model id.")
    p.add_argument("--critic-model", default=None, help="Critic model id (defaults to --model).")
    p.add_argument("--threshold", type=float, default=None, help="Stop score (0-10).")
    p.add_argument("--max-iterations", type=int, default=None, help="Iteration budget.")
    p.add_argument("--no-session-note", action="store_true", help="Skip writing the vault session note.")
    args = p.parse_args()

    cfg = ForgeConfig()
    if args.vault:
        cfg.vault.vault_path = Path(args.vault).expanduser()
    if args.model:
        cfg.model.model = args.model
    if args.critic_model:
        cfg.model.critic_model = args.critic_model
    if args.threshold is not None:
        cfg.loop.score_threshold = args.threshold
    if args.max_iterations is not None:
        cfg.loop.max_iterations = args.max_iterations

    if not cfg.model.api_key:
        print("error: set LOOPFORGE_API_KEY (and optionally LOOPFORGE_BASE_URL / LOOPFORGE_MODEL)", file=sys.stderr)
        return 2

    forge = Forge(cfg)
    outcome = forge.run(args.task, title=args.title, write_session=not args.no_session_note)
    r = outcome.result

    print(r.final)
    print("\n---", file=sys.stderr)
    print(
        f"score {r.final_score:.1f} | stop: {r.stop_reason.value} | "
        f"iterations: {len(r.iterations)} | finalized: #{r.best_iteration}",
        file=sys.stderr,
    )
    if outcome.lesson_learned:
        print(f"lesson saved to vault: {outcome.lesson_learned}", file=sys.stderr)
    if outcome.session_note:
        print(f"session note: {outcome.session_note}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
