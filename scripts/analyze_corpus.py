#!/usr/bin/env python3
"""Анализ корпуса писательских образцов для извлечения Voice DNA.

Детерминированно считает то, что человек не сможет посчитать на глаз:
частоту вводных слов, среднюю длину, ритм предложений, маркеры иронии,
пунктуационные привычки. Результат — markdown-отчёт, который модель
использует, чтобы заполнить шаблон персонального скилла.

Зависимостей нет (только stdlib). Язык корпуса любой.

Использование:
    python3 analyze_corpus.py samples.txt
    cat samples.txt | python3 analyze_corpus.py -

Разделитель образцов: строка из одних дефисов/равно (--- или ===),
иначе — пустая строка. Если ничего не найдено, каждая строка = образец.
"""

import re
import sys
from collections import Counter

# Вводные/маркерные слова, за которыми стоит следить отдельно (RU + EN).
# Это не баны, а кандидаты в "подпись голоса" — то, что человек повторяет.
FILLER_CANDIDATES_RU = [
    "ну", "вот", "короче", "типа", "ребят", "ага", "ну да", "вроде",
    "кажется", "похоже", "блин", "ладно", "слушай", "смотри", "в общем",
    "то есть", "как бы", "честно", "по сути", "на самом деле",
]
FILLER_CANDIDATES_EN = [
    "well", "so", "anyway", "honestly", "kinda", "actually", "basically",
    "i mean", "look", "right", "ok", "yeah", "tbh", "like",
]

# Маркеры иронии/тона — эмодзи и текстовые.
TEXT_MARKERS = ["¯\\_(ツ)_/¯", ":)", ":(", "))", ")))", ":-)", "xD", "P.S.", "PS:", "P.S"]

EMOJI_RE = re.compile(
    "["
    "\U0001F300-\U0001FAFF"
    "\U00002600-\U000027BF"
    "\U0001F000-\U0001F0FF"
    "\U00002190-\U000021FF"
    "\U00002B00-\U00002BFF"
    "️"
    "]",
    flags=re.UNICODE,
)

WORD_RE = re.compile(r"[\w'’-]+", re.UNICODE)
SENT_SPLIT_RE = re.compile(r"[.!?…]+(?:\s|$)")

# Стоп-слова, чтобы топ частотных слов был осмысленным (RU + EN, базовый набор).
STOPWORDS = set("""
и в во не что он на я с со как а то все она так его но да ты к у же вы за бы по
только ее мне было вот от меня еще нет о из ему теперь когда даже ну вдруг ли если
уже или ни быть был него до вас нибудь опять уж вам ведь там потом себя ничего ей
может они тут где есть надо ней для мы тебя их чем была сам чтоб без будто чего раз
тоже себе под будет ж кто этот того потому этого какой совсем ним здесь этом один
почти мой тем чтобы нее сейчас были куда зачем всех никогда можно при наконец два об
другой хоть после над больше тот через эти нас про всего них какая много разве три
эту моя впрочем хорошо свою этой перед иногда лучше чуть том нельзя такой им более
всегда конечно всю между это
the a an and or but to of in on at for with is are was were be been being this that
these those it its as by from he she they we you i me my your our their his her him
not no do does did have has had will would can could should may might must just so
""".split())


def read_input(path):
    if path == "-":
        return sys.stdin.read()
    with open(path, encoding="utf-8") as fh:
        return fh.read()


def split_samples(text):
    if re.search(r"^[-=]{3,}\s*$", text, re.M):
        parts = re.split(r"^[-=]{3,}\s*$", text, flags=re.M)
    elif "\n\n" in text:
        parts = text.split("\n\n")
    else:
        parts = text.splitlines()
    return [p.strip() for p in parts if p.strip()]


def count_markers(text, markers):
    found = Counter()
    for m in markers:
        n = text.count(m)
        if n:
            found[m] = n
    return found


def phrase_counts(text_lower, phrases):
    found = Counter()
    for p in phrases:
        # границы слова для коротких токенов, чтобы "ну" не ловило "нужно"
        n = len(re.findall(r"(?<!\w)" + re.escape(p) + r"(?!\w)", text_lower))
        if n:
            found[p] = n
    return found


def pct(n, total):
    return f"{(100.0 * n / total):.0f}%" if total else "0%"


