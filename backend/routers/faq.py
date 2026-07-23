from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session
from sqlalchemy import text
from ..database import get_db
from .auth import get_current_user

router = APIRouter(prefix="/api/v1/faq", tags=["FAQ"])

TOPIC_SQL = """
WITH nm_ids AS (
  SELECT a.name_ru, MIN(r.nm_id) as nm_id
  FROM wb_assortment a
  JOIN wb_ratings r ON r.supplier_article = a.supplier_article
  WHERE a.name_ru IS NOT NULL
  GROUP BY a.name_ru
),
base AS (
  SELECT a.name_ru as card, a.category_1 as category,
    CASE
      WHEN LOWER(q.text) SIMILAR TO '%(не хватает|нехватает|не хватило|без болт|нет болт|пришла без|нет в комплект|не было в комплект|не положили|нет заглушк|нет крюч|нет шуруп|нет ножк|нет колес|отсутствует|недостает)%'
        THEN 'Некомплект'
      WHEN LOWER(q.text) SIMILAR TO '%(размер|глубина|ширина|высота|длина|диаметр|габарит|сколько см|размеры)%'
        THEN 'Размеры'
      WHEN LOWER(q.text) SIMILAR TO '%(собрать|сборк|собирать|инструкция|шатается|не вставляется|не входит|как собрать)%'
        THEN 'Сборка'
      WHEN LOWER(q.text) SIMILAR TO '%(вернуть|возврат|обмен|брак|сломан|треснул|дефект|погнут|бракован)%'
        THEN 'Возврат/брак'
      WHEN LOWER(q.text) SIMILAR TO '%(купить отдельно|заказать отдельно|приобрести отдельно|отдельно крюч|отдельно болт|отдельно колес|без ведра|швабру без|отдельно швабр|запчаст|комплектующ)%'
        THEN 'Запчасти'
      WHEN LOWER(q.text) SIMILAR TO '%(нагрузка|выдерживает|максимальн|сколько кг|балкон|улиц|морозы|влажност)%'
        THEN 'Нагрузка/условия'
      WHEN LOWER(q.text) SIMILAR TO '%(задержка|задерживается|долго|статус оформлен|когда придёт|когда придет|когда отправит|не приходит)%'
        THEN 'Доставка'
      WHEN LOWER(q.text) SIMILAR TO '%(в наличии|когда будет|есть ли|появится|другой цвет|цвет)%'
        THEN 'Наличие/цвет'
      WHEN LOWER(q.text) SIMILAR TO '%(как пользоваться|как работает|как отжимать|как моет|вода не|тряпка не)%'
        THEN 'Инструкция'
      ELSE NULL
    END as topic
  FROM wb_questions q
  JOIN wb_assortment a ON a.supplier_article = q.supplier_article
  WHERE q.created_date >= NOW() - INTERVAL '6 months'
    AND a.name_ru IS NOT NULL
),
totals AS (
  SELECT card, category, COUNT(*) as total
  FROM base
  GROUP BY card, category
),
topic_counts AS (
  SELECT card, topic, COUNT(*) as cnt
  FROM base
  WHERE topic IS NOT NULL
  GROUP BY card, topic
),
ranked AS (
  SELECT tc.card, tc.topic, tc.cnt,
    ROW_NUMBER() OVER (PARTITION BY tc.card ORDER BY tc.cnt DESC) as rk
  FROM topic_counts tc
)
SELECT
  t.card,
  t.category,
  t.total,
  n.nm_id,
  json_agg(
    json_build_object('topic', r.topic, 'cnt', r.cnt)
    ORDER BY r.cnt DESC
  ) FILTER (WHERE r.card IS NOT NULL) as topics
FROM totals t
LEFT JOIN ranked r ON r.card = t.card AND r.rk <= 4
LEFT JOIN nm_ids n ON n.name_ru = t.card
GROUP BY t.card, t.category, t.total, n.nm_id
ORDER BY t.total DESC
"""


@router.get("/by-card")
def faq_by_card(
    db: Session = Depends(get_db),
    _: dict = Depends(get_current_user),
):
    rows = db.execute(text(TOPIC_SQL)).fetchall()
    result = []
    for r in rows:
        result.append({
            "card":     r.card,
            "category": r.category,
            "total":    r.total,
            "nm_id":    r.nm_id,
            "topics":   r.topics or [],
        })
    return {"cards": result}
