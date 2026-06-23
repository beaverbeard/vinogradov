#!/usr/bin/env python3
"""Анализ корпуса писательских образцов для извлечения Voice DNA (идиостиля).

Детерминированно считает то, что человек не посчитает на глаз. Отчёт разложен по
четырём слоям идиостиля В.В. Виноградова — это каркас voice-DNA-скила:

  1. Лексический   — словарь, функциональные слова (подпись), обороты, богатство.
  2. Синтаксический — длина и ритм предложений (burstiness), пунктуация.
  3. Композиционный — абзацы, зачины, концовки.
  4. Образ автора   — маркеры тона/иронии, регистр (сигналы, не вердикт).

Принцип: скрипт считает СИГНАЛЫ, интерпретацию (модели аргументации, домены
метафор, off-brand) делает LLM по on-brand-фрагментам. Сюда не пихаем хрупкие
регулярки-«детекторы смысла».

Анти-шум:
  - n-граммы фильтруются по document-frequency (в скольких РАЗНЫХ образцах
    встречаются) — иначе фраза, повторённая в одном посте, лезет в «подпись»;
  - функциональные слова даются как ОТКЛОНЕНИЕ от нормы русского (мини-Burrows):
    различает не сырая частота «и/в/не», а над/недо-употребление;
  - стрелки и вариейшн-селекторы не считаются маркерами иронии.

Зависимостей нет (только stdlib). Язык корпуса любой (списки — RU-first + EN).

Использование:
    python3 analyze_corpus.py samples.txt
    cat samples.txt | python3 analyze_corpus.py -

Разделитель образцов: строка из одних дефисов/равно (--- или ===),
иначе — пустая строка. Если ничего не найдено, каждая строка = образец.
"""

import math
import re
import sys
from collections import Counter

# Дискурсивные маркеры / вводные (RU + EN) — кандидаты в "подпись голоса".
FILLER_CANDIDATES_RU = [
    "ну", "вот", "короче", "типа", "ребят", "ага", "ну да", "вроде",
    "кажется", "похоже", "блин", "ладно", "слушай", "смотри", "в общем",
    "то есть", "как бы", "честно", "по сути", "на самом деле", "кстати",
    "получается", "это я к чему", "вот в чём дело", "как я уже говорил",
]
FILLER_CANDIDATES_EN = [
    "well", "so", "anyway", "honestly", "kinda", "actually", "basically",
    "i mean", "look", "right", "ok", "yeah", "tbh", "like",
]

# Базовая частота функциональных слов в РЯ (‰, на 1000 токенов) — приблизительно,
# по частотным словарям (НКРЯ / Ляшевская-Шаров). Нужна, чтобы считать ОТКЛОНЕНИЕ:
# различает автора не сырая частота служебного слова, а над/недо-употребление
# относительно нормы (упрощённый Burrows' Delta без полноценной эталонной выборки).
BASELINE_FREQ = {
    "и": 35.0, "в": 31.0, "не": 18.0, "на": 15.0, "что": 14.0, "с": 12.5,
    "я": 11.0, "как": 9.0, "он": 9.0, "а": 8.5, "к": 7.0, "по": 7.0,
    "это": 6.5, "но": 5.5, "то": 5.0, "из": 5.0, "у": 5.0, "за": 4.5,
    "так": 4.5, "же": 4.5, "мы": 4.5, "от": 4.0, "вы": 4.0, "для": 4.0,
    "если": 4.0, "бы": 3.5, "уже": 3.0, "только": 3.0, "ещё": 3.0,
    "чтобы": 3.0, "вот": 1.5, "ну": 1.0,
}

# Маркеры иронии/тона — эмодзи и текстовые.
TEXT_MARKERS = ["¯\\_(ツ)_/¯", ":)", ":(", "))", ")))", ":-)", "xD", "P.S.", "PS:", "P.S"]

# Регистровые маркеры — сигнал, в каком пласте лексики живёт голос.
REGISTER_COLLOQUIAL = [
    "блин", "фигня", "херня", "хрень", "нафиг", "нафига", "дофига", "дохрена",
    "офигенно", "крутой", "крутая", "круто", "чувак", "штука", "прикол",
    "бомба", "жесть", "капец", "норм", "щас", "че", "чё", "ваще",
]
REGISTER_FORMAL = [
    "является", "осуществляется", "осуществлять", "данный", "данном",
    "следует", "необходимо", "посредством", "вследствие", "в целях",
    "реализация", "обеспечение", "способствует", "представляет собой",
]

