#!/usr/bin/env python3
"""Кластеризация реальных фраз клиентов WB-чатов для усиления триггеров бота."""
from __future__ import annotations

import json
import re
from collections import defaultdict

from sqlalchemy import text

from backend.database import engine

PATTERNS = {
    "nekomplekt": re.compile(
        r"не\s+хвата|нет\s+в\s+короб|отсутств|не\s+полож|недокомплект|забыли\s+полож|"
        r"без\s+(болт|труб|винт|колес|крепеж|ножек|гаек|прут)|дослать|дошлите|"
        r"выслать\s+(болт|детал|крепеж)|не\s+хватило|мало\s+(болт|труб|винт)|"
        r"пришло\s+без|не\s+доложил|комплектующ|детал\w*\s+нет|нет\s+детал|"
        r"нет\s+болт|нет\s+труб|нет\s+колес|нет\s+крепеж|одной\s+труб|двух\s+труб|"
        r"не\s+хватает\s+детал|прислали\s+\d|положили\s+только|в\s+наборе\s+нет|"
        r"не\s+хватило\s+|не\s+хватает\s+|не\s+хватало",
        re.I,
    ),
    "soglasie": re.compile(
        r"^(да|даа+|ок|окей|хорошо|отправляйте|отправляй|отправьте|"
        r"буду\s+благодар|актуально|согласен|согласна|нужно|"
        r"давайте|давай|конечно|ждём|ждем|заполнила|заполнил|заполнили)[\s!.]*$"
        r"|да[,\s]+актуальн|да[,\s]+давайте|отправляйте|актуально|"
        r"заполнил|заявку\s+оформил|форму\s+заполнил",
        re.I,
    ),
    "otkaz": re.compile(
        r"не\s+актуальн|не\s+надо|не\s+нужно|отказ|не\s+хочу|"
        r"уже\s+перезаказ|хочу\s+вернуть|перезаказ",
        re.I,
    ),
    "money": re.compile(
        r"деньг|компенсац|возврат\s*денег|верн(ите|уть|у)\s+деньг|"
        r"возмест|возврат\s+средств|верните\s+оплат",
        re.I,
    ),
    "brak": re.compile(
        r"брак|сломан|полом|треснул|вмятин|скол|крив|облез|ржав|сварк|"
        r"поврежд|дефект|некачеств|дохл|ужас|погнут|оторван|дырк",
        re.I,
    ),
    "vozvrat": re.compile(
        r"верн(уть|ите|у)\s+товар|оформить\s+возврат|хочу\s+возврат|"
        r"сделать\s+возврат|как\s+вернуть|возврат\s+товар|на\s+возврат|"
        r"^возврат[\s!.]*$|можно\s+ли\s+вернуть",
        re.I,
    ),
    "dostavka": re.compile(
        r"где\s+(мой\s+)?заказ|когда\s+прид|статус\s+не\s+меня|потерял|"
        r"где\s+посыл|трек|отслеж|не\s+приш(ел|ла|ло)|ещё\s+не\s+приш|"
        r"когда\s+достав|где\s+находит",
        re.I,
    ),
    "status": re.compile(
        r"заявк|когда\s+детал|как\s+долго\s+ждать|статус\s+заяв|"
        r"что\s+с\s+моей|ожидаю\s+детал|когда\s+отправ",
        re.I,
    ),
    "sborka": re.compile(
        r"собрать|сборк|инструкц|трубк\w*\s+не\s+подход|непонятно|"
        r"что\s+куда|нет\s+отверст|схем[аы]\s+сбор|как\s+собрать|"
        r"не\s+сходится|не\s+стыку",
        re.I,
    ),
    "ekspl": re.compile(
        r"не\s+работа|не\s+крут|не\s+увлажн|швабр|не\s+крутится|"
        r"сломалась\s+после|перестал[ао]?\s+работа",
        re.I,
    ),
    "ssylka": re.compile(
        r"ссылк|не\s+открыв|перекидыва|что\s+за\s+сайт|не\s+работает\s+ссыл",
        re.I,
    ),
    "otmena": re.compile(r"отмен(ить|ите|а)\s+заказ|отменить\s+достав", re.I),
    "thanks": re.compile(
        r"^(спасибо|благодар|хорошо\s*,?\s*спасибо|понял[а]?|принято|"
        r"спасибо\s+большое|спасибо\s+за\s+помощь)[\s!.❤🙏👍]*$",
        re.I,
    ),
    "greet": re.compile(
        r"^(здравствуйте|добрый\s+(день|вечер|утро)|привет)[!.,\s]*$",
        re.I,
    ),
    "wb_nudge": re.compile(r"пожалуйста,\s*посмотрите\s+моё\s+сообщение", re.I),
    "red": re.compile(
        r"роспотреб|суд\b|юрист|адвокат|мошен|угроз|прокуратур|полици",
        re.I,
    ),
    "nevozvrat": re.compile(
        r"не\s+подош(ёл|ел|ла)|лишн(ий|яя|ее)|заказала?\s+две|"
        r"размер\s+не\s+тот|цвет\s+не\s+тот|передумал",
        re.I,
    ),
}


def main():
    with engine.connect() as c:
        rows = c.execute(
            text(
                """
            SELECT DISTINCT ON (lower(trim(text)))
                   trim(text) AS t, add_ts
            FROM wb_chat_messages
            WHERE sender = 'client'
              AND text IS NOT NULL
              AND length(trim(text)) BETWEEN 3 AND 500
              AND trim(text) NOT ILIKE :del
            ORDER BY lower(trim(text)), add_ts DESC
            LIMIT 450
            """
            ),
            {"del": "Мы удалили это сообщение%"},
        ).fetchall()

    msgs = [r[0] for r in rows]
    print("unique samples", len(msgs))

    buckets = defaultdict(list)
    unmatched = []
    for t in msgs:
        hit = None
        for name, rx in PATTERNS.items():
            if rx.search(t):
                hit = name
                break
        if hit:
            buckets[hit].append(t)
        else:
            unmatched.append(t)

    print("\n=== bucket sizes ===")
    for k, v in sorted(buckets.items(), key=lambda x: -len(x[1])):
        print(f"{k:12s} {len(v)}")
    print(f"{'UNMATCHED':12s} {len(unmatched)} ({100 * len(unmatched) / max(len(msgs), 1):.0f}%)")

    print("\n=== UNMATCHED sample ===")
    for t in unmatched[:150]:
        print("---")
        print(t[:350].replace("\n", " / "))

    out = {
        "n": len(msgs),
        "buckets": {k: v for k, v in buckets.items()},
        "unmatched": unmatched,
    }
    path = "/tmp/chat_phrases.json"
    with open(path, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=2)
    print("\nsaved", path)


if __name__ == "__main__":
    main()
