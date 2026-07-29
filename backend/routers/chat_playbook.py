"""
Плейбук автоответов в чатах: темы, варианты текстов и режим работы по каждой теме.

Что здесь настраивается (всё из интерфейса, без правки кода):
  - какие темы бот отвечает сам, какие готовит на согласование, какие отдаёт менеджеру;
  - тексты ответов — несколько вариантов на тему, выбираются случайно;
  - что бот пишет клиенту и менеджеру при передаче в красной зоне;
  - уточняющий вопрос перед закрытием диалога.

Таблицы: cx_chat_themes, cx_chat_variants, cx_chat_settings.
"""
import random
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.orm import Session

from ..database import get_db
from .auth import get_current_user

router = APIRouter(prefix="/api/v1/chat-playbook", tags=["chat-playbook"])

ZONES = ("auto", "draft", "human")

# ── Стартовое наполнение (черновик 1, 2026-07-22) ────────────────────────────
# (theme_key, name, when_text, zone, action, note, sort)
SEED_THEMES = [
    ("nekomplekt", "Не хватает детали",
     "Нет детали в коробке / комплекте: трубки А/В/С, болты, гайки, колёса, ножки, накладки, ключ, шурупы. "
     "Фразы: «некомплект», «в комплекте нет», «болтиков нет», «можно прислать деталь», «доотправьте», "
     "«только два колеса положили», «вместо длинной положили короткую».",
     "auto", "offer_reshipment",
     "Ветвится по товару: разрешён — предлагаем деталь, не разрешён — ведём на возврат.", 10),
    ("soglasie", "Согласие на доотправку",
     "Клиент соглашается на отправку детали: «да», «отправляйте», «актуально», «заполнила», «давайте».",
     "auto", "send_form_link", "", 20),
    ("otkaz_dootpravki", "Отказ от доотправки",
     "«Не актуально», «не надо», «хочу вернуть», «уже перезаказал», угрозы/суд — человеку.",
     "human", "handoff_to_manager",
     "Бот возврат не оформляет и ничего не обещает — только передаёт менеджеру.", 30),
    ("status_zayavki", "Что с моей заявкой",
     "«Как долго ждать / идти деталь», «когда придёт», «в течение какого срока» — при оформленной заявке.",
     "auto", "lookup_request_status",
     "Отвечает фактами из системы: статус заявки и трек.", 40),
    ("brak", "Брак и повреждение",
     "Вмятина, скол, плохая сварка, кривая/короткая деталь, облезла краска, разваливается, царапины.",
     "draft", "", "Надо отличить настоящий брак от неверной сборки.", 50),
    ("vozvrat", "Хочу вернуть товар",
     "«Как сделать возврат», «можно ли вернуть», «возврат возможен» — без требования денег.",
     "draft", "", "Рядом лежит тема денег — это красная зона.", 60),
    ("nevozvratny", "Невозвратный товар",
     "«Заказала две, одна лишняя», «не подошёл размер», «почему невозвратный». "
     "Также: товар из категории невозвратных (сушилки и др. — список в API).",
     "draft", "", "Черновик менеджеру. Привязка к категориям ассортимента.", 70),
    ("dostavka_wb", "Где мой заказ",
     "«Когда придёт заказ WB», «статус оформлен N дней», «где посылка», перенос доставки WB.",
     "auto", "", "", 80),
    ("otmena_zakaza", "Отменить заказ",
     "Клиент просит отменить заказ или доставку.",
     "auto", "", "", 90),
    ("sborka", "Как собрать",
     "«Как собрать», «трубки не подходят / короче», «болт не входит», «накладки молотком», инструкция.",
     "auto", "", "Часто это не брак, а неверный порядок сборки — ответ снимает возврат.", 100),
    ("ekspluataciya", "Товар не работает",
     "Собрано, но не работает. Швабры: не увлажняется, не крутится, вода не сливается.",
     "auto", "", "", 110),
    ("ssylka", "Ссылка не открывается",
     "«Ссылка не работает», «ведёт на главную», «адрес не находит», «что за сайт».",
     "auto", "resend_form_link", "", 120),
    ("blagodarnost", "Благодарность и закрытие",
     "«Спасибо», «благодарю», «большое спасибо», «всё нормально/отлично», «разобрались», «нашла все».",
     "auto", "closing_check",
     "Если непонятно, closed ли диалог — сначала уточняющий вопрос, потом закрытие.", 130),

    # ── Из листа «Чат продавца» + меню уточнения (2026-07-29) ───────────────
    ("utochnenie", "Уточнение: меню 1–5",
     "Неясно, что нужно клиенту: «помогите», «вопрос», «здравствуйте» без сути, "
     "низкая уверенность классификатора. Бот предлагает меню 1–5.",
     "auto", "ask_menu",
     "После ответа цифрой/словом ведём в нужную тему. Не путать с красной зоной.", 5),
    ("cheque", "Где взять чек",
     "«Нужен чек», «где чек», «чек на покупку», «кассовый чек».",
     "auto", "", "Из старых шаблонов «Чат продавца».", 140),
    ("srok_hraneniya", "Продлить хранение на ПВЗ",
     "«Продлить срок хранения», «продлить выдачу», «не успеваю забрать».",
     "auto", "", "Это зона WB, не продавца.", 150),
    ("teh_sboi", "Техсбой маркетплейса",
     "Ошибка приложения WB, не открывается заказ, глюк статуса, «сайт не работает».",
     "auto", "", "", 160),
    ("parametry", "Размеры и характеристики",
     "«Какая высота/ширина/глубина», «какая нагрузка», «диаметр труб», "
     "«подойдёт ли в нишу», «выдержит ли». Без явной проблемы со сборкой.",
     "auto", "", "FAQ + характеристики. При сборке — тема sborka.", 170),
    ("dengi", "Сроки возврата денег",
     "«Когда вернутся деньги», «срок возврата средств», «где деньги за возврат» — "
     "без запроса детали. Красная зона / человек.",
     "human", "handoff_to_manager",
     "Деньгами управляет WB. Бот не обещает сроки от продавца.", 35),
]

