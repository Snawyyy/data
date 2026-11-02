import unittest
from datetime import datetime, timezone
from html.parser import HTMLParser

from artistdb import db, report

NOW = datetime(2026, 11, 2, tzinfo=timezone.utc)


def result(**over):
    base = {
        "start_at": "2021-08", "end_at": "2021-11", "before": 50, "after": 600,
        "multiplier": 12, "trigger_tweet_id": "9", "trigger_ratio": 400.0,
        "cut_off": False, "coarse": False,
    }
    return {**base, **over}


class Tags(HTMLParser):
    def __init__(self):
        super().__init__()
        self.tags = []
        self.attrs = []

    def handle_starttag(self, tag, attrs):
        self.tags.append(tag)
        self.attrs.append(dict(attrs))


class Classification(unittest.TestCase):
    def test_unflagged_signal_is_reliable(self):
        self.assertEqual(report.classify(None, result()), "reliable")
        self.assertEqual(report.classify(result(), None), "reliable")

    def test_only_flagged_signals_are_unsure(self):
        self.assertEqual(report.classify(result(coarse=True), result(cut_off=True)), "flagged")

    def test_no_signal(self):
        self.assertEqual(report.classify(None, None), "none")

    def test_best_date_prefers_a_trustworthy_signal(self):
        fol = result(start_at="2019-01-01T00:00:00+00:00")
        self.assertEqual(report.best_date(fol, result(cut_off=True)), "2019-01")
        self.assertEqual(report.best_date(fol, result()), "2021-08")
        self.assertEqual(report.best_date(None, None), "")


class Charts(unittest.TestCase):
    def test_monthly_median(self):
        posts = [
            ("2021-01-02T00:00:00+00:00", 10, "a"),
            ("2021-01-09T00:00:00+00:00", 30, "b"),
            ("2021-03-01T00:00:00+00:00", 7, "c"),
        ]
        self.assertEqual(
            report.monthly_median_likes(posts), [("2021-01", 20), ("2021-03", 7)]
        )

    def test_log_scale_puts_bigger_values_higher(self):
        x0 = datetime(2020, 1, 1, tzinfo=timezone.utc)
        chart = report.Chart("t", [("2020-06", 10), ("2021-06", 100000)], x0, NOW, "u")
        self.assertLess(chart.y(100000), chart.y(10))
        self.assertLess(chart.x(x0), chart.x(NOW))

    def test_zero_and_empty_series_do_not_crash(self):
        x0 = datetime(2020, 1, 1, tzinfo=timezone.utc)
        self.assertIn("polyline", report.Chart("t", [("2020-06", 0), ("2020-07", 5)], x0, NOW, "u").render())
        self.assertIn("no data", report.Chart("t", [], x0, NOW, "u").render())

    def test_long_gap_breaks_the_likes_line(self):
        x0 = datetime(2020, 1, 1, tzinfo=timezone.utc)
        series = [("2020-01", 5), ("2020-02", 5), ("2024-01", 5), ("2024-02", 5)]
        page = report.Chart("t", series, x0, NOW, "u", break_gaps=True, dots=False).render()
        self.assertEqual(page.count("<polyline"), 2)


class Page(unittest.TestCase):
    def setUp(self):
        self.conn = db.connect(":memory:")
        artist = {"id": "1", "username": "good", "followers": 5000,
                  "created_at": "2019-01-01T00:00:00+00:00"}
        db.upsert_artist(self.conn, artist, "test")
        db.upsert_tweet(self.conn, "1", {
            "id": "9", "created_at": "2021-08-05T00:00:00+00:00", "like_count": 20000,
            "is_reply": 0, "is_retweet": 0,
        })
        db.save_blowup(self.conn, "1", "engagement", result())
        db.upsert_artist(self.conn, {"id": "2", "username": "<b>x</b>", "followers": 10}, "test")

    def page(self, min_followers=0):
        return report.build(self.conn, min_followers=min_followers, now=NOW)

    def test_page_shows_counts_and_trigger_link(self):
        parser = Tags()
        parser.feed(self.page())
        hrefs = {a.get("href") for a in parser.attrs}
        self.assertIn("https://x.com/good/status/9", hrefs)
        self.assertEqual(parser.tags.count("section"), 2)
        self.assertEqual(parser.tags.count("svg"), 4)

    def test_api_text_is_escaped(self):
        page = self.page()
        self.assertNotIn("<b>x</b>", page)
        self.assertIn("&lt;b&gt;x&lt;/b&gt;", page)

    def test_min_followers_filters_artists(self):
        parser = Tags()
        parser.feed(self.page(min_followers=100))
        self.assertEqual(parser.tags.count("section"), 1)


if __name__ == "__main__":
    unittest.main()