def main():
    if len(sys.argv) < 2:
        print("usage: analyze_corpus.py <file|->", file=sys.stderr)
        sys.exit(1)

    raw = read_input(sys.argv[1])
    samples = split_samples(raw)
    if not samples:
        print("Корпус пуст — нечего анализировать.", file=sys.stderr)
        sys.exit(1)

    joined = "\n".join(samples)
    lower = joined.lower()
    total_samples = len(samples)

    lengths = [len(s) for s in samples]
    avg_len = sum(lengths) / total_samples
    median_len = sorted(lengths)[total_samples // 2]

    # Ритм предложений
    sent_lengths = []
    for s in samples:
        for part in SENT_SPLIT_RE.split(s):
            part = part.strip()
            if part:
                sent_lengths.append(len(WORD_RE.findall(part)))
    avg_sent = sum(sent_lengths) / len(sent_lengths) if sent_lengths else 0
    short_sents = sum(1 for x in sent_lengths if x <= 5)
    long_sents = sum(1 for x in sent_lengths if x >= 20)

    # Частотные слова (без стоп-слов)
    words = [w.lower() for w in WORD_RE.findall(lower) if len(w) > 2]
    content_words = [w for w in words if w not in STOPWORDS]
    top_words = Counter(content_words).most_common(25)

    fillers = phrase_counts(lower, FILLER_CANDIDATES_RU + FILLER_CANDIDATES_EN)
    markers = count_markers(joined, TEXT_MARKERS)
    emojis = Counter(EMOJI_RE.findall(joined))

    # Пунктуационные привычки
    em_dash = joined.count("—") + joined.count("--")
    ellipsis = joined.count("...") + joined.count("…")
    parens = min(joined.count("("), joined.count(")"))
    exclaim = joined.count("!")
    question = joined.count("?")
    contractions = len(re.findall(r"\b\w+['’]\w+\b", joined))

    out = []
    w = out.append
    w("# Voice DNA — отчёт по корпусу\n")
    w(f"**Образцов:** {total_samples}  |  **Символов всего:** {len(joined)}\n")

    w("## Длина и ритм")
    w(f"- Средняя длина образца: **{avg_len:.0f}** символов (медиана {median_len})")
    w(f"- Средняя длина предложения: **{avg_sent:.1f}** слов")
    w(f"- Коротких предложений (≤5 слов): {short_sents} ({pct(short_sents, len(sent_lengths))})")
    w(f"- Длинных предложений (≥20 слов): {long_sents} ({pct(long_sents, len(sent_lengths))})")
    w("  → высокая доля и тех и других = намеренная игра длиной (хорошо для голоса)\n")

    w("## Вводные / слова-подписи (кандидаты в DNA)")
    if fillers:
        for word, n in fillers.most_common():
            w(f"- `{word}` — {n} раз")
    else:
        w("- не найдено заметных вводных — голос, видимо, более «сухой»")
    w("")

    w("## Маркеры тона / иронии")
    if markers or emojis:
        for m, n in markers.most_common():
            w(f"- `{m}` — {n} раз")
        for e, n in emojis.most_common(10):
            w(f"- {e} — {n} раз")
    else:
        w("- маркеров иронии/эмодзи не найдено — нейтральный регистр")
    w("")

    w("## Пунктуационные привычки")
    w(f"- Тире (—/--): {em_dash}  |  Многоточие: {ellipsis}  |  Скобки-вставки: {parens}")
    w(f"- Восклицания: {exclaim}  |  Вопросы: {question}  |  Сокращения (don't/it's): {contractions}")
    w("")

    w("## Топ частотных слов (без стоп-слов)")
    w(", ".join(f"{wd}×{n}" for wd, n in top_words) or "—")
    w("")

    w("## Подсказки для генерации скилла")
    if avg_len < 200:
        w("- Короткий формат доминирует → основной канал «чат/реплики».")
    else:
        w("- Длинный формат → основной канал «посты/лонгриды».")
    if fillers:
        top_f = ", ".join(f"«{x}»" for x, _ in fillers.most_common(4))
        w(f"- Обязательные вводные в финальном скилле: {top_f}.")
    if emojis or markers:
        top_m = ", ".join(f"`{x}`" for x, _ in (markers + emojis).most_common(3))
        w(f"- Маркеры иронии для тональности «ироничнее»: {top_m}.")
    if em_dash > total_samples:
        w("- Автор САМ активно использует тире → не баним тире жёстко, только защищаем от AI-накопления.")
    print("\n".join(out))


if __name__ == "__main__":
    main()
