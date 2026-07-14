"""Tests for loop mechanics and vault memory — no network, fake LLM."""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from loopforge.config import ForgeConfig, LoopConfig, VaultConfig
from loopforge.critic import Critic
from loopforge.engine import RefinementLoop, StopReason
from loopforge.harness import Forge
from loopforge.vault import MemorySnapshot, ObsidianVault


class FakeLLM:
    """Scripted client: critic calls return queued scores, generator calls
    return numbered drafts."""

    def __init__(self, scores, patterns=None):
        self.scores = list(scores)
        self.patterns = list(patterns or [])
        self.drafts = 0
        self.critic_calls = 0
        self.systems_seen = []

    def complete(self, model, system, user, temperature):
        self.systems_seen.append(system)
        if "writing critic" in system:
            score = self.scores[min(self.critic_calls, len(self.scores) - 1)]
            pattern = (
                self.patterns[min(self.critic_calls, len(self.patterns) - 1)]
                if self.patterns
                else ""
            )
            self.critic_calls += 1
            return json.dumps(
                {
                    "scores": {"clarity": score, "accuracy": score,
                               "structure": score, "style": score},
                    "overall": score,
                    "issues": ["tighten the opening"],
                    "kept_strengths": ["clear thesis"],
                    "recurring_pattern": pattern,
                }
            )
        self.drafts += 1
        return f"draft-{self.drafts}"


def make_loop(fake, **loop_kwargs):
    cfg = LoopConfig(**loop_kwargs)
    critic = Critic(fake, model="critic")
    return RefinementLoop(fake, critic, cfg, model="gen")


EMPTY = MemorySnapshot(style="", lessons="")


def test_threshold_stop():
    fake = FakeLLM(scores=[6.0, 9.0])
    r = make_loop(fake, score_threshold=8.5, max_iterations=5).run("task", EMPTY)
    assert r.stop_reason == StopReason.THRESHOLD
    assert len(r.iterations) == 2
    assert r.final_score == 9.0
    assert r.final == "draft-2"


def test_plateau_stop():
    # gains: +0.1, +0.1 -> two sub-epsilon steps = plateau
    fake = FakeLLM(scores=[6.0, 6.1, 6.2])
    r = make_loop(
        fake, score_threshold=9.5, max_iterations=10,
        plateau_epsilon=0.25, plateau_patience=2,
    ).run("task", EMPTY)
    assert r.stop_reason == StopReason.PLATEAU
    assert len(r.iterations) == 3


def test_budget_stop_and_no_wasted_revision():
    fake = FakeLLM(scores=[5.0, 6.0, 7.0])
    r = make_loop(fake, score_threshold=9.5, max_iterations=3).run("task", EMPTY)
    assert r.stop_reason == StopReason.BUDGET
    assert len(r.iterations) == 3
    # 1 initial draft + 2 revisions; the 3rd revision would never be scored
    assert fake.drafts == 3


def test_finalize_best_not_last():
    # score regresses on the last iteration; best (7.0) must win
    fake = FakeLLM(scores=[7.0, 5.0, 5.1])
    r = make_loop(
        fake, score_threshold=9.5, max_iterations=3, plateau_epsilon=0.0,
    ).run("task", EMPTY)
    assert r.best_iteration == 1
    assert r.final == "draft-1"
    assert r.final_score == 7.0


def test_memory_snapshot_injected():
    fake = FakeLLM(scores=[9.0])
    mem = MemorySnapshot(style="Short sentences. No jargon.", lessons="- avoid passive voice")
    make_loop(fake, score_threshold=8.5).run("task", mem)
    gen_system = fake.systems_seen[0]
    assert "Short sentences" in gen_system
    assert "avoid passive voice" in gen_system


def test_vault_roundtrip(tmp_path):
    vcfg = VaultConfig(vault_path=tmp_path)
    vault = ObsidianVault(vcfg)
    vault.append_lesson("Opens every paragraph with 'However'")
    vault.append_lesson("opens every paragraph with 'however'")  # dupe, case-insensitive
    snap = vault.snapshot()
    assert snap.lessons.count("However") == 1

    note = vault.write_session_note("My Post", "prompt", "final text", "- history")
    body = note.read_text()
    assert "[[Style]]" in body and "[[Lessons]]" in body
    assert "final text" in body


def test_snapshot_char_budget_keeps_newest(tmp_path):
    vcfg = VaultConfig(vault_path=tmp_path, snapshot_char_limit=120)
    vault = ObsidianVault(vcfg)
    for i in range(20):
        vault.append_lesson(f"lesson number {i} with some padding text")
    snap = vault.snapshot()
    assert len(snap.lessons) <= 120
    assert "lesson number 19" in snap.lessons  # newest survives
    assert "lesson number 0" not in snap.lessons  # oldest trimmed


def test_harness_learning_writeback(tmp_path):
    cfg = ForgeConfig()
    cfg.vault = VaultConfig(vault_path=tmp_path)
    cfg.loop = LoopConfig(score_threshold=8.5, max_iterations=3)
    fake = FakeLLM(scores=[6.0, 9.0], patterns=["Overuses em dashes", "Overuses em dashes"])
    forge = Forge(cfg, client=fake)

    outcome = forge.run("write a haiku about loops", title="Haiku")
    assert outcome.result.stop_reason == StopReason.THRESHOLD
    assert outcome.lesson_learned == "Overuses em dashes"
    # lesson persisted to vault, visible to NEXT snapshot
    assert "Overuses em dashes" in (tmp_path / "LoopForge Memory" / "Lessons.md").read_text()
    # session note written with loop history
    assert outcome.session_note is not None
    assert "finalized" in outcome.session_note.read_text()


def test_frozen_snapshot_semantics(tmp_path):
    """Mid-run lesson writes must not alter the already-taken snapshot."""
    vcfg = VaultConfig(vault_path=tmp_path)
    vault = ObsidianVault(vcfg)
    vault.append_lesson("old lesson")
    snap = vault.snapshot()
    vault.append_lesson("new lesson written mid-run")
    assert "new lesson" not in snap.lessons  # frozen
    assert "new lesson" in vault.snapshot().lessons  # next run sees it
