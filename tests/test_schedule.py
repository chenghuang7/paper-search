import unittest
from datetime import datetime

from scripts.schedule_gate import should_run


def at(value):
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


class ScheduleTests(unittest.TestCase):
    def test_wait_until_0217_beijing_across_utc_date_boundary(self):
        self.assertFalse(should_run("schedule", {}, at("2026-09-06T18:16:00Z")))
        self.assertTrue(should_run("schedule", {}, at("2026-09-06T18:17:00Z")))

    def test_missed_trigger_retries_later_same_day(self):
        old = {"last_published_success": "2026-09-06T12:55:19Z"}
        self.assertTrue(should_run("schedule", old, at("2026-09-06T19:17:00Z")))

    def test_success_today_skips_remaining_checks(self):
        current = {"last_published_success": "2026-09-06T18:18:00Z"}
        self.assertFalse(should_run("schedule", current, at("2026-09-07T00:17:00Z")))

    def test_early_manual_run_does_not_replace_daily_update(self):
        early = {"last_published_success": "2026-09-06T17:10:00Z"}
        self.assertTrue(should_run("schedule", early, at("2026-09-06T18:17:00Z")))

    def test_manual_and_push_always_run(self):
        current = {"last_published_success": "2026-09-07T01:18:00Z"}
        for event in ("push", "workflow_dispatch"):
            self.assertTrue(should_run(event, current, at("2026-09-07T03:17:00Z")))

    def test_invalid_future_and_naive_checkpoint_recover(self):
        for value in (None, "bad", "2027-01-01T00:00:00Z", "2026-09-07T01:18:00"):
            self.assertTrue(should_run("schedule", {"last_published_success": value}, at("2026-09-07T03:17:00Z")))


if __name__ == "__main__":
    unittest.main()
