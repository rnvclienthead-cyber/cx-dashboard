import test from 'node:test'
import assert from 'node:assert/strict'
import {
  setAuthSession,
  getAuthToken,
  getAuthRole,
  clearAuthSession,
  markSessionUse,
  sessionUsedRecently,
} from '../src/authStorage.js'

test('remembers a recent human action and forgets it on logout', () => {
  clearAuthSession()
  assert.equal(sessionUsedRecently(), false)
  markSessionUse()
  assert.equal(sessionUsedRecently(), true)
  clearAuthSession()
  assert.equal(sessionUsedRecently(), false)
})

test('keeps token in memory even without localStorage', () => {
  clearAuthSession()
  setAuthSession({ token: 'abc', role: 'admin', username: 'r' })
  assert.equal(getAuthToken(), 'abc')
  assert.equal(getAuthRole(), 'admin')
  clearAuthSession()
  assert.equal(getAuthToken(), '')
})
