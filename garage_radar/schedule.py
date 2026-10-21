"""Moscow digest slots. Persist slot claims before starting delivery."""
from datetime import datetime, timedelta, timezone

MOSCOW = timezone(timedelta(hours=3))
SCHEDULES = {"once": (9,), "twice": (9, 19), "twelve": tuple(range(0, 24, 2)), "off": ()}
SCHEDULE_LABELS = {"once": "1 раз · 09:00", "twice": "2 раза · 09:00 и 19:00",
                   "twelve": "12 раз · каждые 2 часа, включая ночь", "off": "Автосводки выключены"}


def due_slot(mode: str, now: datetime) -> str | None:
    if now.tzinfo is None:
        raise ValueError("Нужен часовой пояс")
    local = now.astimezone(MOSCOW)
    # A stopped PC does not trigger a flood of catch-up digests.
    if local.hour in SCHEDULES.get(mode, ()) and local.minute < 30:
        return local.strftime("%Y-%m-%dT%H:00+03:00")
    return None
