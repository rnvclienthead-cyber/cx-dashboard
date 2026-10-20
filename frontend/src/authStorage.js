// Сессия в памяти: в Mini App Telegram localStorage часто недоступен.

const memory = {
  token: '',
  role: '',
  username: '',
}

// Последнее действие человека. Фоновые опросы меню срок входа не двигают.
let lastUseAt = 0
const USE_WINDOW_MS = 2 * 60 * 1000

function fromStore(store, key) {
  try {
    if (!store) return ''
    return store.getItem(key) || ''
  } catch {
    return ''
  }
}

function toStore(store, key, value) {
  try {
    if (!store) return
    if (value) store.setItem(key, value)
    else store.removeItem(key)
  } catch { /* Mini App / private mode */ }
}

function read(key) {
  const local = typeof localStorage !== 'undefined' ? localStorage : null
  const fromLocal = fromStore(local, key)
  // Другая вкладка могла продлить вход — берём более свежий токен из хранилища.
  if (fromLocal) {
    memory[key] = fromLocal
    return fromLocal
  }
  if (memory[key]) return memory[key]
  const session = typeof sessionStorage !== 'undefined' ? sessionStorage : null
  const value = fromStore(session, key)
  if (value) memory[key] = value
  return value
}

function write(key, value) {
  memory[key] = value || ''
  const local = typeof localStorage !== 'undefined' ? localStorage : null
  const session = typeof sessionStorage !== 'undefined' ? sessionStorage : null
  toStore(local, key, memory[key])
  toStore(session, key, memory[key])
}

export function getAuthToken() {
  return read('token')
}

export function getAuthRole() {
  return read('role')
}

export function getAuthUsername() {
  return read('username')
}

export function setAuthSession({ token = '', role = '', username = '' } = {}) {
  write('token', token)
  write('role', role)
  write('username', username)
}

export function markSessionUse() {
  lastUseAt = Date.now()
}

export function sessionUsedRecently() {
  return lastUseAt > 0 && Date.now() - lastUseAt < USE_WINDOW_MS
}

export function clearAuthSession() {
  lastUseAt = 0
  setAuthSession({ token: '', role: '', username: '' })
}

export function rememberAfterLogin(path) {
  if (!path || path === '/login') return
  try { sessionStorage.setItem('afterLogin', path) } catch { /* ignore */ }
}

export function takeAfterLogin() {
  try {
    const next = sessionStorage.getItem('afterLogin') || ''
    sessionStorage.removeItem('afterLogin')
    return next
  } catch {
    return ''
  }
}
