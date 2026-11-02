"""Keyword rules that decide whether a post is a 30%+ event anyone can join."""
import re
from dataclasses import dataclass
from datetime import date

PCT = re.compile(r"(\d{1,3}(?:\.\d+)?)\s*(?:%|퍼센트|프로)")
# Percentages that describe statistics rather than a benefit.
STAT_PCT = re.compile(r"\d{1,3}(?:\.\d+)?\s*(?:%|퍼센트|프로)\s*(?:증가|감소|성장|상승|하락|늘|줄|점유)")
# In summaries a percentage only counts next to a benefit word.
NEAR_BENEFIT_PCT = re.compile(r"(\d{1,3}(?:\.\d+)?)\s*(?:%|퍼센트|프로)\S{0,4}\s*(?:\S{0,6}\s*)?(?:적립|할인|캐시백|페이백|환급|돌려)")
GIVEAWAY = re.compile(r"(?<![\d,.])\d\s*\+\s*\d(?![\d,.])|원플러스원|원플원|투플러스원|투플원|증정")
HALF = re.compile(r"반값")
SPEND = re.compile(r"(\d[\d,]*)\s*(만|천)?\s*원\s*(?:이상\s*)?(?:쓰면|결제|구매|사용)")
GET = re.compile(r"(\d[\d,]*)\s*(만|천)?\s*원\s*(?:\S{0,6}\s*)?(?:적립|할인|캐시백|페이백|환급)")
CAP = re.compile(r"최대\s*(\d[\d,]*\s*(?:만|천)?\s*원)")
QUESTION = re.compile(r"\?\s*(?:\(.*\))?\s*$")

RANGE_FULL = re.compile(r"(\d{1,2})\s*[/.월]\s*(\d{1,2})\s*일?\s*[~∼-]\s*(?:(\d{1,2})\s*[/.월]\s*)?(\d{1,2})\s*일?")
RANGE_DAYS = re.compile(r"(?<![\d/.])(\d{1,2})\s*[~∼-]\s*(\d{1,2})\s*일")
UNTIL_FULL = re.compile(r"(\d{1,2})\s*월\s*(\d{1,2})\s*일\s*까지")
UNTIL_DAY = re.compile(r"(?<!\d)(\d{1,2})\s*일\s*까지")
NEXT_MONTH_UNTIL = re.compile(r"다음\s*달\s*(\d{1,2})\s*일\s*까지")
WHOLE_MONTH = re.compile(r"(\d{1,2})\s*월\s*한\s*달")


@dataclass
class Verdict:
    status: str  # "pass", "unconfirmed" or "drop"
    brand: str = ""
    rate: float | None = None
    kind: str = ""
    cap: str = ""
    period: str = ""
    ends: date | None = None


def _contains(text, words):
    lowered = text.lower()
    for word in words:
        if re.fullmatch(r"[A-Za-z0-9 ]+", word):
            if re.search(rf"(?<![A-Za-z0-9]){re.escape(word.lower())}(?![A-Za-z0-9])", lowered):
                return word
        elif word.lower() in lowered:
            return word
    return None


def _won(number, unit):
    return float(number.replace(",", "")) * {"만": 10000, "천": 1000}.get(unit, 1)


def _rates(title, summary):
    cleaned = STAT_PCT.sub(" ", title)
    rates = [float(p) for p in PCT.findall(cleaned)]
    if HALF.search(title):
        rates.append(50.0)
    spend, get = SPEND.search(title), GET.search(title)
    if spend and get and _won(*spend.groups()) > 0:
        rates.append(_won(*get.groups()) / _won(*spend.groups()) * 100)
    if not rates:
        rates = [float(p) for p in NEAR_BENEFIT_PCT.findall(summary)]
    return rates


def _safe_date(year, month, day):
    try:
        return date(year, month, day)
    except ValueError:
        return None


def _month_end(year, month):
    next_first = date(year + (month == 12), month % 12 + 1, 1)
    return date.fromordinal(next_first.toordinal() - 1)


def parse_period(text, published):
    """Returns (display text, end date). Years and months come from the publish date."""
    year, month = published.year, published.month
    match = RANGE_FULL.search(text)
    if match:
        start_month, start_day, end_month, end_day = match.groups()
        end_month = int(end_month or start_month)
        end_year = year + 1 if end_month < published.month - 6 else year
        return match.group(0).strip(), _safe_date(end_year, end_month, int(end_day))
    match = UNTIL_FULL.search(text)
    if match:
        end_month, end_day = int(match.group(1)), int(match.group(2))
        end_year = year + 1 if end_month < published.month - 6 else year
        return f"{end_month}월 {end_day}일까지", _safe_date(end_year, end_month, end_day)
    match = NEXT_MONTH_UNTIL.search(text)
    if match:
        next_year, next_month = (year + 1, 1) if month == 12 else (year, month + 1)
        day = int(match.group(1))
        return f"{next_month}월 {day}일까지", _safe_date(next_year, next_month, day)
    match = RANGE_DAYS.search(text)
    if match:
        return f"{month}월 {match.group(1)}~{match.group(2)}일", _safe_date(year, month, int(match.group(2)))
    match = UNTIL_DAY.search(text)
    if match:
        day = int(match.group(1))
        return f"{month}월 {day}일까지", _safe_date(year, month, day)
    match = WHOLE_MONTH.search(text)
    if match:
        end_month = int(match.group(1))
        return f"{end_month}월 한 달", _month_end(year, end_month)
    return "", None


def _kind(title):
    if "적립" in title or "캐시백" in title or "페이백" in title or "환급" in title or "돌려" in title:
        return "적립"
    return "할인"


def classify(title, summary, published, today, rules, keywords):
    """Judges one post. Only the title decides brand and benefit; the summary fills in details."""
    brand = _contains(title, keywords["brands"])
    if not brand:
        return Verdict("drop")
    if _contains(title, keywords["noise"]) or QUESTION.search(title):
        return Verdict("drop")
    if _contains(f"{title} {summary}", keywords["ineligible"]) or _contains(title, keywords["ineligible_title"]):
        return Verdict("drop")
    if not _contains(title, keywords["benefits"]) and not GIVEAWAY.search(title) and not SPEND.search(title):
        return Verdict("drop")

    rates = _rates(title, summary)
    if GIVEAWAY.search(title) and not PCT.search(title):
        return Verdict("drop")

    text = f"{title} {summary}"
    period, ends = parse_period(title, published)
    if not ends:
        period, ends = parse_period(summary, published)
    if ends and ends < today:
        return Verdict("drop")
    cap = CAP.search(text)
    details = {"brand": brand, "kind": _kind(title), "cap": cap.group(1).replace(" ", "") if cap else "",
               "period": period, "ends": ends}
    if not rates:
        return Verdict("unconfirmed", **details)
    best = max(rates)
    if best < rules["threshold"]:
        return Verdict("drop")
    return Verdict("pass", rate=round(best, 1), **details)
