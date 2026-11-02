"""Fetches posts from news and community RSS feeds."""
import html
import re
import time
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime

USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0 Safari/537.36"
PAUSE_SECONDS = 1


@dataclass
class Post:
    title: str
    summary: str
    url: str
    source: str
    published: datetime
    direct: bool = True  # False when the link goes through a news aggregator


def fetch(url):
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(request, timeout=30) as response:
        return response.read()


def _plain(text):
    text = re.sub(r"<[^>]+>", " ", html.unescape(text or ""))
    return re.sub(r"\s+", " ", text).strip()


def _published(item, now):
    raw = item.findtext("pubDate")
    if raw:
        try:
            parsed = parsedate_to_datetime(raw)
            return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
        except (TypeError, ValueError):
            pass
    return now


def _child_text(item, local_name):
    for child in item:
        if child.tag.rsplit("}", 1)[-1].lower() == local_name.lower():
            return (child.text or "").strip()
    return ""


def parse_bing(raw, now):
    posts = []
    for item in ET.fromstring(raw).findall(".//item"):
        query = urllib.parse.parse_qs(urllib.parse.urlparse(item.findtext("link") or "").query)
        url = query.get("url", [item.findtext("link") or ""])[0]
        posts.append(Post(_plain(item.findtext("title")), _plain(item.findtext("description")), url,
                          _child_text(item, "Source") or urllib.parse.urlparse(url).netloc, _published(item, now)))
    return posts


def parse_google(raw, now):
    posts = []
    for item in ET.fromstring(raw).findall(".//item"):
        source = item.findtext("source") or ""
        title = _plain(item.findtext("title"))
        if source and title.endswith(f" - {source}"):
            title = title[: -len(source) - 3]
        posts.append(Post(title, "", item.findtext("link") or "", source or "Google 뉴스", _published(item, now), direct=False))
    return posts


def parse_rss(raw, name, now):
    return [Post(_plain(item.findtext("title")), _plain(item.findtext("description")), item.findtext("link") or "",
                 name, _published(item, now))
            for item in ET.fromstring(raw).findall(".//item")]


def parse_clien(raw, now):
    page = raw.decode("utf-8", "replace")
    posts = []
    for path, title in re.findall(r'<a href="(/service/board/jirum/\d+)[^"]*" data-role="list-title-text">(.*?)</a>', page, re.S):
        posts.append(Post(_plain(title), "", f"https://www.clien.net{path}", "클리앙 알뜰구매", now))
    return posts


def bing_url(query):
    return "https://www.bing.com/news/search?" + urllib.parse.urlencode(
        {"q": query, "format": "rss", "setlang": "ko", "cc": "KR"})


def _retry_due(name, state, now):
    """A failing source is retried less often the longer it fails, up to every 6 hours."""
    since = state["failing_since"].get(name)
    last_try = state["last_try"].get(name)
    if not since or not last_try:
        return True
    failing_for = now - datetime.fromisoformat(since)
    wait = min(timedelta(hours=6), max(timedelta(minutes=15), failing_for / 2))
    return now - datetime.fromisoformat(last_try) >= wait


def collect(config, now, state):
    """Returns (posts, {source name: error or None}). A source with zero posts counts as broken."""
    posts, health = [], {}
    started = time.monotonic()
    budget = config["collection"]["time_budget_seconds"]

    def run(name, loader):
        if time.monotonic() - started > budget or not _retry_due(name, state, now):
            print(f"이번 실행에서 건너뜀: {name}")
            return
        state["last_try"].pop(name, None)
        try:
            found = loader()
            if not found:
                raise ValueError("글을 하나도 읽지 못했습니다")
            posts.extend(found)
            health[name] = None
        except Exception as error:  # one broken source must not stop the others
            health[name] = f"{type(error).__name__}: {error}"
            state["last_try"][name] = now.isoformat()
        time.sleep(PAUSE_SECONDS)

    def news(build_url, parser):
        found = []
        for query in config["news"]["queries"]:
            found += parser(fetch(build_url(query)), now)
            time.sleep(PAUSE_SECONDS)
        return found

    days = config["rules"]["max_age_days"]
    last_news = state.get("last_news_at")
    interval = timedelta(hours=config["collection"]["news_interval_hours"])
    if not last_news or now - datetime.fromisoformat(last_news) >= interval:
        state["last_news_at"] = now.isoformat()
        run("Bing 뉴스", lambda: news(bing_url, parse_bing))
        run("Google 뉴스", lambda: news(
            lambda q: "https://news.google.com/rss/search?" + urllib.parse.urlencode(
                {"q": f"{q} when:{days}d", "hl": "ko", "gl": "KR", "ceid": "KR:ko"}), parse_google))
    for feed in config["community"]["rss"]:
        run(feed["name"], lambda feed=feed: parse_rss(fetch(feed["url"]), feed["name"], now))
    run("클리앙 알뜰구매", lambda: parse_clien(fetch("https://www.clien.net/service/board/jirum"), now))
    return posts, health
