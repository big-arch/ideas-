from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass
from pathlib import Path

from .models import CATEGORIES


@dataclass(frozen=True)
class Search:
    region: str = "Ростовская область"
    locality: str = ""
    discount: float = 0.10
    min_comparables: int = 5
    max_age_days: int = 30
    area_tolerance: float = 0.20
    category: str = "garage"

    def __post_init__(self) -> None:
        if self.category not in CATEGORIES:
            raise ValueError("Неизвестная категория")
        if not 0 < self.discount < 1 or not 0 < self.area_tolerance <= 0.5:
            raise ValueError("Некорректный порог скидки или площади")
        if self.min_comparables < 3 or not 1 <= self.max_age_days <= 365:
            raise ValueError("Некорректный размер выборки или срок актуальности")


@dataclass(frozen=True)
class Config:
    search: Search
    database: Path
    feed_file: Path | None
    poll_seconds: int
    source: str = "file"
    source_refresh_seconds: int = 900


def load_config(path: Path) -> Config:
    with path.open("rb") as stream:
        data = tomllib.load(stream)
    root = path.resolve().parent
    runtime = data.get("runtime", {})
    period = int(runtime.get("poll_seconds", 60))
    if period < 10:
        raise ValueError("Интервал проверки должен быть не меньше 10 секунд")
    feed = runtime.get("feed_file", "")
    source = data.get("source", {})
    provider = source.get("provider", "file")
    if provider not in {"file", "avito_public", "public_catalogs"}:
        raise ValueError("Неизвестный источник данных")
    return Config(Search(**data.get("search", {})), root / runtime.get("database", "data/radar.sqlite3"),
                  root / feed if feed else None, period, provider, int(source.get("refresh_seconds", 900)))


def load_env(path: Path, *, override: bool = False) -> None:
    if not path.exists():
        return
    for raw in path.read_text(encoding="utf-8-sig").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        key, sep, value = line.partition("=")
        if not sep or not key.strip().isidentifier():
            raise ValueError("Некорректная строка в .env")
        key, value = key.strip(), value.strip().strip("\"'")
        if override or key not in os.environ:
            os.environ[key] = value
