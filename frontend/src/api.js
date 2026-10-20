// frontend/src/api.js
import router from './router'
import {
  getAuthToken,
  getAuthRole,
  getAuthUsername,
  setAuthSession,
  clearAuthSession,
  sessionUsedRecently,
} from './authStorage'
import {
  GET_RETRY_DELAY_MS,
  httpErrorMessage,
  networkErrorMessage,
  shouldRetryGet,
} from './utils/apiNetwork'

function sleep(ms) {
  return new Promise((resolve) => setTimeout(resolve, ms))
}

export const apiFetch = async (url, options = {}) => {
  const token = getAuthToken()

  const headers = { ...options.headers }
  if (token) headers['Authorization'] = `Bearer ${token}`
  if (token && sessionUsedRecently()) headers['X-Session-Touch'] = '1'

  if (options.body && typeof options.body === 'string' && !headers['Content-Type']) {
    headers['Content-Type'] = 'application/json'
  }

  const config = { ...options, headers }
  const method = options.method || 'GET'
  const maxAttempts = shouldRetryGet(method, 1) ? 2 : 1
  let lastError = null

  for (let attempt = 1; attempt <= maxAttempts; attempt++) {
    try {
      const response = await fetch(url, config)

      const fresh = response.headers.get('X-Access-Token')
      if (fresh && response.status !== 401) {
        setAuthSession({
          token: fresh,
          role: getAuthRole(),
          username: getAuthUsername(),
        })
      }

      // 1. Если токен протух — выкидываем на логин
      if (response.status === 401) {
        console.warn("Токен недействителен. Выполняем выход.")
        clearAuthSession()
        router.push('/login')
        const error = new Error('Сессия истекла. Пожалуйста, войдите снова.')
        error.status = 401
        throw error
      }

      // 2. УМНАЯ ЗАЩИТА: Если бэкенд ответил любой другой ошибкой (400, 403, 500)
      if (!response.ok) {
        let errorMessage = httpErrorMessage(response.status, false)
        let errorData = null
        const contentType = response.headers.get('content-type') || ''
        const looksJson = contentType.includes('json')
        try {
          errorData = await response.json()
          errorMessage = errorData.detail || httpErrorMessage(response.status, true)
        } catch (e) {
          errorMessage = httpErrorMessage(response.status, looksJson)
        }
        const error = new Error(errorMessage)
        error.status = response.status
        error.data = errorData
        throw error
      }

      return response
    } catch (err) {
      lastError = err
      const transient = !err?.status || err.status === 502 || err.status === 503 || err.status === 504
      if (!transient) throw err
      if (shouldRetryGet(method, attempt, maxAttempts)) {
        await sleep(GET_RETRY_DELAY_MS)
        continue
      }
      if (err?.status) throw err
      const error = new Error(networkErrorMessage())
      error.cause = err
      throw error
    }
  }

  throw lastError || new Error(networkErrorMessage())
}