# (theme_key, branch_key, branch_label, text, sort)
SEED_VARIANTS = [
    ("nekomplekt", "sku_allowed", "Товар в списке разрешённых",
     "Здравствуйте! Очень жаль, что в коробке оказалось не всё — понимаем, как это неприятно, когда собрать не получается. У нас есть возможность выслать недостающую деталь бесплатно. Отправляем?", 1),
    ("nekomplekt", "sku_allowed", "Товар в списке разрешённых",
     "Здравствуйте! Извините за эту ситуацию, комплект должен был приехать полным. Мы можем отправить вам недостающую деталь — доставим бесплатно. Подскажите, актуально?", 2),
    ("nekomplekt", "sku_allowed", "Товар в списке разрешённых",
     "Здравствуйте! Досадно, что так вышло с комплектацией. Давайте не будем ничего возвращать — мы просто вышлем недостающую деталь за наш счёт. Отправляем?", 3),
    ("nekomplekt", "sku_denied", "Товара нет в списке",
     "Здравствуйте! Извините за эту ситуацию — комплект должен был приехать полным. По этому товару выслать деталь отдельно мы, к сожалению, не можем. Оформите, пожалуйста, возврат в личном кабинете во вкладке «Покупки» с пометкой «брак» — так товар не уедет к следующему покупателю. Деньги вернутся полностью.", 1),
    ("nekomplekt", "sku_denied", "Товара нет в списке",
     "Здравствуйте! Очень жаль, что не хватило детали. Отдельно выслать её по этому товару у нас нет возможности. Самое быстрое решение — оформить возврат во вкладке «Покупки» с пометкой «брак», чтобы этот экземпляр не попал к другому человеку. Будем рады видеть вас снова.", 2),

    ("soglasie", "", "",
     "Отлично! Заполните, пожалуйста, короткую форму — укажите, какая деталь нужна и куда её отправить. Займёт пару минут.\n\n"
     "Важно: в чате Wildberries ссылка обычно не открывается по нажатию. Скопируйте её целиком и вставьте в адресную строку браузера:\n"
     "{form_url}", 1),
    ("soglasie", "", "",
     "Хорошо, оформим! Опишите в форме, чего не хватает, и укажите адрес — это быстро.\n\n"
     "Ссылка в приложении WB часто не кликабельна: скопируйте её и откройте в браузере (Chrome, Safari и т.п.):\n"
     "{form_url}", 2),

    ("otkaz_dootpravki", "", "", "Понял вас. Передаю обращение коллеге — он свяжется с вами и всё решит.", 1),

    ("status_zayavki", "new", "Заявка принята",
     "Здравствуйте! Заявку получили, сейчас проверяем наличие детали. Как только соберём посылку — пришлём трек-номер сюда, в чат.", 1),
    ("status_zayavki", "approved", "Заявка одобрена",
     "Здравствуйте! Заявка одобрена, готовим посылку к отправке. Как передадим в доставку — сразу пришлём трек.", 1),
    ("status_zayavki", "shipped", "Посылка отправлена",
     "Здравствуйте! Посылка уже в пути. Трек-номер {carrier}: {track}. По нему можно отследить движение.", 1),
    ("status_zayavki", "ready_for_pickup", "Ждёт в пункте выдачи",
     "Здравствуйте! Посылка ждёт вас в пункте выдачи {carrier}. Забрать нужно до {storage_until} — после этого её отправят обратно.", 1),
    ("status_zayavki", "delivered", "Получена",
     "Здравствуйте! По нашим данным посылка получена. Всё ли подошло?", 1),

    ("brak", "fixable_by_part", "Чинится деталью",
     "Здравствуйте! Извините, деталь не должна была приехать в таком виде. Мы вышлем вам замену бесплатно — возвращать ничего не нужно. Отправляем?", 1),
    ("brak", "needs_return", "Нужен возврат",
     "Здравствуйте! Очень жаль, что товар пришёл с дефектом — так быть не должно. Оформите, пожалуйста, возврат во вкладке «Покупки» с пометкой «брак»: это важно, чтобы бракованный экземпляр не уехал к другому покупателю. Деньги вернут полностью.", 1),
    ("brak", "needs_return", "Нужен возврат",
     "Здравствуйте! Извините за такой экземпляр. Правильнее всего оформить возврат с пометкой «брак» — тогда товар изымут из оборота, а деньги вернутся вам в полном объёме. Если решите заказать снова, мы будем рады.", 2),

    ("vozvrat", "", "", "Здравствуйте! Оформить возврат можно в личном кабинете во вкладке «Покупки» — выбираете товар и указываете причину. Если дело в недостающей или сломанной детали, напишите нам: возможно, получится решить быстрее, чем возвратом.", 1),
    ("vozvrat", "", "", "Здравствуйте! Возврат оформляется во вкладке «Покупки» в вашем личном кабинете. Подскажите, а что именно не подошло? Иногда вопрос решается проще — например, мы досылаем недостающую деталь.", 2),

    ("nevozvratny", "", "",
     "Этот товар невозвратный. Возвратность товара определяет сервис WB. "
     "От товара можно отказаться при получении.", 1),
    ("nevozvratny", "", "",
     "По этому товару возврат не предусмотрен — категорию возвратности определяет "
     "маркетплейс, мы на неё не влияем. Отказаться можно было при получении в пункте выдачи.", 2),

    ("dostavka_wb", "", "",
     "Здравствуйте! Статус доставки до пункта выдачи смотрите в карточке заказа в приложении. "
     "Если срок сдвинулся или статус долго не меняется — актуальные детали быстрее подскажут в поддержке: "
     "профиль → «Задать вопрос» → раздел про доставку. По самому товару (комплектация, сборка, качество) "
     "мы на связи здесь — напишите, поможем.", 1),
    ("dostavka_wb", "", "",
     "Здравствуйте! По пути до пункта выдачи информация обновляется в заказе на маркетплейсе. "
     "По срокам и задержке удобнее уточнить в поддержке: профиль → «Задать вопрос» → доставка. "
     "Если вопрос по товару — пишите нам, разберёмся.", 2),

    ("otmena_zakaza", "", "", "Здравствуйте! Отменить заказ можно в первый час после оформления или пока продавец не начал сборку — смотря что наступит раньше. С нашей стороны маркетплейс отменять заказы не позволяет. Если срок уже прошёл, товар можно не забирать из пункта выдачи — тогда деньги вернутся автоматически.", 1),

    ("sborka", "", "", "Здравствуйте! Давайте разберёмся — чаще всего дело в порядке сборки. Подскажите, какой товар у вас, и я пришлю схему именно для него.", 1),
    ("sborka", "", "", "Здравствуйте! Скорее всего трубки просто перепутаны местами — они разной длины и на вид почти одинаковые. Разложите их по размеру перед сборкой, так сразу станет понятнее. Напишите, какая модель у вас, пришлю точную схему.", 2),
    ("sborka", "стеллаж", "Стеллажи",
     "В комплекте три вида тонких трубок. Самые короткие (65 см) — вниз, к основанию, их 2 штуки. Средние (67,5 см) — по бокам, по 2 на каждый ярус, всего 6. Самые длинные (71 см) — под полки. Трубки потолще — вертикальные стойки. Разложите всё по длине перед началом, дальше собирается легко.", 3),

    ("ekspluataciya", "", "", "Здравствуйте! Давайте проверим два момента: отсек для чистой воды должен быть наполнен полностью, и важно, верно ли установлен крутящий механизм. Вот короткое видео, где показано, как это работает: https://drive.google.com/file/d/1wyyjqKCyyqF_AxXInQkBzmHDhMLWZIl7/view?usp=sharing", 1),
    ("ekspluataciya", "", "", "Здравствуйте! Чаще всего причина в одном из двух: не до конца наполнен отсек для чистой воды или неправильно встал крутящий механизм. Посмотрите, пожалуйста, это видео — там наглядно: https://drive.google.com/file/d/1wyyjqKCyyqF_AxXInQkBzmHDhMLWZIl7/view?usp=sharing Если не поможет, напишите — разберёмся дальше.", 2),

    ("ssylka", "", "", "Скопируйте, пожалуйста, ссылку целиком и вставьте её в адресную строку браузера — внутри приложения маркетплейса она не открывается. Вот она ещё раз: {form_url}", 1),
    ("ssylka", "", "", "Так бывает: внутри приложения ссылка не срабатывает. Скопируйте её и откройте в браузере — Chrome, Safari, любом. Держите ссылку: {form_url}", 2),

    ("blagodarnost", "problem_solved", "Проблема решена",
     "Рады, что всё сложилось! Если найдёте минуту и обновите отзыв о товаре — нам это очень поможет. Хорошего дня!", 1),
    ("blagodarnost", "problem_solved", "Проблема решена",
     "Отлично, что вопрос закрыт. Будем благодарны, если поделитесь впечатлением в отзыве — для нас это правда важно.", 2),
    ("blagodarnost", "just_thanks", "Просто благодарность",
     "Пожалуйста! Если что-то ещё понадобится — пишите сюда.", 1),
    ("blagodarnost", "just_thanks", "Просто благодарность",
     "Рад помочь. Обращайтесь, если появятся вопросы.", 2),

    ("utochnenie", "", "",
     "Подскажите, пожалуйста, что вам нужно — ответьте цифрой или коротко своими словами:\n"
     "1 — не хватает детали / нужна доотправка\n"
     "2 — брак или повреждение товара\n"
     "3 — вопрос по сборке, размерам или нагрузке\n"
     "4 — хочу оформить возврат\n"
     "5 — другое (подключу коллегу)", 1),

    ("cheque", "", "",
     "Чек можно скачать в личном кабинете WB: откройте профиль через браузер → раздел «Чеки». "
     "В мобильном приложении путь может отличаться — удобнее именно через браузер.", 1),

    ("srok_hraneniya", "", "",
     "Продлить срок хранения на пункте выдачи с нашей стороны нельзя — этим управляет Wildberries. "
     "Напишите в поддержку WB: профиль → «Задать вопрос» → раздел про хранение или доставку.", 1),

    ("teh_sboi", "", "",
     "Похоже на сбой на стороне маркетплейса — мы на такие ошибки не влияем. "
     "Обратитесь в поддержку WB: профиль → «Задать вопрос» → выберите тему по ситуации. "
     "Если вопрос по самому товару (сборка, комплектация, качество) — пишите нам сюда, поможем.", 1),

    ("parametry", "", "",
     "Точные размеры и нагрузка указаны в карточке товара в разделе «Характеристики». "
     "Напишите, пожалуйста, что именно нужно: высота, ширина, глубина или нагрузка — "
     "подскажу цифры по вашей модели.", 1),
    ("parametry", "", "",
     "Ориентиры по линейке: вешалки обычно 140–175 см в высоту, диаметр труб чаще 19–25 мм, "
     "нагрузка на штангу — ориентировочно 15–35 кг при равномерной нагрузке. "
     "Точные цифры — в карточке; если скажете артикул, уточню точечно.", 2),

    ("dengi", "", "",
     "Подключаю коллегу — по срокам возврата денег разберётся он. "
     "Обычно зачислением занимается Wildberries и банк, не продавец.", 1),
]

