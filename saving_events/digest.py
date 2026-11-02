"""Groups articles into events, keeps delivery schedule, and writes the email."""
import html
import re
import smtplib
from datetime import date, datetime, timedelta
from email.message import EmailMessage

WEEKDAYS = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]
MAX_SOURCES_PER_EVENT = 3


def tokens(title, stopwords):
    words = re.findall(r"[0-9A-Za-z가-힣]+", title.lower())
    stop = {w.lower() for w in stopwords}
    return sorted({w for w in words if len(w) >= 2 and not re.fullmatch(r"\d+", w) and w not in stop
                   and not re.fullmatch(r"\d+(%|만원|원|일|월)?", w)})


def _shares_token(left, right):
    return any(a == b or (len(a) >= 3 and len(b) >= 3 and (a in b or b in a)) for a in left for b in right)


def find_group(events, brand_group, rate, words):
    for event in events:
        if event["brand_group"] == brand_group and event["rate"] == rate and _shares_token(event["tokens"], words):
            return event
    return None


def brand_group(brand):
    lowered = brand.lower().replace(" ", "")
    if lowered in ("네이버페이", "네이버머니", "npay", "n페이", "엔페이"):
        return "네이버페이"
    if lowered in ("삼성월렛", "삼성페이", "월렛머니"):
        return "삼성월렛"
    return brand


def add_post(events, post, verdict, today, stopwords):
    """Adds a post to its event, creating one if needed. Returns the event."""
    group = brand_group(verdict.brand)
    words = tokens(post.title, stopwords)
    event = find_group(events, group, verdict.rate, words)
    if event is None:
        event = {"brand_group": group, "rate": verdict.rate, "kind": verdict.kind, "title": post.title,
                 "cap": verdict.cap, "period": verdict.period,
                 "ends": verdict.ends.isoformat() if verdict.ends else "",
                 "tokens": words, "sources": [], "first_seen": today.isoformat(), "sent_on": "",
                 "published": post.published.isoformat(), "lookup_done": False}
        events.append(event)
    event["cap"] = event["cap"] or verdict.cap
    if not event["period"] and verdict.period:
        event["period"] = verdict.period
        event["ends"] = verdict.ends.isoformat() if verdict.ends else ""
    event["tokens"] = sorted(set(event["tokens"]) | set(words))
    if not any(s["url"] == post.url for s in event["sources"]):
        event["sources"].append({"name": post.source, "url": post.url, "direct": post.direct})
    return event


def find_direct_source(event, candidates, stopwords, max_days_apart=3):
    """Picks the article about this event from search results, to replace an aggregator link."""
    published = datetime.fromisoformat(event["published"])
    for post in candidates:
        close_in_time = abs(post.published - published) <= timedelta(days=max_days_apart)
        words = tokens(post.title, stopwords)
        shared = [w for w in event["tokens"] if _shares_token([w], words)]
        if post.direct and close_in_time and len(shared) >= 2:
            return {"name": post.source, "url": post.url, "direct": True}
    return None


def prune(events, today, keep_days=45):
    """Forgets events that have ended, and old ones so they can come back in a later month."""
    cutoff = today - timedelta(days=keep_days)
    kept = []
    for event in events:
        if date.fromisoformat(event["first_seen"]) < cutoff:
            continue
        if event["ends"] and date.fromisoformat(event["ends"]) < today and not event["sent_on"]:
            continue
        kept.append(event)
    return kept


def period_key(now, delivery):
    """Returns this delivery period's key when sending is due now, otherwise None."""
    mode = delivery["mode"]
    if mode == "manual" or now.hour < delivery["hour"]:
        return None
    if mode == "daily":
        return now.date().isoformat()
    if mode == "weekly":
        if now.weekday() != WEEKDAYS.index(delivery["weekday"]):
            return None
        year, week, _ = now.isocalendar()
        return f"{year}-W{week:02d}"
    if mode == "monthly":
        if now.day < delivery["day_of_month"]:
            return None
        return now.strftime("%Y-%m")
    raise ValueError(f"알 수 없는 전송 방식: {mode}")


def _facts(event):
    if event["rate"] is None:
        rate = "할인율 확인 필요"
    else:
        rate = f"{event['rate']:g}% {event['kind']}"
    facts = [f"대상: {event['brand_group']}", f"혜택: {rate}"]
    if event["cap"]:
        facts.append(f"한도: 최대 {event['cap']}")
    if event["period"]:
        facts.append(f"기간: {event['period']}")
    return facts


def _ordered_sources(event):
    return sorted(event["sources"], key=lambda s: not s["direct"])[:MAX_SOURCES_PER_EVENT]


def _sorted(events):
    return sorted(events, key=lambda e: (e["rate"] is None, -(e["rate"] or 0), e["ends"] or "9999"))


def render(events):
    """Returns (subject, plain text, html). The body holds event facts and sources only."""
    ordered = _sorted(events)
    plain, rich = [], []
    for event in ordered:
        facts = _facts(event)
        sources = _ordered_sources(event)
        plain.append("\n".join([event["title"], *facts, *(f"출처: {s['name']} {s['url']}" for s in sources)]))
        links = " · ".join(f'<a href="{html.escape(s["url"])}">{html.escape(s["name"])}</a>' for s in sources)
        rich.append(f"<h3>{html.escape(event['title'])}</h3><p>{'<br>'.join(html.escape(f) for f in facts)}"
                    f"<br>출처: {links}</p>")
    return f"30% 이상 행사 {len(ordered)}건", "\n\n".join(plain), "".join(rich)


def send(subject, plain, rich, username, password, recipient):
    message = EmailMessage()
    message["Subject"] = subject
    message["From"] = username
    message["To"] = recipient
    message.set_content(plain)
    message.add_alternative(f"<html><body>{rich}</body></html>", subtype="html")
    with smtplib.SMTP_SSL("smtp.gmail.com", 465, timeout=30) as server:
        server.login(username, password)
        server.send_message(message)
