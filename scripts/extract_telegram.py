#!/usr/bin/env python3
"""Извлекает корпус сообщений одного автора из JSON-экспорта Telegram Desktop.

Как получить экспорт: Telegram Desktop → Настройки → Продвинутые настройки →
Экспорт данных Telegram → формат «Машиночитаемый JSON». Поддерживаются оба
варианта: экспорт одного чата (result.json с ключом "messages") и полный
экспорт аккаунта (ключ "chats" → "list").

Зависимостей нет (только stdlib).

Использование:
    python3 extract_telegram.py result.json                    # показать авторов
    python3 extract_telegram.py result.json --from "Имя"       # корпус в stdout
    python3 extract_telegram.py result.json --from "Имя" -o /tmp/corpus.txt

Фильтрация: берёт только обычные сообщения указанного автора; пропускает
сервисные записи, форварды, медиа без текста и сообщения, в которых после
удаления URL остаётся меньше --min-len символов (по умолчанию 30).

Вывод — образцы через разделитель `---`, готовый вход для analyze_corpus.py.
"""

import argparse
import json
import re
import sys
from collections import Counter

URL_RE = re.compile(r"https?://\S+")


def flatten_text(text):
    """Поле text у Telegram — строка или список из строк и entity-словарей."""
    if isinstance(text, str):
        return text
    parts = []
    for item in text:
        if isinstance(item, str):
            parts.append(item)
        elif isinstance(item, dict):
            parts.append(item.get("text", ""))
    return "".join(parts)


def iter_messages(data):
    if "messages" in data:
        yield from data["messages"]
    elif "chats" in data:
        for chat in data["chats"].get("list", []):
            yield from chat.get("messages", [])


def main():
    ap = argparse.ArgumentParser(
        description="Корпус сообщений одного автора из экспорта Telegram Desktop."
    )
    ap.add_argument("path", help="путь к result.json")
    ap.add_argument("--from", dest="sender",
                    help="имя автора как в поле from; без флага — список авторов")
    ap.add_argument("--min-len", type=int, default=30,
                    help="мин. длина текста без URL (default: 30)")
    ap.add_argument("--max-samples", type=int, default=0,
                    help="взять N самых свежих образцов (0 = все)")
    ap.add_argument("-o", "--output", default="-",
                    help="файл вывода (default: stdout)")
    args = ap.parse_args()

    with open(args.path, encoding="utf-8") as fh:
        data = json.load(fh)
    messages = [m for m in iter_messages(data) if m.get("type") == "message"]

    if not messages:
        print("В экспорте нет сообщений (ожидался JSON Telegram Desktop).", file=sys.stderr)
        sys.exit(1)

    if not args.sender:
        counts = Counter(m.get("from") for m in messages if m.get("from"))
        print('Авторы в экспорте — укажи --from "Имя":', file=sys.stderr)
        for name, n in counts.most_common(20):
            print(f"  {n:6d}  {name}", file=sys.stderr)
        sys.exit(0)

    samples = []
    for m in messages:
        if m.get("from") != args.sender:
            continue
        if m.get("forwarded_from"):
            continue
        text = flatten_text(m.get("text", "")).strip()
        if len(URL_RE.sub("", text).strip()) < args.min_len:
            continue
        samples.append(text)

    if not samples:
        print(f"Не найдено сообщений от «{args.sender}» длиннее {args.min_len} символов.",
              file=sys.stderr)
        sys.exit(1)

    if args.max_samples and len(samples) > args.max_samples:
        samples = samples[-args.max_samples:]

    out = "\n---\n".join(samples) + "\n"
    if args.output == "-":
        sys.stdout.write(out)
    else:
        with open(args.output, "w", encoding="utf-8") as fh:
            fh.write(out)
    avg = sum(len(s) for s in samples) / len(samples)
    print(f"Образцов: {len(samples)}  |  средняя длина: {avg:.0f} символов"
          + (f"  →  {args.output}" if args.output != "-" else ""),
          file=sys.stderr)


if __name__ == "__main__":
    main()