SEED_SETTINGS = [
    ("signature", "\n\nАлександр, команда Видовито",
     "Подпись под содержательным ответом. Под короткими репликами не ставится."),
    ("red_zone_client_text",
     "Подключаю коллегу — он разберётся в вашей ситуации и ответит здесь же. Отвечаем в рабочее время.",
     "Что бот пишет клиенту, передавая обращение менеджеру."),
    ("red_zone_notify_tg", "true",
     "Слать ли менеджеру уведомление в Telegram при передаче."),
    ("red_zone_manager_text",
     "🔴 Чат требует менеджера\nПричина: {reason}\nКлиент: {client}\nТовар: {sku}\nСообщение: {message}\nОткрыть: {link}",
     "Текст уведомления менеджеру. Доступны {reason} {client} {sku} {message} {link}."),
    ("closing_question_text",
     "Подскажите, всё ли получилось решить? Если остались вопросы — я на связи.",
     "Уточняющий вопрос, когда непонятно, закрыт ли диалог."),
    ("closing_question_delay_hours", "4",
     "Через сколько часов молчания задавать уточняющий вопрос."),
    ("closing_auto_close_hours", "48",
     "Через сколько часов после уточняющего вопроса закрывать диалог без ответа."),
]


def init_playbook_tables(engine):
    with engine.connect() as conn:
        conn.execute(text("""
            CREATE TABLE IF NOT EXISTS cx_chat_themes (
                theme_key  TEXT PRIMARY KEY,
                name       TEXT NOT NULL,
                when_text  TEXT    DEFAULT '',
                zone       TEXT    DEFAULT 'draft',
                action     TEXT    DEFAULT '',
                note       TEXT    DEFAULT '',
                sort_order INTEGER DEFAULT 0,
                active     BOOLEAN DEFAULT TRUE,
                updated_at TIMESTAMP DEFAULT now(),
                updated_by TEXT    DEFAULT ''
            )"""))
        conn.execute(text("""
            CREATE TABLE IF NOT EXISTS cx_chat_variants (
                id           SERIAL PRIMARY KEY,
                theme_key    TEXT NOT NULL,
                branch_key   TEXT    DEFAULT '',
                branch_label TEXT    DEFAULT '',
                body         TEXT NOT NULL,
                sort_order   INTEGER DEFAULT 0,
                active       BOOLEAN DEFAULT TRUE,
                updated_at   TIMESTAMP DEFAULT now(),
                updated_by   TEXT    DEFAULT ''
            )"""))
        conn.execute(text("""
            CREATE TABLE IF NOT EXISTS cx_chat_settings (
                key        TEXT PRIMARY KEY,
                value      TEXT DEFAULT '',
                descr      TEXT DEFAULT '',
                updated_at TIMESTAMP DEFAULT now(),
                updated_by TEXT DEFAULT ''
            )"""))

        # Сидим только пустые таблицы — правки из интерфейса не затираем.
        if not conn.execute(text("SELECT count(*) FROM cx_chat_themes")).scalar():
            for k, n, w, z, a, note, s in SEED_THEMES:
                conn.execute(text("""
                    INSERT INTO cx_chat_themes (theme_key, name, when_text, zone, action, note, sort_order, updated_by)
                    VALUES (:k, :n, :w, :z, :a, :note, :s, 'system')
                """), {"k": k, "n": n, "w": w, "z": z, "a": a, "note": note, "s": s})
        if not conn.execute(text("SELECT count(*) FROM cx_chat_variants")).scalar():
            for tk, bk, bl, body, s in SEED_VARIANTS:
                conn.execute(text("""
                    INSERT INTO cx_chat_variants (theme_key, branch_key, branch_label, body, sort_order, updated_by)
                    VALUES (:tk, :bk, :bl, :body, :s, 'system')
                """), {"tk": tk, "bk": bk, "bl": bl, "body": body, "s": s})
        for k, v, d in SEED_SETTINGS:
            conn.execute(text("""
                INSERT INTO cx_chat_settings (key, value, descr, updated_by)
                VALUES (:k, :v, :d, 'system')
                ON CONFLICT (key) DO UPDATE SET descr = EXCLUDED.descr
            """), {"k": k, "v": v, "d": d})
        conn.commit()
    # Дозасев новых тем (меню 1–5 и т.п.) без затирания правок из UI
    ensure_new_themes(engine)
    ensure_mp_control_settings(engine)


