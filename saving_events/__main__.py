"""Entry point: collect posts, update state, and send the digest when it is due."""
import argparse
import json
import os
import pathlib
import sys
import tomllib
from datetime import datetime, timedelta, timezone

from . import digest, llm, rules, sources

KST = timezone(timedelta(hours=9))
ROOT = pathlib.Path(__file__).resolve().parent.parent
FAILURE_ALERT_HOURS = 24
MAX_LOOKUPS_PER_RUN = 5
LLM_MEMORY = 300


def load_state(path):
    state = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
    for key, empty in (("events", []), ("last_period", ""), ("failing_since", {}), ("last_try", {}),
                       ("alerted_on", ""), ("last_news_at", ""), ("llm_checked", [])):
        state.setdefault(key, empty)
    return state


def save_state(path, state):
    path.parent.mkdir(exist_ok=True)
    path.write_text(json.dumps(state, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")


class RateReader:
    """Asks the optional LLM about posts without a rate. Stops for the run on the first failure."""

    def __init__(self, config, state, key):
        self.model = config["llm"]["model"]
        self.calls_left = config["llm"]["max_calls_per_run"] if key else 0
        self.threshold = config["rules"]["threshold"]
        self.state = state
        self.key = key

    def upgrade(self, post, verdict):
        if verdict.status != "unconfirmed" or self.calls_left <= 0 or post.url in self.state["llm_checked"]:
            return verdict
        self.calls_left -= 1
        try:
            rate = llm.read_rate(post.title, post.summary, self.model, self.key)
        except llm.LlmError as error:
            print(f"::warning::LLM을 쓸 수 없어 키워드 결과만 사용합니다: {error}")
            self.calls_left = 0
            return verdict
        self.state["llm_checked"] = (self.state["llm_checked"] + [post.url])[-LLM_MEMORY:]
        if rate is not None and rate >= self.threshold:
            verdict.status, verdict.rate = "pass", round(rate, 1)
        return verdict


def update_events(state, posts, config, now, reader):
    today = now.date()
    oldest = now - timedelta(days=config["rules"]["max_age_days"])
    include_unconfirmed = config["rules"]["include_unconfirmed"]
    stopwords = config["keywords"]["group_stopwords"]
    for post in sorted(posts, key=lambda p: not p.direct):
        if post.published < oldest:
            continue
        published = post.published.astimezone(KST).date()
        verdict = rules.classify(post.title, post.summary, published, today, config["rules"], config["keywords"])
        verdict = reader.upgrade(post, verdict)
        if verdict.status == "pass" or (verdict.status == "unconfirmed" and include_unconfirmed):
            digest.add_post(state["events"], post, verdict, today, stopwords)
    state["events"] = digest.prune(state["events"], today)
    add_direct_links(state["events"], now, stopwords)


def add_direct_links(events, now, stopwords):
    """Looks up the original article for events known only through an aggregator link."""
    lookups = [e for e in events if not e["lookup_done"] and not any(s["direct"] for s in e["sources"])]
    for event in lookups[:MAX_LOOKUPS_PER_RUN]:
        try:
            found = sources.parse_bing(sources.fetch(sources.bing_url(event["title"])), now)
        except Exception as error:  # the aggregator link still works, so try again next run
            print(f"::warning::원문 링크 찾기 실패: {error}")
            return
        event["lookup_done"] = True
        source = digest.find_direct_source(event, found, stopwords)
        if source:
            event["sources"].append(source)


def track_health(state, health, now):
    """Remembers since when each source fails. Returns sources failing for a full day, once per day."""
    failing = state["failing_since"]
    for name, error in health.items():
        if error:
            print(f"::warning::{name} 수집 실패: {error}")
            failing.setdefault(name, now.isoformat())
        else:
            failing.pop(name, None)
    limit = now - timedelta(hours=FAILURE_ALERT_HOURS)
    broken = [name for name, since in failing.items() if datetime.fromisoformat(since) <= limit]
    today = now.date().isoformat()
    if broken and state["alerted_on"] != today:
        state["alerted_on"] = today
        return broken
    return []


def pending_events(state, now, include_sent):
    today = now.date().isoformat()
    return [e for e in state["events"] if (include_sent or not e["sent_on"]) and (not e["ends"] or e["ends"] >= today)]


def deliver(events, now, recipient):
    """Sends events. Returns False if the password is missing, so nothing gets marked as sent."""
    password = os.environ.get("MAIL_APP_PASSWORD")
    if not password:
        print("::warning::MAIL_APP_PASSWORD가 없어 메일을 보내지 못했습니다.")
        return False
    if events:
        digest.send(*digest.render(events), recipient, password, recipient)
        for event in events:
            event["sent_on"] = now.date().isoformat()
    print(f"보낸 행사: {len(events)}건")
    return True


def main():
    parser = argparse.ArgumentParser(prog="saving_events")
    parser.add_argument("--send-now", action="store_true", help="전송 시각과 상관없이 지금 보냅니다")
    parser.add_argument("--include-sent", action="store_true", help="이미 보낸 진행 중 행사도 다시 보냅니다")
    parser.add_argument("--preview", action="store_true", help="메일을 보내지 않고 내용만 출력합니다")
    parser.add_argument("--config", default=str(ROOT / "config.toml"))
    parser.add_argument("--state", default=str(ROOT / "data" / "state.json"))
    args = parser.parse_args()

    config = tomllib.loads(pathlib.Path(args.config).read_text(encoding="utf-8"))
    state_path = pathlib.Path(args.state)
    state = load_state(state_path)
    now = datetime.now(KST)

    posts, health = sources.collect(config, now, state)
    update_events(state, posts, config, now, RateReader(config, state, os.environ.get("GEMINI_API_KEY")))
    broken = track_health(state, health, now)
    print(f"읽은 글 {len(posts)}건, 보관 중인 행사 {len(state['events'])}건")

    if args.preview:
        print(*digest.render(pending_events(state, now, args.include_sent))[:2], sep="\n\n")
    else:
        key = digest.period_key(now, config["delivery"])
        due = key is not None and state["last_period"] != key
        if args.send_now or due:
            events = pending_events(state, now, args.include_sent)
            if deliver(events, now, config["delivery"]["recipient"]) and due:
                state["last_period"] = key
    save_state(state_path, state)

    if broken:
        print(f"::error::24시간 이상 실패 중인 소스: {', '.join(broken)}")
        sys.exit(1)


if __name__ == "__main__":
    main()