# Зачины-сигналы: начать предложение с контраста/связки — это голос,
# а не «в/на/и» (просто частые предлоги). Их выделяем отдельно.
CONTRAST_OPENERS = {"но", "а", "и", "если", "это", "поэтому", "кстати", "ну",
                    "вот", "зато", "однако", "хотя", "потому", "значит", "так"}

# Эмодзи БЕЗ стрелок (стрелки = структура, не тон) и без служебных кодпоинтов.
EMOJI_RE = re.compile(
    "["
    "\U0001F300-\U0001FAFF"
    "\U00002600-\U000026FF"
    "\U00002700-\U000027BF"
    "\U0001F000-\U0001F0FF"
    "\U00002B00-\U00002BFF"
    "]",
    flags=re.UNICODE,
)
ARROW_RE = re.compile("[←-⇿]", flags=re.UNICODE)  # блок стрелок (→ ← ↔ …)
EMOJI_SKIP = {"️", "︎", "‍"}  # variation selectors, ZWJ — не эмодзи

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


def ngram_counts(samples, n, min_count=3, min_df=2):
    """Частотные n-граммы по предложениям с фильтром document-frequency.

    Возвращает (gram, count, df), где df = число РАЗНЫХ образцов, в которых
    встретился оборот. Фраза, повторённая внутри одного поста (df=1), —
    топик-шум, а не голос: отсекаем по min_df. N-граммы из одних стоп-слов
    тоже отбрасываем.
    """
    total = Counter()
    doc = Counter()
    for s in samples:
        seen = set()
        for sent in SENT_SPLIT_RE.split(s.lower()):
            tokens = WORD_RE.findall(sent)
            for i in range(len(tokens) - n + 1):
                gram = tokens[i:i + n]
                if all(t in STOPWORDS for t in gram):
                    continue
                g = " ".join(gram)
                total[g] += 1
                seen.add(g)
        for g in seen:
            doc[g] += 1
    res = [(g, c, doc[g]) for g, c in total.most_common()
           if c >= min_count and doc[g] >= min_df]
    return res


def sentence_openers(samples, min_count=2):
    """Первые слова предложений — сильный маркер голоса (как автор открывает мысль)."""
    openers = Counter()
    total = 0
    for s in samples:
        for sent in SENT_SPLIT_RE.split(s):
            tokens = WORD_RE.findall(sent)
            if tokens:
                openers[tokens[0].lower()] += 1
                total += 1
    top = [(w, c) for w, c in openers.most_common(12) if c >= min_count]
    return top, total


def classify_ending(sample):
    """Чем автор заканчивает образец: точка, смайл, ничего — это привычка."""
    tail = sample.rstrip()
    if not tail:
        return "пусто"
    if tail.endswith("...") or tail[-1] == "…":
        return "многоточие"
    if EMOJI_RE.match(tail[-1]):
        return "эмодзи"
    if tail[-1] == ")":
        return "скобка/смайл"
    if tail[-1] == ".":
        return "точка"
    if tail[-1] == "!":
        return "восклицание"
    if tail[-1] == "?":
        return "вопрос"
    return "без знака"


def sentence_lengths(samples):
    """Длины всех предложений в словах — основа ритма."""
    lengths = []
    for s in samples:
        for part in SENT_SPLIT_RE.split(s):
            part = part.strip()
            if part:
                lengths.append(len(WORD_RE.findall(part)))
    return [x for x in lengths if x]


def burstiness(lengths):
    """Ритм: среднее, σ, коэффициент вариации CV=σ/μ.

    CV — стилометрически сильнее среднего: ловит «игру длиной» (чередование
    рубленых и длинных фраз), которую среднее прячет. CV<0.5 — ровный ритм,
    CV>0.8 — выраженная игра длиной.
    """
    n = len(lengths)
    if n < 2:
        return {"mean": (lengths[0] if lengths else 0), "std": 0.0, "cv": 0.0}
    mean = sum(lengths) / n
    var = sum((x - mean) ** 2 for x in lengths) / n
    std = var ** 0.5
    return {"mean": mean, "std": std, "cv": (std / mean if mean else 0.0)}


def lexical_richness(all_words):
    """Богатство словаря: TTR (с поправкой на длину), hapax-доля, Yule's K."""
    n = len(all_words)
    freq = Counter(all_words)
    v = len(freq)
    if n < 2 or v < 1:
        return {"tokens": n, "types": v, "ttr": 0, "ttr_norm": 0, "hapax_ratio": 0, "yule_k": 0}
    hapax = sum(1 for c in freq.values() if c == 1)
    sum_f2 = sum(c * c for c in freq.values())
    yule_k = 10000 * (sum_f2 - n) / (n * n)
    return {
        "tokens": n,
        "types": v,
        "ttr": v / n,
        "ttr_norm": math.log(v) / math.log(n),
        "hapax_ratio": hapax / v,
        "yule_k": yule_k,
    }