def ensure_new_themes(engine):
    """Добавляет темы/варианты, которых ещё нет. Существующие тексты не трогает."""
    NEW = {"utochnenie", "cheque", "srok_hraneniya", "teh_sboi", "parametry", "dengi"}
    with engine.connect() as conn:
        for k, n, w, z, a, note, s in SEED_THEMES:
            if k not in NEW:
                continue
            conn.execute(text("""
                INSERT INTO cx_chat_themes
                    (theme_key, name, when_text, zone, action, note, sort_order, updated_by)
                VALUES (:k, :n, :w, :z, :a, :note, :s, 'system')
                ON CONFLICT (theme_key) DO NOTHING
            """), {"k": k, "n": n, "w": w, "z": z, "a": a, "note": note, "s": s})
        for tk, bk, bl, body, s in SEED_VARIANTS:
            if tk not in NEW:
                continue
            has = conn.execute(text("""
                SELECT 1 FROM cx_chat_variants
                WHERE theme_key = :tk AND coalesce(branch_key,'') = :bk AND body = :body
                LIMIT 1
            """), {"tk": tk, "bk": bk or "", "body": body}).scalar()
            if has:
                continue
            conn.execute(text("""
                INSERT INTO cx_chat_variants
                    (theme_key, branch_key, branch_label, body, sort_order, updated_by)
                VALUES (:tk, :bk, :bl, :body, :s, 'system')
            """), {"tk": tk, "bk": bk or "", "bl": bl or "", "body": body, "s": s})
        conn.commit()


