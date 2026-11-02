import pathlib
import tomllib
import unittest
from datetime import date, datetime, timedelta, timezone

from saving_events import digest, rules, sources

ROOT = pathlib.Path(__file__).resolve().parent.parent
CONFIG = tomllib.loads((ROOT / "config.toml").read_text(encoding="utf-8"))
TODAY = date(2026, 11, 3)


def judge(title, summary="", published=TODAY):
    return rules.classify(title, summary, published, TODAY, CONFIG["rules"], CONFIG["keywords"])


class ThresholdTest(unittest.TestCase):
    def test_at_or_above_threshold_passes(self):
        for title in ["네이버페이 결제 30% 적립", "네이버페이 결제 30퍼센트 할인", "네이버페이 결제 30프로 할인",
                      "네이버 머니 QR로 결제시 40퍼센트 할인", "삼성월렛 머니 결제 50% 적립", "삼성월렛 포인트 결제 70% 할인",
                      "편의점 네이버페이 결제 100% 환급", "네이버페이 최대 30% 적립", "네이버페이 20~30% 적립",
                      "네이버페이 40% 혜택", "이마트24 네이버페이 결제 반값 할인",
                      "“1만원 쓰면 5000원 적립”…네이버페이, 오프라인 결제망 확대", "네이버페이 3만원 결제 시 9천원 적립"]:
            with self.subTest(title):
                self.assertEqual(judge(title).status, "pass")

    def test_below_threshold_drops(self):
        for title in ["네이버페이 결제 29% 적립", "네이버페이 결제 29.9% 적립", "네이버페이 1만원 쓰면 2,900원 적립",
                      "해외 어디서나 네이버페이 머니 결제로 3% 적립", "삼성월렛 머니 프로모션…최대 7.5% 포인트 적립"]:
            with self.subTest(title):
                self.assertEqual(judge(title).status, "drop")

    def test_real_rates(self):
        self.assertEqual(judge("파리바게뜨, 네이버페이 결제 시 ‘파란라벨’ 전 제품 40% 할인").rate, 40)
        self.assertEqual(judge("“1만원 쓰면 5000원 적립”…네이버페이, 오프라인 결제망 확대").rate, 50)

    def test_rate_from_summary_needs_benefit_word(self):
        self.assertEqual(judge("파스쿠찌, 네이버페이 결제시 할인", "네이버페이로 결제하면 30% 할인해 준다").status, "pass")
        self.assertEqual(judge("파스쿠찌, 네이버페이 결제시 할인", "매출이 30% 증가했다").status, "unconfirmed")

    def test_statistics_are_not_benefits(self):
        self.assertEqual(judge("네이버페이 적립 이용자 40% 증가").status, "unconfirmed")


class ExclusionTest(unittest.TestCase):
    def test_giveaways_excluded(self):
        for title in ["CU 네이버페이 결제 1+1 할인", "GS25 2+1 행사 할인", "세븐일레븐 1+2 행사 할인", "이마트24 증정 할인 행사"]:
            with self.subTest(title):
                self.assertEqual(judge(title).status, "drop")

    def test_not_open_to_everyone_excluded(self):
        for title in ["SKT 고객 CU 50% 할인", "CU 픽업 주문 40% 할인", "네이버페이 신규 가입 50% 적립",
                      "삼성페이 삼성카드 결제 33% 할인", "챗GPT 구독료 30% 돌려받는다…우리카드"]:
            with self.subTest(title):
                self.assertEqual(judge(title).status, "drop")
        self.assertEqual(judge("CU 50% 할인", "SKT 고객 대상 행사").status, "drop")

    def test_noise_excluded(self):
        for title in ["[네이버페이] 5원 받으세요", "[네이버페이] 라이브 예고 19원 받으세요 (+토스트 적립)",
                      "11/3일자 네이버페이 105원(85원) + 랜덤포인트 모음", "페이스페이 gs25 조기종료인가요? (아직 됨)",
                      "네이버페이 50% 적립 끝났나요?", "삼성월렛 머니, 중국 결제 시장 공략…위챗페이와 손잡았다"]:
            with self.subTest(title):
                self.assertEqual(judge(title).status, "drop")

    def test_other_targets_excluded(self):
        self.assertEqual(judge("[지마켓] 다이슨 30% 할인").status, "drop")

    def test_cu_is_a_whole_word(self):
        self.assertEqual(judge("DOCUMENT 50% 할인").status, "drop")


