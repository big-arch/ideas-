from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

from .analysis import render, shortlist
from .bot import run_bot
from .config import load_config, load_env
from .demo import demo_listings
from .storage import Store, load_jsonl


def main() -> int:
    # Windows pipes can otherwise inherit a code page without ₽ or м².
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description="Радар объявлений — Ростовская область")
    parser.add_argument("--config", type=Path, default=Path("config.toml"))
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("demo", help="Анализ вымышленных данных без сети и токенов")
    sub.add_parser("digest", help="Сводка рабочей базы")
    sub.add_parser("bot", help="Запустить Telegram-бота")
    importer = sub.add_parser("import", help="Импорт файла JSONL")
    importer.add_argument("file", type=Path)
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    try:
        config = load_config(args.config)
        load_env(args.config.resolve().parent / ".env", override=True)
        if args.command == "demo":
            results = shortlist(demo_listings(), config.search)
            for result in results:
                print(render(result), end="\n\n")
            return 0
        store = Store(config.database)
        if args.command == "import":
            count = store.upsert(load_jsonl(args.file))
            print(f"Обновлено объявлений: {count}")
        elif args.command == "digest":
            results = shortlist(store.all(), config.search)
            for result in results:
                print(render(result), end="\n\n")
            if not results:
                print("Кандидатов нет. Нужен импорт свежих объявлений и базы аналогов.")
        else:
            run_bot(config, store)
        return 0
    except (ValueError, OSError) as error:
        logging.error("%s", error)
        return 1
    except KeyboardInterrupt:
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