# ── Помощники для бота ───────────────────────────────────────────────────────

# Управление ботом — отдельно по площадкам (ключи вида bot_enabled_wb).
BOT_CONTROL_KEYS = (
    "bot_enabled",
    "bot_confidence_min",
    "bot_only_days",
    "bot_only_no_manager",
    "bot_suggest_on_open",
    "bot_ai_draft",
)
BOT_MPS = ("wb", "ozon", "ym")


def get_setting(db: Session, key: str, default: str = "") -> str:
    row = db.execute(text("SELECT value FROM cx_chat_settings WHERE key = :k"),
                     {"k": key}).fetchone()
    return row[0] if row else default


def get_setting_mp(db: Session, key: str, mp: str = "wb", default: str = "") -> str:
    """Per-mp control-настройка с fallback на legacy-ключ (без суффикса) для WB."""
    mp = (mp or "wb").lower()
    if key in BOT_CONTROL_KEYS:
        row = db.execute(text("SELECT value FROM cx_chat_settings WHERE key = :k"),
                         {"k": f"{key}_{mp}"}).fetchone()
        if row is not None:
            return row[0]
        if mp == "wb":
            legacy = db.execute(text("SELECT value FROM cx_chat_settings WHERE key = :k"),
                                {"k": key}).fetchone()
            if legacy is not None:
                return legacy[0]
    return get_setting(db, key, default)


