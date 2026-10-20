export function centrifugoUrl() {
  if (typeof window === 'undefined') return 'wss://cxvo.ru/connection/websocket'
  const proto = window.location.protocol === 'https:' ? 'wss:' : 'ws:'
  return `${proto}//${window.location.host}/connection/websocket`
}
