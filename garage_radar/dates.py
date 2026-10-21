"""Dates shown by listing sites; never substitute the crawler observation time."""
import re
from datetime import datetime, timedelta, timezone

MSK = timezone(timedelta(hours=3))
MONTHS = {m: i + 1 for i, m in enumerate(("января", "февраля", "марта", "апреля", "мая", "июня",
          "июля", "августа", "сентября", "октября", "ноября", "декабря"))}


def site_date(text: str, observed: datetime) -> str:
    text = " ".join(text.lower().split())
    now = observed.astimezone(MSK)
    day = now.replace(hour=0, minute=0, second=0, microsecond=0)
    if re.fullmatch(r"(?:только что|несколько секунд назад)", text): return observed.isoformat()
    relative = re.fullmatch(r"(\d+)\s+(минут\w*|час\w*|дн\w*|день)\s+назад", text)
    if relative:
        n, unit = int(relative[1]), relative[2]
        delta = timedelta(minutes=n) if unit.startswith("мин") else timedelta(hours=n) if unit.startswith("час") else timedelta(days=n)
        return (now - delta).isoformat()
    if re.fullmatch(r"сегодня(?:[, ]+\d{1,2}:\d{2})?", text): return day.isoformat()
    if re.fullmatch(r"вчера(?:[, ]+\d{1,2}:\d{2})?", text): return (day - timedelta(days=1)).isoformat()
    match = re.fullmatch(r"(\d{1,2})\s+(" + "|".join(MONTHS) + r")(?:\s+(20\d{2}))?(?:[, ]+\d{1,2}:\d{2})?", text)
    if not match: return ""
    try:
        value = day.replace(year=int(match[3] or now.year), month=MONTHS[match[2]], day=int(match[1]))
        if not match[3] and value > now: value = value.replace(year=value.year - 1)
        return value.isoformat() if value <= now else ""
    except ValueError:
        return ""