def ensure_mp_control_settings(engine) -> None:
    """Сидит bot_*_{wb,ozon,ym}. Для WB копирует значение со старого ключа без суффикса."""
    from .. import chat_bot as _bot
    defaults = {k: (v, d) for k, v, d in _bot.EXTRA_SETTINGS if k in BOT_CONTROL_KEYS}
    with engine.connect() as conn:
        for mp in BOT_MPS:
            for key in BOT_CONTROL_KEYS:
                default, descr = defaults.get(key, ("", ""))
                mp_key = f"{key}_{mp}"
                exists = conn.execute(text(
                    "SELECT 1 FROM cx_chat_settings WHERE key = :k"
                ), {"k": mp_key}).scalar()
                if exists:
                    conn.execute(text(
                        "UPDATE cx_chat_settings SET descr = :d WHERE key = :k"
                    ), {"d": f"[{mp.upper()}] {descr}", "k": mp_key})
                    continue
                val = default
                if mp == "wb":
                    legacy = conn.execute(text(
                        "SELECT value FROM cx_chat_settings WHERE key = :k"
                    ), {"k": key}).scalar()
                    if legacy is not None:
                        val = legacy
                conn.execute(text("""
                    INSERT INTO cx_chat_settings (key, value, descr, updated_by)
                    VALUES (:k, :v, :d, 'system')
                    ON CONFLICT (key) DO NOTHING
                """), {
                    "k": mp_key,
                    "v": val,
                    "d": f"[{mp.upper()}] {descr}",
                })
        conn.commit()


