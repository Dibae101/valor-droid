"""A goal must survive across screens, and must never become a command channel.

Per-screen action choice has no notion of a task spanning screens. Measured on
retained runs, that is how one app was tapped 71 times on the control opening the
system wallpaper picker while reaching the picker's own confirm twice, and another
oscillated roughly 100 times between OK and CANCEL without ever obtaining the
document every screen behind it needed. Published work points the same way:
DroidAgent reports 61% activity coverage against 51% for prior techniques by
setting task goals and pursuing them, rather than choosing taps in isolation.

Two properties are pinned here. The goal persists and ages, so a flow can be
finished. And it stays inert: the executor still only accepts an identifier it
verified on the live screen, so a fabricated task cannot cause a fabricated tap.
"""

from __future__ import annotations

import unittest

from valordroid.llm.config import ModelConfig
from valordroid.llm.gateway import ModelGateway
from valordroid.llm.navigation import (
    MAX_TASK_CHARS,
    MAX_TASK_STEPS,
    TaskMemory,
    build_recovery_action_prompt,
)
from valordroid.llm.providers import ModelResponse, RecordingProvider

CANDIDATES = [
    {
        "candidate_id": "a" * 64,
        "kind": "tap",
        "resource_id": "com.example:id/activate",
        "text": "Activate",
        "content_description": "",
        "attempts": 0,
        "associated_units": 0,
    },
    {
        "candidate_id": "b" * 64,
        "kind": "tap",
        "resource_id": "com.example:id/confirm",
        "text": "Set wallpaper",
        "content_description": "",
        "attempts": 0,
        "associated_units": 0,
    },
]


class TaskMemoryTest(unittest.TestCase):
    def test_a_fresh_memory_asks_for_a_goal(self) -> None:
        lines = " ".join(TaskMemory().prompt_lines())
        self.assertIn("No task is in hand", lines)

    def test_a_goal_persists_and_ages_across_screens(self) -> None:
        memory = TaskMemory()
        memory = memory.observe("set this as the wallpaper", state_id="s1", finished=False)
        self.assertEqual(memory.goal, "set this as the wallpaper")
        for index in range(3):
            memory = memory.observe(
                "set this as the wallpaper", state_id=f"s{index}", finished=False
            )
        self.assertEqual(memory.steps, 3, "steps must accumulate, not reset")
        self.assertTrue(memory.active)
        rendered = " ".join(memory.prompt_lines())
        self.assertIn("set this as the wallpaper", rendered)
        self.assertIn("Actions spent on it: 3", rendered)

    def test_finishing_clears_the_goal_and_remembers_it(self) -> None:
        memory = TaskMemory().observe("obtain a document", state_id="s1", finished=False)
        memory = memory.observe("obtain a document", state_id="s1", finished=True)
        self.assertEqual(memory.goal, "")
        self.assertIn("obtain a document", memory.finished)
        rendered = " ".join(memory.prompt_lines())
        self.assertIn("Already finished or abandoned", rendered)

    def test_a_new_goal_replaces_the_old_one_and_resets_its_age(self) -> None:
        memory = TaskMemory().observe("first goal", state_id="s1", finished=False)
        memory = memory.observe("first goal", state_id="s2", finished=False)
        memory = memory.observe("second goal", state_id="s3", finished=False)
        self.assertEqual(memory.goal, "second goal")
        self.assertEqual(memory.steps, 0)
        self.assertIn("first goal", memory.finished)

    def test_a_goal_cannot_be_pursued_forever(self) -> None:
        memory = TaskMemory().observe("hopeless goal", state_id="s1", finished=False)
        for index in range(MAX_TASK_STEPS + 2):
            memory = memory.observe("hopeless goal", state_id=f"s{index}", finished=False)
        self.assertFalse(memory.active, "an unbounded task would consume the run")
        self.assertTrue(memory.stale)
        self.assertIn("used its whole allowance", " ".join(memory.prompt_lines()))

    def test_a_verbose_goal_is_bounded(self) -> None:
        memory = TaskMemory().observe("g" * 4000, state_id="s1", finished=False)
        self.assertLessEqual(len(memory.goal), MAX_TASK_CHARS)

    def test_the_goal_reaches_the_prompt(self) -> None:
        memory = TaskMemory().observe(
            "set this as the wallpaper", state_id="s1", finished=False
        )
        prompt, shown = build_recovery_action_prompt(
            candidates=CANDIDATES,
            state_id="s1",
            activity="com.example/.Main",
            failed_rungs=[],
            recent_state_ids=[],
            task=memory,
            max_characters=8000,
        )
        self.assertIn("set this as the wallpaper", prompt)
        self.assertIn("task_finished", prompt)
        self.assertEqual(len(shown), len(CANDIDATES), "candidates must still fit")


class GatewayTaskTest(unittest.TestCase):
    """The gateway must carry the goal forward without loosening its bounds."""

    @staticmethod
    def _gateway(payload: str) -> tuple[ModelGateway, RecordingProvider]:
        config = ModelConfig(
            enabled=True,
            provider="openai_compatible",
            model="test-model",
            base_url="https://example.invalid/v1",
            max_calls_per_run=4,
            max_calls_per_purpose=4,
        )
        provider = RecordingProvider(config, (ModelResponse(payload, 12, 3),))
        return ModelGateway(config, provider, environment={}), provider

    def _select(self, gateway: ModelGateway):
        return gateway.select_recovery_action(
            candidates=CANDIDATES,
            state_id="s1",
            activity="com.example/.Main",
            failed_rungs=(),
            recent_state_ids=(),
        )

    def test_a_declared_goal_is_remembered_for_the_next_call(self) -> None:
        gateway, _ = self._gateway(
            '{"task": "set this as the wallpaper", "task_finished": false, '
            f'"candidate_id": "{"a" * 64}"}}'
        )
        self.assertEqual(gateway.task.goal, "")
        outcome = self._select(gateway)
        self.assertEqual(outcome.value, "a" * 64)
        self.assertEqual(gateway.task.goal, "set this as the wallpaper")

    def test_a_reply_without_a_task_still_works(self) -> None:
        """Older replies must not be refused for lacking the new fields."""

        gateway, _ = self._gateway(f'{{"candidate_id": "{"a" * 64}"}}')
        outcome = self._select(gateway)
        self.assertEqual(outcome.value, "a" * 64)
        self.assertEqual(gateway.task.goal, "")

    def test_a_task_cannot_authorise_an_unoffered_action(self) -> None:
        """The task is memory, not a command channel."""

        gateway, _ = self._gateway(
            '{"task": "tap the thing I invented", "candidate_id": "' + "f" * 64 + '"}'
        )
        outcome = self._select(gateway)
        self.assertFalse(hasattr(outcome, "value"), "an unoffered id must be refused")
        self.assertEqual(
            gateway.task.goal, "", "a refused reply must not update the goal"
        )


if __name__ == "__main__":
    unittest.main()
