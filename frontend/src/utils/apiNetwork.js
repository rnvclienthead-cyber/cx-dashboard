export const GET_RETRY_DELAY_MS = 700

export function isIdempotentMethod(method) {
  const m = String(method || 'GET').toUpperCase()
  return m === 'GET' || m === 'HEAD'
}

export function networkErrorMessage() {
  return 'Нет связи с сервером. Подождите пару секунд и обновите страницу.'
}

export function httpErrorMessage(status, isJson) {
  if (!isJson && (status === 502 || status === 503 || status === 504)) {
    return 'Сервер временно не отвечает. Подождите пару секунд и обновите страницу.'
  }
  return 'Ошибка сервера'
}

export function shouldRetryGet(method, attempt, maxAttempts = 2) {
  return isIdempotentMethod(method) && attempt < maxAttempts
}

export function shouldRecreateEventSource(readyState) {
  return readyState === 2 // EventSource.CLOSED
}
