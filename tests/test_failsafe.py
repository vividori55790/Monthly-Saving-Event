import json
import pathlib
import tomllib
import unittest
from datetime import date, datetime, timedelta, timezone
from unittest import mock

from saving_events import __main__ as app
from saving_events import digest, llm, rules, sources

ROOT = pathlib.Path(__file__).resolve().parent.parent
CONFIG = tomllib.loads((ROOT / "config.toml").read_text(encoding="utf-8"))
NOW = datetime(2026, 11, 3, 9, tzinfo=timezone(timedelta(hours=9)))
UNCONFIRMED = sources.Post("파스쿠찌, 네이버페이 결제시 할인", "", "https://example.com/a", "언론사", NOW)
CONFIRMED = sources.Post("설빙, 네이버페이 결제 50% 적립", "", "https://example.com/b", "언론사", NOW)


def empty_state():
    return app.load_state(pathlib.Path("does-not-exist.json"))


def run_update(state, posts, key):
    with mock.patch.object(sources, "fetch", side_effect=OSError("no network")):
        app.update_events(state, posts, CONFIG, NOW, app.RateReader(CONFIG, state, key))


class LlmFailSafeTest(unittest.TestCase):
    def test_outage_keeps_keyword_results_and_stops_calling(self):
        state = empty_state()
        with mock.patch.object(llm, "_post", side_effect=OSError("down")) as post:
            run_update(state, [UNCONFIRMED, UNCONFIRMED, CONFIRMED], "key")
        self.assertEqual(post.call_count, 1)
        self.assertEqual([e["title"] for e in state["events"]], [CONFIRMED.title])
        self.assertEqual(state["llm_checked"], [])

    def test_garbled_reply_counts_as_outage(self):
        for reply in [{}, {"candidates": []}, {"candidates": [{"content": {"parts": [{"text": "not json"}]}}]}]:
            with mock.patch.object(llm, "_post", return_value=reply), self.assertRaises(llm.LlmError):
                llm.read_rate("t", "s", "m", "k")

    def test_no_key_never_calls(self):
        state = empty_state()
        with mock.patch.object(llm, "_post") as post:
            run_update(state, [UNCONFIRMED], None)
        post.assert_not_called()

    def answer(self, **fields):
        return {"candidates": [{"content": {"parts": [{"text": json.dumps(fields)}]}}]}

    def test_rate_needs_threshold_and_open_event(self):
        cases = [(self.answer(rate=40, open_to_everyone=True), 1), (self.answer(rate=29, open_to_everyone=True), 0),
                 (self.answer(rate=40, open_to_everyone=False), 0), (self.answer(rate=None, open_to_everyone=True), 0),
                 (self.answer(rate="40", open_to_everyone=True), 0), (self.answer(rate=400, open_to_everyone=True), 0)]
        for reply, expected in cases:
            with self.subTest(reply), mock.patch.object(llm, "_post", return_value=reply):
                state = empty_state()
                run_update(state, [UNCONFIRMED], "key")
                self.assertEqual(len(state["events"]), expected)


class CollectionTest(unittest.TestCase):
    RSS = b"<rss><channel><item><title>t</title><link>u</link></item></channel></rss>"

    def collect(self, state, fetch):
        with mock.patch.object(sources, "fetch", side_effect=fetch), mock.patch.object(sources.time, "sleep"):
            return sources.collect(CONFIG, NOW, state)

    def test_one_broken_source_does_not_stop_others(self):
        def fetch(url):
            if "ppomppu" in url:
                raise OSError("blocked")
            return self.RSS
        _, health = self.collect(empty_state(), fetch)
        self.assertIn("OSError", health["뽐뿌 쿠폰"])
        self.assertIsNone(health["루리웹 핫딜"])

    def test_news_waits_for_its_interval(self):
        urls = []
        state = empty_state()
        state["last_news_at"] = (NOW - timedelta(hours=1)).isoformat()
        self.collect(state, lambda url: urls.append(url) or self.RSS)
        self.assertFalse(any("bing" in u or "google" in u for u in urls))

    def test_failing_source_backs_off_then_retries(self):
        state = empty_state()
        state["last_news_at"] = NOW.isoformat()
        state["failing_since"]["뽐뿌 쿠폰"] = (NOW - timedelta(hours=4)).isoformat()
        state["last_try"]["뽐뿌 쿠폰"] = (NOW - timedelta(hours=1)).isoformat()
        urls = []
        self.collect(state, lambda url: urls.append(url) or self.RSS)
        self.assertFalse(any("id=coupon" in u for u in urls))
        state["last_try"]["뽐뿌 쿠폰"] = (NOW - timedelta(hours=3)).isoformat()
        urls.clear()
        self.collect(state, lambda url: urls.append(url) or self.RSS)
        self.assertTrue(any("id=coupon" in u for u in urls))

    def test_alert_only_after_a_day_and_once_per_day(self):
        state = empty_state()
        self.assertEqual(app.track_health(state, {"소스": "error"}, NOW), [])
        later = NOW + timedelta(hours=25)
        self.assertEqual(app.track_health(state, {"소스": "error"}, later), ["소스"])
        self.assertEqual(app.track_health(state, {"소스": "error"}, later + timedelta(minutes=15)), [])
        app.track_health(state, {"소스": None}, later)
        self.assertEqual(state["failing_since"], {})


class DeliveryTest(unittest.TestCase):
    def test_missing_password_keeps_events_pending(self):
        state = empty_state()
        run_update(state, [CONFIRMED], None)
        with mock.patch.dict("os.environ", {}, clear=True):
            self.assertFalse(app.deliver(state["events"], NOW, "a@example.com"))
        self.assertEqual(len(app.pending_events(state, NOW, False)), 1)

    def test_sent_events_are_not_sent_again(self):
        state = empty_state()
        run_update(state, [CONFIRMED], None)
        with mock.patch.dict("os.environ", {"MAIL_APP_PASSWORD": "x"}), mock.patch.object(digest, "send") as send:
            self.assertTrue(app.deliver(app.pending_events(state, NOW, False), NOW, "a@example.com"))
        send.assert_called_once()
        self.assertEqual(app.pending_events(state, NOW, False), [])
        self.assertEqual(len(app.pending_events(state, NOW, True)), 1)
        run_update(state, [CONFIRMED], None)
        self.assertEqual(app.pending_events(state, NOW, False), [])

    def test_mail_failure_keeps_events_pending(self):
        state = empty_state()
        run_update(state, [CONFIRMED], None)
        with mock.patch.dict("os.environ", {"MAIL_APP_PASSWORD": "x"}), \
                mock.patch.object(digest, "send", side_effect=OSError("smtp")), self.assertRaises(OSError):
            app.deliver(app.pending_events(state, NOW, False), NOW, "a@example.com")
        self.assertEqual(len(app.pending_events(state, NOW, False)), 1)


class DirectSourceTest(unittest.TestCase):
    def event(self):
        state = empty_state()
        run_update(state, [CONFIRMED], None)
        return state["events"][0]

    def test_rejects_old_articles_about_a_similar_event(self):
        old = sources.Post("설빙, 네이버페이 빙수 50% 적립", "", "https://old", "언론사", NOW - timedelta(days=60))
        self.assertIsNone(digest.find_direct_source(self.event(), [old], CONFIG["keywords"]["group_stopwords"]))


if __name__ == "__main__":
    unittest.main()
