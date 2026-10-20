/**
 * Группировка быстрых шаблонов по теме (папки).
 * Ожидает объекты с theme_key, theme_name, title, branch_key/branch_label опционально.
 */

export function templateVariantLabel(template) {
  const branch = String(template?.branch_label || '').trim()
  if (branch) return branch
  const title = String(template?.title || '').trim()
  const theme = String(template?.theme_name || template?.theme_key || '').trim()
  if (theme && title.startsWith(`${theme}:`)) {
    return title.slice(theme.length + 1).trim() || 'Основной'
  }
  if (theme && title === theme) return 'Основной'
  return title || 'Основной'
}

export function templateBody(template) {
  return String(
    template?.preview_body
    || template?.body
    || template?.text
    || template?.answer
    || template?.content
    || ''
  ).trim()
}

/** Короткое превью текста ответа для кнопки выбора (≈2 строки). */
export function templatePreviewText(template, maxLen = 140) {
  const body = templateBody(template).replace(/\s+/g, ' ').trim()
  if (!body) return ''
  if (body.length <= maxLen) return body
  const cut = body.slice(0, maxLen)
  const soft = cut.replace(/\s+\S*$/, '')
  return `${(soft.length >= 40 ? soft : cut).trim()}…`
}

/** Три темы на полоске быстрых шаблонов в чате. Остальные — в «Темы». */
export const QUICK_CHAT_THEME_KEYS = ['dostavka_wb', 'nekomplekt', 'soglasie']

export const QUICK_CHAT_THEME_LABELS = {
  dostavka_wb: 'Где мой заказ',
  nekomplekt: 'Не хватает детали',
  soglasie: 'Согласие на доотправку',
}

export const DOSTAVKA_OUR_DELAY_KEY = 'nasha_zaderzhka'
const DOSTAVKA_SELLER_CODES = new Set(['FBS', 'DBS'])
const DOSTAVKA_MARKETPLACE_CODES = new Set(['FBO', 'FBY', 'DBW'])

/** FBS/своя доставка → наша задержка; FBO/FBY/DBW → обычный шаблон. null — тип неизвестен. */
export function dostavkaBranchForFulfillment(code) {
  const key = String(code || '').trim().toUpperCase()
  if (DOSTAVKA_SELLER_CODES.has(key)) return DOSTAVKA_OUR_DELAY_KEY
  if (DOSTAVKA_MARKETPLACE_CODES.has(key)) return ''
  return null
}

export function pickThemeTemplate(group, fulfillmentCode, rngOrOpts = Math.random) {
  const opts = typeof rngOrOpts === 'function' ? { rng: rngOrOpts } : (rngOrOpts || {})
  const rng = typeof opts.rng === 'function' ? opts.rng : Math.random
  const templates = Array.isArray(group?.templates) ? group.templates : []
  if (!templates.length) return null
  let pool = templates
  if (group.theme_key === 'dostavka_wb') {
    const branch = dostavkaBranchForFulfillment(fulfillmentCode)
    if (branch === null) return null
    pool = templates.filter(t => String(t.branch_key || '') === branch)
    if (!pool.length) return null
  } else if (group.theme_key === 'nekomplekt') {
    const branch = opts.nekomplektBranch
    if (branch == null || branch === '') return null
    pool = templates.filter(t => String(t.branch_key || '') === branch)
    if (!pool.length) return null
  } else if (templates.length > 1) {
    return null
  }
  if (pool.length === 1) return pool[0]
  const idx = Math.min(pool.length - 1, Math.max(0, Math.floor(Number(rng()) * pool.length)))
  return pool[idx]
}

export function compactThemeGroups(
  groups,
  keys = QUICK_CHAT_THEME_KEYS,
  labels = QUICK_CHAT_THEME_LABELS,
) {
  const list = Array.isArray(groups) ? groups : []
  const byKey = new Map(list.map(g => [g.theme_key, g]))
  return keys.map(key => {
    const group = byKey.get(key)
    if (!group) return null
    const name = labels[key] || group.theme_name
    return name === group.theme_name ? group : { ...group, theme_name: name }
  }).filter(Boolean)
}

export function groupTemplatesByTheme(templates, _suggestedTheme = '') {
  const list = Array.isArray(templates) ? templates : []
  const order = []
  const map = new Map()

  for (const template of list) {
    const key = String(template?.theme_key || '').trim() || '_other'
    if (!map.has(key)) {
      map.set(key, {
        theme_key: key,
        theme_name: String(template?.theme_name || key).trim() || key,
        templates: [],
      })
      order.push(key)
    }
    map.get(key).templates.push(template)
  }

  return order.map(key => map.get(key))
}
