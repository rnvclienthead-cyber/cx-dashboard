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

export function groupTemplatesByTheme(templates, suggestedTheme = '') {
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

  const groups = order.map(key => map.get(key))
  if (!suggestedTheme) return groups
  return [...groups].sort((a, b) => {
    if (a.theme_key === suggestedTheme && b.theme_key !== suggestedTheme) return -1
    if (b.theme_key === suggestedTheme && a.theme_key !== suggestedTheme) return 1
    return 0
  })
}
