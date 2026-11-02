import sqlite3
import unittest
from datetime import datetime, timedelta, timezone

from artistdb import blowup, db

START = datetime(2020, 1, 1, tzinfo=timezone.utc)


def posts_by_month(levels, per_month=4):
    """One list entry per 30 days: the like count of that month's posts."""
    rows = []
    for i, likes in enumerate(levels):
        for k in range(per_month):
            when = START + timedelta(days=30 * i + k * 6)
            rows.append((when.isoformat(), likes, f"t{i}_{k}"))
    return rows


class FollowerDetection(unittest.TestCase):
    def test_tiny_start_is_found_and_not_cut_off(self):
        got = blowup.from_followers(
            [("2022-04-01T00:00:00+00:00", 77), ("2026-11-01T00:00:00+00:00", 96600)]
        )
        self.assertEqual(got["before"], 77)
        self.assertFalse(got["cut_off"])
        self.assertTrue(got["coarse"])

    def test_start_under_floor_is_ignored(self):
        got = blowup.from_followers(
            [("2022-01-01T00:00:00+00:00", 10), ("2023-01-01T00:00:00+00:00", 9000)]
        )
        self.assertIsNone(got)

    def test_big_first_point_is_cut_off(self):
        got = blowup.from_followers(
            [("2020-01-01T00:00:00+00:00", 70000), ("2020-06-01T00:00:00+00:00", 250000)]
        )
        self.assertTrue(got["cut_off"])
        self.assertFalse(got["coarse"])

    def test_window_after_first_point_is_not_cut_off(self):
        got = blowup.from_followers([
            ("2019-01-01T00:00:00+00:00", 8000),
            ("2020-01-01T00:00:00+00:00", 8500),
            ("2020-03-01T00:00:00+00:00", 40000),
            ("2021-03-01T00:00:00+00:00", 42000),
        ])
        self.assertEqual(got["start_at"][:7], "2020-01")
        self.assertEqual(got["end_at"][:7], "2020-03")
        self.assertFalse(got["cut_off"])
        self.assertFalse(got["coarse"])

    def test_slow_or_falling_followers_are_not_a_blowup(self):
        slow = [("2020-01-01T00:00:00+00:00", 1000), ("2021-01-01T00:00:00+00:00", 1200)]
        falling = [("2020-01-01T00:00:00+00:00", 1000), ("2021-01-01T00:00:00+00:00", 500)]
        self.assertIsNone(blowup.from_followers(slow))
        self.assertIsNone(blowup.from_followers(falling))
        self.assertIsNone(blowup.from_followers([]))


class EngagementDetection(unittest.TestCase):
    def test_sustained_jump_names_the_biggest_recent_post(self):
        rows = posts_by_month([50] * 8 + [600] * 6)
        viral = ((START + timedelta(days=30 * 8 + 1)).isoformat(), 20000, "viral")
        got = blowup.from_engagement(sorted(rows + [viral]))
        self.assertEqual(got["trigger_tweet_id"], "viral")
        self.assertEqual(got["trigger_ratio"], 400.0)
        self.assertEqual(got["before"], 50)
        self.assertEqual(got["after"], 600)

    def test_jump_below_the_floor_is_ignored(self):
        self.assertIsNone(blowup.from_engagement(posts_by_month([1] * 8 + [7] * 6)))

    def test_later_real_jump_is_found_after_a_tiny_one(self):
        rows = posts_by_month([1] * 8 + [7] * 8 + [500] * 6)
        got = blowup.from_engagement(rows)
        self.assertEqual(got["after"], 500)

    def test_spike_that_fades_is_not_a_blowup(self):
        self.assertIsNone(
            blowup.from_engagement(posts_by_month([50] * 8 + [600] + [50] * 6))
        )

    def test_too_little_history_gives_nothing(self):
        self.assertIsNone(blowup.from_engagement(posts_by_month([50, 600, 600])))
        self.assertIsNone(blowup.from_engagement([]))

    def test_cut_off_depends_on_when_stored_posts_begin(self):
        rows = posts_by_month([50] * 8 + [600] * 6)
        near = (START - timedelta(days=100)).isoformat()
        far = (START - timedelta(days=800)).isoformat()
        self.assertFalse(blowup.from_engagement(rows, joined=near)["cut_off"])
        self.assertTrue(blowup.from_engagement(rows, joined=far)["cut_off"])
        self.assertFalse(blowup.from_engagement(rows)["cut_off"])


class Storage(unittest.TestCase):
    def setUp(self):
        self.conn = db.connect(":memory:")
        db.upsert_artist(self.conn, {"id": "1", "username": "a"}, "test")

    def test_flags_are_saved(self):
        result = {
            "start_at": "2020-01", "end_at": "2020-04", "before": 1, "after": 2,
            "multiplier": 2, "cut_off": True, "coarse": True,
        }
        db.save_blowup(self.conn, "1", "followers", result)
        row = self.conn.execute("SELECT cut_off, coarse FROM blowups").fetchone()
        self.assertEqual(tuple(row), (1, 1))

    def test_old_database_gets_new_columns(self):
        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        conn.executescript(db.SCHEMA.replace(
            "    cut_off         INTEGER NOT NULL DEFAULT 0,\n", ""
        ).replace(
            "    coarse          INTEGER NOT NULL DEFAULT 0,\n", ""
        ))
        have = {r["name"] for r in conn.execute("PRAGMA table_info(blowups)")}
        self.assertNotIn("cut_off", have)
        db.migrate(conn)
        have = {r["name"] for r in conn.execute("PRAGMA table_info(blowups)")}
        self.assertLessEqual({"cut_off", "coarse"}, have)


if __name__ == "__main__":
    unittest.main()
