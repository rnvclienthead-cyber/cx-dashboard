-- Cleanup exact/junk template duplicates (idempotent-safe: deletes by content rules)
BEGIN;

-- 1) Empty bodies (any mp/theme)
DELETE FROM cx_chat_variants
WHERE coalesce(trim(body), '') = '';

-- 2) Exact active duplicates: keep smallest id per (mp, theme, branch, normalized body)
DELETE FROM cx_chat_variants v
USING (
  SELECT id,
         row_number() OVER (
           PARTITION BY coalesce(mp,''), theme_key, coalesce(branch_key,''),
                        regexp_replace(lower(trim(body)), '[[:space:]]+', ' ', 'g')
           ORDER BY id
         ) AS rn
  FROM cx_chat_variants
  WHERE active
) d
WHERE v.id = d.id AND d.rn > 1;

-- 3) Inactive exact clones of an active twin
DELETE FROM cx_chat_variants v
WHERE NOT v.active
  AND EXISTS (
    SELECT 1 FROM cx_chat_variants a
    WHERE a.active
      AND a.id <> v.id
      AND coalesce(a.mp,'') = coalesce(v.mp,'')
      AND a.theme_key = v.theme_key
      AND coalesce(a.branch_key,'') = coalesce(v.branch_key,'')
      AND regexp_replace(lower(trim(a.body)), '[[:space:]]+', ' ', 'g')
        = regexp_replace(lower(trim(v.body)), '[[:space:]]+', ' ', 'g')
  );

-- 4) WB zapchasti in_stock near-dupes: keep body WITHOUT leading greeting
DELETE FROM cx_chat_variants v
WHERE v.theme_key = 'zapchasti'
  AND coalesce(v.mp,'wb') = 'wb'
  AND coalesce(v.branch_key,'') = 'in_stock'
  AND v.body ~* '^\s*(здравствуйте|добрый\s+(день|вечер|утро)|привет)'
  AND EXISTS (
    SELECT 1 FROM cx_chat_variants o
    WHERE o.theme_key = 'zapchasti'
      AND coalesce(o.mp,'wb') = 'wb'
      AND coalesce(o.branch_key,'') = 'in_stock'
      AND o.id <> v.id
      AND o.active
      AND o.body !~* '^\s*(здравствуйте|добрый\s+(день|вечер|утро)|привет)'
  );

-- 5) WB return_expired / wrong_item: drop "Здравствуйте!" twin if same body without greeting exists
DELETE FROM cx_chat_variants v
WHERE v.theme_key IN ('return_expired', 'wrong_item')
  AND coalesce(v.mp,'wb') = 'wb'
  AND v.body ~* '^\s*здравствуйте'
  AND EXISTS (
    SELECT 1 FROM cx_chat_variants o
    WHERE o.theme_key = v.theme_key
      AND coalesce(o.mp,'') = coalesce(v.mp,'')
      AND coalesce(o.branch_key,'') = coalesce(v.branch_key,'')
      AND o.id <> v.id
      AND o.active
      AND regexp_replace(
            lower(regexp_replace(o.body, '^\s*здравствуйте[!.,]?\s*', '', 'i')),
            '[[:space:]]+', ' ', 'g')
        = regexp_replace(
            lower(regexp_replace(v.body, '^\s*здравствуйте[!.,]?\s*', '', 'i')),
            '[[:space:]]+', ' ', 'g')
  );

-- 6) WB return_expired: prefer WB-specific wording over generic «маркетплейса»
DELETE FROM cx_chat_variants v
WHERE v.theme_key = 'return_expired'
  AND coalesce(v.mp,'wb') = 'wb'
  AND v.body ILIKE '%поддержку маркетплейса%'
  AND EXISTS (
    SELECT 1 FROM cx_chat_variants o
    WHERE o.theme_key = 'return_expired'
      AND coalesce(o.mp,'wb') = 'wb'
      AND o.active
      AND o.id <> v.id
      AND o.body ILIKE '%поддержку Wildberries%'
  );

-- 7) Questions: drop near-duplicate Ozon zapchasti (keep one with «можно заказать»)
DELETE FROM cx_question_variants v
WHERE v.theme_key = 'zapchasti'
  AND coalesce(v.mp,'') = 'ozon'
  AND v.body ILIKE '%фирменные сменные насадки%'
  AND EXISTS (
    SELECT 1 FROM cx_question_variants o
    WHERE o.theme_key = 'zapchasti'
      AND coalesce(o.mp,'') = 'ozon'
      AND o.id <> v.id
      AND o.active
      AND o.body ILIKE '%можно заказать%'
  );

-- Same exact-dupe cleanup for questions
DELETE FROM cx_question_variants
WHERE coalesce(trim(body), '') = '';

DELETE FROM cx_question_variants v
USING (
  SELECT id,
         row_number() OVER (
           PARTITION BY coalesce(mp,''), theme_key, coalesce(branch_key,''),
                        regexp_replace(lower(trim(body)), '[[:space:]]+', ' ', 'g')
           ORDER BY id
         ) AS rn
  FROM cx_question_variants
  WHERE active
) d
WHERE v.id = d.id AND d.rn > 1;

COMMIT;

INSERT INTO cx_schema_migrations (key, applied_at)
VALUES ('chat_question_template_dupes_v1', now())
ON CONFLICT (key) DO NOTHING;

-- Verify remaining problem spots
SELECT 'chat_exact_left' AS check, count(*)::text AS n FROM (
  SELECT 1 FROM cx_chat_variants
  GROUP BY coalesce(mp,''), theme_key, coalesce(branch_key,''),
           regexp_replace(lower(trim(body)), '[[:space:]]+', ' ', 'g')
  HAVING count(*) > 1
) t
UNION ALL
SELECT 'chat_empty', count(*)::text FROM cx_chat_variants WHERE coalesce(trim(body),'')=''
UNION ALL
SELECT 'chat_total', count(*)::text FROM cx_chat_variants
UNION ALL
SELECT 'q_total', count(*)::text FROM cx_question_variants;

SELECT 'zapchasti_wb' AS check, id::text, coalesce(branch_key,''), left(body,80)
FROM cx_chat_variants WHERE theme_key='zapchasti' AND coalesce(mp,'')='wb' ORDER BY id;
SELECT 'return_expired_wb' AS check, id::text, active::text, left(body,80)
FROM cx_chat_variants WHERE theme_key='return_expired' AND coalesce(mp,'')='wb' ORDER BY id;
SELECT 'q_zapchasti' AS check, id::text, coalesce(mp,''), left(body,80)
FROM cx_question_variants WHERE theme_key='zapchasti' ORDER BY id;