def function_word_deviation(all_words, min_count=8):
    """Над/недо-употребление служебных слов против нормы РЯ (мини-Burrows).

    Возвращает (over, under): списки (слово, ‰набл, отношение к норме). Сырая
    частота «и/в/не» бесполезна (она высока у всех) — различает отклонение.
    """
    n = len(all_words)
    if not n:
        return [], []
    freq = Counter(all_words)
    rows = []
    for word, base in BASELINE_FREQ.items():
        obs = 1000.0 * freq.get(word, 0) / n
        if freq.get(word, 0) >= min_count and base > 0:
            rows.append((word, obs, obs / base))
    over = sorted([r for r in rows if r[2] >= 1.4], key=lambda r: -r[2])
    under = sorted([r for r in rows if r[2] <= 0.6], key=lambda r: r[2])
    return over, under


def paragraph_stats(samples):
    """Композиция: ритм абзацев. Доля одно-предложенческих абзацев = «дышит»."""
    paras = []
    for s in samples:
        for p in re.split(r"\n\s*\n", s):
            p = p.strip()
            if p:
                paras.append(p)
    if not paras:
        return None
    sent_per_para = []
    for p in paras:
        sents = [x for x in SENT_SPLIT_RE.split(p) if x.strip()]
        sent_per_para.append(max(1, len(sents)))
    one_liners = sum(1 for x in sent_per_para if x == 1)
    return {
        "paras": len(paras),
        "avg_chars": sum(len(p) for p in paras) / len(paras),
        "avg_sents": sum(sent_per_para) / len(sent_per_para),
        "one_liner_share": one_liners / len(paras),
    }


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
    # для маленьких корпусов df-порог снижаем, иначе обороты пропадут
    min_df = 3 if total_samples >= 30 else 2

    lengths = [len(s) for s in samples]
    avg_len = sum(lengths) / total_samples
    median_len = sorted(lengths)[total_samples // 2]

    # Ритм предложений
    sent_lengths = sentence_lengths(samples)
    rhythm = burstiness(sent_lengths)
    short_sents = sum(1 for x in sent_lengths if x <= 5)
    long_sents = sum(1 for x in sent_lengths if x >= 20)

    # Словарь
    all_words = [w.lower() for w in WORD_RE.findall(lower)]
    content_words = [w for w in all_words if len(w) > 2 and w not in STOPWORDS]

    # Document-frequency по ТОКЕНАМ — чтобы отличить стилистический оборот
    # (все слова частотны по корпусу) от тематического (есть редкое слово:
    # имя, термин). Топик-шум вроде «луиса рейеса» содержит редкий токен.
    token_df = Counter()
    for s in samples:
        for tok in set(w.lower() for w in WORD_RE.findall(s)):
            token_df[tok] += 1
    common_df = max(4, int(total_samples * 0.10))
    common_tokens = {tok for tok, df in token_df.items() if df >= common_df}

    def is_stylistic(gram):
        """Оборот стилистический, если все его токены частотны (нет редкого имени/термина)."""
        return all(tok in common_tokens for tok in gram.split())
    top_words = Counter(content_words).most_common(25)
    richness = lexical_richness(all_words)
    fw_over, fw_under = function_word_deviation(all_words)

    fillers = phrase_counts(lower, FILLER_CANDIDATES_RU + FILLER_CANDIDATES_EN)
    markers = count_markers(joined, TEXT_MARKERS)
    emojis = Counter(c for c in EMOJI_RE.findall(joined) if c not in EMOJI_SKIP)
    arrows = len(ARROW_RE.findall(joined))

    bigrams = ngram_counts(samples, 2, min_df=min_df)
    trigrams = ngram_counts(samples, 3, min_df=min_df)
    quadgrams = ngram_counts(samples, 4, min_count=2, min_df=min_df)
    openers, sent_total = sentence_openers(samples)
    endings = Counter(classify_ending(s) for s in samples)
    paras = paragraph_stats(samples)

    # Регистр
    colloquial = sum(phrase_counts(lower, REGISTER_COLLOQUIAL).values())
    formal = sum(phrase_counts(lower, REGISTER_FORMAL).values())

    # Пунктуационная сигнатура
    em_dash = joined.count("—") + joined.count("--")
    ellipsis = joined.count("...") + joined.count("…")
    parens = min(joined.count("("), joined.count(")"))
    exclaim = joined.count("!")
    question = joined.count("?")
    contractions = len(re.findall(r"\b\w+['’]\w+\b", joined))

    out = []
    w = out.append
    w("# Voice DNA — отчёт по корпусу (идиостиль по 4 слоям Виноградова)\n")
    w(f"**Образцов:** {total_samples}  |  **Символов:** {len(joined)}  |  "
      f"**Слов:** {richness['tokens']}  |  df-порог оборотов: ≥{min_df} образцов\n")

    # ── Слой 1. Лексический ───────────────────────────────────────────────
    w("## Слой 1 — Лексический (словарный отпечаток)\n")

    w("**Подпись по служебным словам (отклонение от нормы РЯ, не сырая частота):**")
    if fw_over:
        w("  _Над-употребляет (×норма):_ "
          + ", ".join(f"`{wd}` ×{r:.1f} ({obs:.1f}‰)" for wd, obs, r in fw_over[:8]))
    if fw_under:
        w("  _Недо-употребляет:_ "
          + ", ".join(f"`{wd}` ×{r:.1f}" for wd, obs, r in fw_under[:5]))
    if not (fw_over or fw_under):
        w("  служебные слова — в пределах нормы РЯ, яркой подписи нет")
    w("")

    w("**Дискурсивные маркеры / вводные (кандидаты в DNA):**")
    if fillers:
        for word, n in fillers.most_common():
            w(f"- `{word}` — {n} раз")
    else:
        w("- не найдено заметных вводных — голос, видимо, более «сухой»")
    w("")

    all_grams = quadgrams + trigrams + bigrams
    styl_all = [t for t in all_grams if is_stylistic(t[0])]
    topical = [t for t in all_grams if not is_stylistic(t[0])]

    def dedupe(items, k):
        """Длинные обороты вперёд; биграмму внутри показанного длинного — пропустить."""
        res, shown = [], []
        for g, n, df in items:
            if any(g in s for s in shown):
                continue
            res.append((g, n, df))
            shown.append(g)
            if len(res) >= k:
                break
        return res

    styl_show = dedupe(styl_all, 12)
    w("**Стилистические обороты — ПОДПИСЬ голоса (все слова частотны; «оборот — N раз / M образцов»):**")
    if styl_show:
        for g, n, df in styl_show:
            w(f"- «{g}» — {n} / {df}")
    else:
        w("- устойчивых стилистических оборотов не найдено")
    if topical:
        w("")
        w("_Тематические обороты (есть редкое слово — имя/термин; это ТЕМЫ, в голос НЕ тащить):_ "
          + ", ".join(f"«{g}»" for g, _, _ in topical[:6]))
    w("")

    w("**Богатство словаря:**")
    w(f"- TTR: {richness['ttr']:.3f} (нормир. logV/logN: {richness['ttr_norm']:.3f})  |  "
      f"hapax-доля: {richness['hapax_ratio']:.2f}  |  Yule's K: {richness['yule_k']:.0f}")
    w("  → выше TTR/hapax = богаче словарь; выше Yule's K = больше повторов (уже словарь)")
    w("")

    w("**Топ контентных слов — это ТЕМЫ, не голос (в отпечаток не тащить):**")
    w(", ".join(f"{wd}×{n}" for wd, n in top_words) or "—")
    w("")

    # ── Слой 2. Синтаксический ────────────────────────────────────────────
    w("## Слой 2 — Синтаксический (ритм и пунктуация)\n")
    w(f"- Средняя длина образца: **{avg_len:.0f}** символов (медиана {median_len})")
    w(f"- Длина предложения: среднее **{rhythm['mean']:.1f}** слов, σ {rhythm['std']:.1f}")
    w(f"- **Burstiness (CV = σ/μ): {rhythm['cv']:.2f}** — "
      + ("выраженная игра длиной (рубленые ↔ длинные)" if rhythm['cv'] >= 0.8
         else "умеренная вариация" if rhythm['cv'] >= 0.5
         else "ровный, монотонный ритм"))
    w(f"- Коротких предложений (≤5 слов): {short_sents} ({pct(short_sents, len(sent_lengths))})  |  "
      f"длинных (≥20): {long_sents} ({pct(long_sents, len(sent_lengths))})")
    w(f"- Пунктуация — тире: {em_dash}, скобки-вставки: {parens}, многоточие: {ellipsis}, "
      f"восклицания: {exclaim}, вопросы: {question}, сокращения: {contractions}")
    w("")

    # ── Слой 3. Композиционный ────────────────────────────────────────────
    w("## Слой 3 — Композиционный (абзацы, зачины, концовки)\n")
    if paras:
        one_liner_n = round(paras['one_liner_share'] * paras['paras'])
        w(f"- Абзацев: {paras['paras']}  |  средний абзац: {paras['avg_chars']:.0f} симв / "
          f"{paras['avg_sents']:.1f} предл  |  одно-предложенческих: "
          f"{pct(one_liner_n, paras['paras'])}")
        w("  → высокая доля одно-предложенческих абзацев = текст «дышит», блоки разной длины")
    if openers:
        contrast = [(wd, n) for wd, n in openers if wd in CONTRAST_OPENERS]
        other = [(wd, n) for wd, n in openers if wd not in CONTRAST_OPENERS]
        if contrast:
            w("- Зачины-сигналы (вход в мысль с контраста/связки): "
              + ", ".join(f"«{wd}»×{n}" for wd, n in contrast))
        if other:
            w("- Прочие частые зачины (часто просто предлоги — слабый сигнал): "
              + ", ".join(f"«{wd}»×{n}" for wd, n in other[:5]))
    w("- Концовки образцов: " + ", ".join(
        f"{kind} {pct(n, total_samples)}" for kind, n in endings.most_common()))
    w("")

    # ── Слой 4. Образ автора ──────────────────────────────────────────────
    w("## Слой 4 — Образ автора (тон, регистр) — сигналы\n")
    w("**Маркеры тона / иронии:**")
    if markers or emojis:
        for m, n in markers.most_common():
            w(f"- `{m}` — {n} раз")
        for e, n in emojis.most_common(10):
            w(f"- {e} — {n} раз")
    else:
        w("- маркеров иронии/эмодзи не найдено — нейтральный регистр")
    if arrows:
        w(f"- _(стрелки ×{arrows} — структура/буллеты, НЕ маркер тона)_")
    reg_total = colloquial + formal
    if reg_total:
        w(f"**Регистр:** разговорный {pct(colloquial, reg_total)} ↔ "
          f"формальный {pct(formal, reg_total)} "
          f"(маркеров: {colloquial} разг. / {formal} форм.)")
    else:
        w("**Регистр:** ярких маркеров ни разговорного, ни формального — нейтральный")
    w("")

    # ── Подсказки для сборки плагина ──────────────────────────────────────
    w("## Подсказки для сборки voice-плагина\n")
    if avg_len < 200:
        w("- Короткий формат доминирует → основной канал «чат/реплики».")
    else:
        w("- Длинный формат → основной канал «посты/лонгриды».")
    if rhythm['cv'] >= 0.8:
        w(f"- §2 Паттерны интонации: явно «чередуй рубленые и длинные» "
          f"(CV={rhythm['cv']:.2f}).")
    if fw_over:
        top_fw = ", ".join(f"«{wd}»" for wd, _, _ in fw_over[:5])
        w(f"- §3 Подпись: автор над-употребляет {top_fw} — это маркер голоса (деиксис/связки/обращение).")
    if fillers:
        top_f = ", ".join(f"«{x}»" for x, _ in fillers.most_common(4))
        w(f"- §3 Обязательные вводные — {top_f}.")
    sig = [g for g, _, _ in styl_show[:4]]
    if sig:
        w("- §3 Фирменные обороты (дословно): " + ", ".join(f"«{g}»" for g in sig) + ".")
    if emojis or markers:
        top_m = ", ".join(f"`{x}`" for x, _ in (markers + emojis).most_common(3))
        w(f"- §2/тональность «ироничнее»: реальные маркеры — {top_m}.")
    contrast_openers = [wd for wd, _ in openers if wd in CONTRAST_OPENERS][:3]
    if contrast_openers:
        w("- §2 Как открывает мысль: " + ", ".join(f"«{wd}»" for wd in contrast_openers) + ".")
    no_dot = endings.get("без знака", 0) + endings.get("скобка/смайл", 0) + endings.get("эмодзи", 0)
    if endings.get("вопрос", 0) > total_samples / 4:
        w(f"- §2 Концовки: часто заканчивает вопросом ({pct(endings.get('вопрос', 0), total_samples)}) "
          f"→ вопрос-ловушка в финале.")
    if no_dot > total_samples / 2:
        w("- §2 Концовки: автор обычно НЕ ставит точку → не «причёсывать» финал точкой.")
    if reg_total and colloquial > formal * 2:
        w("- §3 Регистр: голос разговорный → формальные обороты в §6 Табу.")
    if em_dash > total_samples:
        w("- Тире-исключение: автор САМ активно сыплет тире → в голосе это норма, "
          "не баним (базовый анти-слой тире не любит — пометить явно).")
    print("\n".join(out))


if __name__ == "__main__":
    main()
