/** Left-click without modifiers: run fn. Cmd/Ctrl/Shift/Alt/middle: keep native <a> behaviour. */
export function onAppClick(event, fn) {
  if (event.defaultPrevented) return
  if (typeof event.button === 'number' && event.button !== 0) return
  if (event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return
  event.preventDefault()
  fn()
}

export function hrefFor(router, to) {
  return router.resolve(to).href
}

export function sameQuery(a = {}, b = {}, keys) {
  return keys.every((key) => String(a[key] ?? '') === String(b[key] ?? ''))
}