def get_zone(db: Session, theme_key: str) -> str:
    """Режим темы: auto — отправляем сами, draft — на согласование, human — менеджеру."""
    row = db.execute(text(
        "SELECT zone, active FROM cx_chat_themes WHERE theme_key = :k"
    ), {"k": theme_key}).fetchone()
    if not row or not row[1]:
        return "human"
    return row[0] if row[0] in ZONES else "human"


def pick_variant(db: Session, theme_key: str, branch_key: str = "") -> Optional[str]:
    """Случайный активный вариант — чтобы ответы не выглядели под копирку."""
    rows = db.execute(text("""
        SELECT body FROM cx_chat_variants
        WHERE theme_key = :k AND branch_key = :b AND active
        ORDER BY sort_order
    """), {"k": theme_key, "b": branch_key}).fetchall()
    if not rows:
        return None
    return random.choice([r[0] for r in rows])


# ── API ──────────────────────────────────────────────────────────────────────

class ThemeUpdate(BaseModel):
    name: Optional[str] = None
    when_text: Optional[str] = None
    zone: Optional[str] = None
    note: Optional[str] = None
    active: Optional[bool] = None


class VariantIn(BaseModel):
    theme_key: str
    branch_key: str = ""
    branch_label: str = ""
    body: str
    active: bool = True


class VariantUpdate(BaseModel):
    body: Optional[str] = None
    active: Optional[bool] = None
    branch_label: Optional[str] = None


class SettingUpdate(BaseModel):
    value: str


@router.get("")
def get_playbook(db: Session = Depends(get_db), current_user=Depends(get_current_user)):
    themes = db.execute(text("""
        SELECT theme_key, name, when_text, zone, action, note, sort_order, active
        FROM cx_chat_themes ORDER BY sort_order, name
    """)).mappings().all()
    variants = db.execute(text("""
        SELECT id, theme_key, branch_key, branch_label, body, sort_order, active
        FROM cx_chat_variants ORDER BY theme_key, branch_key, sort_order, id
    """)).mappings().all()
    settings = db.execute(text(
        "SELECT key, value, descr FROM cx_chat_settings ORDER BY key"
    )).mappings().all()

    by_theme = {}
    for v in variants:
        by_theme.setdefault(v["theme_key"], []).append(dict(v))

    return {
        "themes": [{**dict(t), "variants": by_theme.get(t["theme_key"], [])} for t in themes],
        "settings": [dict(s) for s in settings],
    }


