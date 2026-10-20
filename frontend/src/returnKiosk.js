/** Складской полноэкран: сканер и карточка. Очередь /returns — обычный кабинет с сайдбаром. */
export function isReturnKioskPath(path) {
  const p = String(path || '').split('?')[0]
  return p === '/return/scan' || p.startsWith('/return/')
}

/** Клиентские страницы без сайдбара кабинета — иначе телефон грузит весь дашборд и белеет. */
export function isClientFormPath(path) {
  const p = String(path || '').split('?')[0]
  return p === '/reshipment/form' || p.startsWith('/reshipment/form/')
    || p.startsWith('/reshipment/confirm/')
}

export function isVidovitoFormHost() {
  if (typeof window === 'undefined') return false
  return String(window.location.hostname || '').includes('vidovito.com')
}

export function skipDashboardShell(path) {
  return isReturnKioskPath(path) || isClientFormPath(path) || isVidovitoFormHost()
}
