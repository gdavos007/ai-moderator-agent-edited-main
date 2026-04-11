"""
Tests for the _say_and_wait_for_playback() TTS playback helper.

Verifies the duration estimation and sleep logic that compensates for
wait_for_playout() resolving when frames are queued (~0.5s) rather than
when the client finishes playback.
"""

import unittest

from src.domain.text_analysis import _estimate_tts_duration


def _compute_remaining_sleep(text: str, elapsed: float) -> float:
    """Mirror the sleep calculation from _say_and_wait_for_playback."""
    estimated = _estimate_tts_duration(text)
    return max(estimated - elapsed, 0.0)


class TestEstimateTTSDuration(unittest.TestCase):
    """Verify _estimate_tts_duration produces sensible values."""

    def test_minimum_duration(self):
        """Very short text should still have minimum 1.0s duration."""
        self.assertEqual(_estimate_tts_duration("Hi"), 1.0)
        self.assertEqual(_estimate_tts_duration(""), 1.0)
        self.assertEqual(_estimate_tts_duration("OK"), 1.0)

    def test_typical_sentence(self):
        """A typical sentence should produce a reasonable duration."""
        text = "Now, let's get into tonight's discussion."
        duration = _estimate_tts_duration(text)
        # 41 chars / 15 ≈ 2.73s
        self.assertAlmostEqual(duration, len(text) / 15.0, places=2)
        self.assertGreater(duration, 2.0)
        self.assertLess(duration, 4.0)

    def test_long_text(self):
        """A long closing message should produce a substantial duration."""
        text = (
            "Thank you so much for participating in tonight's focus group. "
            "Your insights have been incredibly valuable, and we really appreciate "
            "your time and thoughtful responses."
        )
        duration = _estimate_tts_duration(text)
        self.assertGreater(duration, 10.0)

    def test_proportional_to_length(self):
        """Longer text should produce longer duration."""
        short = _estimate_tts_duration("Hello there.")
        long = _estimate_tts_duration("Hello there, this is a much longer sentence with more words.")
        self.assertGreater(long, short)


class TestRemainingSleepComputation(unittest.TestCase):
    """Verify the sleep duration calculation: max(estimated - elapsed, 0)."""

    def test_fast_playout_needs_sleep(self):
        """When playout resolves quickly (~0.5s), most duration remains."""
        text = "Now, let's get into tonight's discussion."
        remaining = _compute_remaining_sleep(text, elapsed=0.5)
        estimated = _estimate_tts_duration(text)
        self.assertAlmostEqual(remaining, estimated - 0.5, places=2)
        self.assertGreater(remaining, 1.0)

    def test_slow_playout_no_sleep(self):
        """When elapsed exceeds estimated, remaining is 0 (no negative sleep)."""
        text = "Hi"
        remaining = _compute_remaining_sleep(text, elapsed=5.0)
        self.assertEqual(remaining, 0.0)

    def test_exact_match_no_sleep(self):
        """When elapsed equals estimated, remaining is 0."""
        text = "Test sentence for timing."
        estimated = _estimate_tts_duration(text)
        remaining = _compute_remaining_sleep(text, elapsed=estimated)
        self.assertEqual(remaining, 0.0)

    def test_category_announcement_scenario(self):
        """Simulate the bug: category announcement with only 0.5s wait."""
        text = "Now, let's get into tonight's discussion."
        estimated = _estimate_tts_duration(text)

        # Old behavior: only waited 0.5s after playout (which itself was ~0.5s)
        old_total_wait = 0.5 + 0.5  # playout + fixed sleep
        # New behavior: waits for estimated remaining
        remaining = _compute_remaining_sleep(text, elapsed=0.5)
        new_total_wait = 0.5 + remaining  # playout + computed sleep

        # New wait should be substantially longer than old wait
        self.assertGreater(new_total_wait, old_total_wait)
        # New wait should approximate the full estimated duration
        self.assertAlmostEqual(new_total_wait, estimated, delta=0.1)

    def test_closing_message_scenario(self):
        """Simulate the closing message path that already works correctly."""
        text = (
            "Thank you so much for participating in tonight's focus group. "
            "Your insights have been incredibly valuable."
        )
        estimated = _estimate_tts_duration(text)
        # Simulate playout resolving in 0.5s
        remaining = _compute_remaining_sleep(text, elapsed=0.5)
        # Should sleep for most of the estimated duration
        self.assertGreater(remaining, estimated * 0.8)


if __name__ == "__main__":
    unittest.main()