@router.put("/themes/{theme_key}")
def update_theme(theme_key: str, payload: ThemeUpdate, db: Session = Depends(get_db),
                 current_user=Depends(get_current_user)):
    if payload.zone is not None and payload.zone not in ZONES:
        raise HTTPException(400, f"zone должен быть одним из {ZONES}")
    fields, params = [], {"k": theme_key, "u": current_user["username"]}
    for col in ("name", "when_text", "zone", "note", "active"):
        val = getattr(payload, col)
        if val is not None:
            fields.append(f"{col} = :{col}")
            params[col] = val
    if not fields:
        return {"status": "noop"}
    res = db.execute(text(f"""
        UPDATE cx_chat_themes SET {', '.join(fields)}, updated_at = now(), updated_by = :u
        WHERE theme_key = :k
    """), params)
    if not res.rowcount:
        raise HTTPException(404, "Тема не найдена")
    db.commit()
    return {"status": "ok"}


@router.post("/variants")
def create_variant(payload: VariantIn, db: Session = Depends(get_db),
                   current_user=Depends(get_current_user)):
    if not payload.body.strip():
        raise HTTPException(400, "Текст не может быть пустым")
    nxt = db.execute(text("""
        SELECT COALESCE(max(sort_order), 0) + 1 FROM cx_chat_variants
        WHERE theme_key = :k AND branch_key = :b
    """), {"k": payload.theme_key, "b": payload.branch_key}).scalar()
    row = db.execute(text("""
        INSERT INTO cx_chat_variants (theme_key, branch_key, branch_label, body, sort_order, active, updated_by)
        VALUES (:k, :b, :bl, :body, :s, :a, :u) RETURNING id
    """), {"k": payload.theme_key, "b": payload.branch_key, "bl": payload.branch_label,
           "body": payload.body.strip(), "s": nxt, "a": payload.active,
           "u": current_user["username"]}).fetchone()
    db.commit()
    return {"status": "ok", "id": row[0]}


@router.put("/variants/{variant_id}")
def update_variant(variant_id: int, payload: VariantUpdate, db: Session = Depends(get_db),
                   current_user=Depends(get_current_user)):
    fields, params = [], {"id": variant_id, "u": current_user["username"]}
    for col in ("body", "active", "branch_label"):
        val = getattr(payload, col)
        if val is not None:
            fields.append(f"{col} = :{col}")
            params[col] = val
    if not fields:
        return {"status": "noop"}
    res = db.execute(text(f"""
        UPDATE cx_chat_variants SET {', '.join(fields)}, updated_at = now(), updated_by = :u
        WHERE id = :id
    """), params)
    if not res.rowcount:
        raise HTTPException(404, "Вариант не найден")
    db.commit()
    return {"status": "ok"}


@router.delete("/variants/{variant_id}")
def delete_variant(variant_id: int, db: Session = Depends(get_db),
                   current_user=Depends(get_current_user)):
    db.execute(text("DELETE FROM cx_chat_variants WHERE id = :id"), {"id": variant_id})
    db.commit()
    return {"status": "ok"}


@router.put("/settings/{key}")
def update_setting(key: str, payload: SettingUpdate, db: Session = Depends(get_db),
                   current_user=Depends(get_current_user)):
    # UPSERT — per-mp ключи могут появиться до сида
    db.execute(text("""
        INSERT INTO cx_chat_settings (key, value, descr, updated_by, updated_at)
        VALUES (:k, :v, '', :u, now())
        ON CONFLICT (key) DO UPDATE SET
            value = EXCLUDED.value,
            updated_at = now(),
            updated_by = EXCLUDED.updated_by
    """), {"v": payload.value, "u": current_user["username"], "k": key})
    db.commit()
    return {"status": "ok"}
