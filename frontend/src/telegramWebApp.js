// Telegram Mini App bootstrap: вход по initData и разбор startapp.

export const telegramShell = {
  active: false,
}

const MP_FROM_CODE = { w: 'wb', o: 'ozon', y: 'ym' }

export function parseStartParam(raw) {
  const s = String(raw || '').trim()
  if (!s) return { mp: '', chat: '' }
  if (s.length >= 2 && s[1] === '_' && MP_FROM_CODE[s[0]]) {
    return { mp: MP_FROM_CODE[s[0]], chat: s.slice(2).replace('__', ':') }
  }
  return { mp: 'wb', chat: s }
}

export function isTelegramMiniApp() {
  const tg = typeof window !== 'undefined' ? window.Telegram?.WebApp : null
  return !!(tg && (tg.initData || tg.initDataUnsafe?.user))
}

export function openExternalLink(url) {
  const href = String(url || '').trim()
  if (!href) return
  const tg = typeof window !== 'undefined' ? window.Telegram?.WebApp : null
  if (tg && typeof tg.openLink === 'function') {
    tg.openLink(href)
    return
  }
  window.open(href, '_blank', 'noopener')
}

function applyStartParam(start, rememberAfterLogin) {
  const { mp, chat } = parseStartParam(start)
  if (!chat || typeof window === 'undefined') return
  const params = new URLSearchParams(window.location.search)
  if (!params.get('chat')) params.set('chat', chat)
  if (mp && !params.get('mp')) params.set('mp', mp)
  const target = `/chats?${params.toString()}`
  rememberAfterLogin(target)
  window.history.replaceState(null, '', target)
}

export function isProbablyTelegramShell() {
  if (typeof window === 'undefined') return false
  if (window.Telegram?.WebApp) return true
  if (window.TelegramWebviewProxy) return true
  return /Telegram/i.test(navigator.userAgent || '')
}

function loadTelegramScript() {
  return new Promise((resolve) => {
    if (typeof window === 'undefined' || window.Telegram?.WebApp) return resolve()
    if (!isProbablyTelegramShell()) return resolve()
    let settled = false
    const done = () => {
      if (settled) return
      settled = true
      resolve()
    }
    const s = document.createElement('script')
    s.src = 'https://telegram.org/js/telegram-web-app.js'
    s.async = true
    s.onload = done
    s.onerror = done
    document.head.appendChild(s)
    setTimeout(done, 1500)
  })
}

export async function bootstrapTelegram() {
  await loadTelegramScript()
  const tg = typeof window !== 'undefined' ? window.Telegram?.WebApp : null
  if (!isTelegramMiniApp() || !tg) return
  telegramShell.active = true
  try { tg.ready() } catch { /* ignore */ }
  try { tg.expand() } catch { /* ignore */ }
  try { tg.setHeaderColor('#ffffff') } catch { /* ignore */ }
  try { tg.setBackgroundColor('#f8fafc') } catch { /* ignore */ }

  const { setAuthSession, rememberAfterLogin } = await import('./authStorage')

  const start = tg.initDataUnsafe?.start_param || ''
  if (start) applyStartParam(start, rememberAfterLogin)
  if (typeof window !== 'undefined' && window.location.pathname === '/chats') {
    rememberAfterLogin(window.location.pathname + window.location.search)
  }

  const initData = tg.initData
  if (!initData) return
  try {
    const res = await fetch('/api/auth/telegram-webapp', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ init_data: initData }),
    })
    const data = await res.json().catch(() => ({}))
    if (!res.ok || !data.access_token) {
      const detail = data.detail
      try {
        sessionStorage.setItem(
          'tgAuthError',
          typeof detail === 'string' ? detail : 'Этот Telegram не привязан к профилю на сайте',
        )
      } catch { /* ignore */ }
      return
    }
    setAuthSession({
      token: data.access_token,
      role: data.role,
      username: data.username,
    })
    try { sessionStorage.removeItem('tgAuthError') } catch { /* ignore */ }
  } catch {
    try { sessionStorage.setItem('tgAuthError', 'Не удалось войти через Telegram') } catch { /* ignore */ }
  }
}