class PeriodTest(unittest.TestCase):
    def test_periods(self):
        cases = [("서울 맛집 124곳 10/23~29 할인", date(2026, 10, 20), date(2026, 10, 29)),
                 ("파란라벨 40% 할인…네이버페이 결제 시 15~18일 한정", date(2026, 10, 14), date(2026, 10, 18)),
                 ("설빙, 네이버페이 결제 시 50% 적립…18일까지 최대 3만원 혜택", date(2026, 10, 14), date(2026, 10, 18)),
                 ("다음달 30일까지 결제하면 적립", date(2026, 10, 27), date(2026, 11, 30)),
                 ("11월 30일까지 진행", date(2026, 11, 1), date(2026, 11, 30)),
                 ("이마트24 11월 한 달 30% 할인", date(2026, 11, 1), date(2026, 11, 30))]
        for text, published, ends in cases:
            with self.subTest(text):
                self.assertEqual(rules.parse_period(text, published)[1], ends)

    def test_ended_event_dropped(self):
        self.assertEqual(judge("설빙, 네이버페이 결제 시 50% 적립…18일까지", published=date(2026, 10, 14)).status, "drop")

    def test_cap_extracted(self):
        self.assertEqual(judge("네이버페이 50% 적립…최대 1만 원 제공").cap, "1만원")


class GroupingTest(unittest.TestCase):
    def add(self, events, title, url):
        post = sources.Post(title, "", url, "언론사", datetime(2026, 11, 2, tzinfo=timezone.utc))
        digest.add_post(events, post, judge(title), TODAY, CONFIG["keywords"]["group_stopwords"])

    def test_same_event_groups_and_different_events_stay_apart(self):
        events = []
        self.add(events, "네이버페이, 서울레스토랑위크서 50% 적립", "a")
        self.add(events, "얼굴 내밀면 50% 적립…네이버페이, 서울레스토랑위크 혜택", "b")
        self.add(events, "설빙, 네이버페이 포인트·머니 현장 결제 시 50% 적립", "c")
        self.assertEqual(len(events), 2)
        self.assertEqual(len(events[0]["sources"]), 2)

    def test_render_contains_only_event_facts_and_sources(self):
        events = []
        self.add(events, "파리바게뜨, 네이버페이 결제 40% 할인", "https://example.com/a")
        subject, plain, rich = digest.render(events)
        self.assertEqual(subject, "30% 이상 행사 1건")
        self.assertEqual(plain.splitlines(), ["파리바게뜨, 네이버페이 결제 40% 할인", "대상: 네이버페이", "혜택: 40% 할인",
                                              "출처: 언론사 https://example.com/a"])
        self.assertIn('href="https://example.com/a"', rich)


class ScheduleTest(unittest.TestCase):
    def at(self, *args):
        return datetime(*args, tzinfo=timezone(timedelta(hours=9)))

    def test_modes(self):
        daily = {"mode": "daily", "hour": 8, "weekday": "mon", "day_of_month": 1}
        self.assertIsNone(digest.period_key(self.at(2026, 11, 3, 7, 59), daily))
        self.assertEqual(digest.period_key(self.at(2026, 11, 3, 8, 0), daily), "2026-11-03")
        weekly = {**daily, "mode": "weekly", "weekday": "tue"}
        self.assertEqual(digest.period_key(self.at(2026, 11, 3, 9, 0), weekly), "2026-W45")
        self.assertIsNone(digest.period_key(self.at(2026, 11, 4, 9, 0), weekly))
        monthly = {**daily, "mode": "monthly", "day_of_month": 5}
        self.assertIsNone(digest.period_key(self.at(2026, 11, 3, 9, 0), monthly))
        self.assertEqual(digest.period_key(self.at(2026, 11, 6, 9, 0), monthly), "2026-11")
        self.assertIsNone(digest.period_key(self.at(2026, 11, 3, 9, 0), {**daily, "mode": "manual"}))


class ParserTest(unittest.TestCase):
    NOW = datetime(2026, 11, 3, tzinfo=timezone.utc)

    def test_bing_gives_the_real_article_url(self):
        raw = ('<rss xmlns:News="https://x"><channel><item><title>제목</title>'
               '<link>http://www.bing.com/news/apiclick.aspx?ref=FexRss&amp;url=https%3a%2f%2fwww.mk.co.kr%2fnews%2f1&amp;c=1</link>'
               '<description>요약</description><pubDate>Mon, 02 Nov 2026 01:00:00 GMT</pubDate>'
               '<News:Source>매일경제</News:Source></item></channel></rss>').encode()
        post = sources.parse_bing(raw, self.NOW)[0]
        self.assertEqual((post.url, post.source, post.direct), ("https://www.mk.co.kr/news/1", "매일경제", True))

    def test_google_strips_publisher_from_title(self):
        raw = ('<rss><channel><item><title>제목 - 뉴스1</title><link>https://news.google.com/a</link>'
               '<source url="https://news1.kr">뉴스1</source></item></channel></rss>').encode()
        post = sources.parse_google(raw, self.NOW)[0]
        self.assertEqual((post.title, post.source, post.direct), ("제목", "뉴스1", False))


if __name__ == "__main__":
    unittest.main()
