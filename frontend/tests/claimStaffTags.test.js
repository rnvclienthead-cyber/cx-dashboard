import test from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'

import { PROBLEM_TAGS, tagIdsEqual } from '../src/utils/problemTags.js'

test('problem tags are the 13 map categories', () => {
  assert.equal(Object.keys(PROBLEM_TAGS).length, 13)
  assert.match(PROBLEM_TAGS[7], /Хлипкость/)
  assert.equal(tagIdsEqual([7, 4], [4, 7]), true)
  assert.equal(tagIdsEqual([7], [4, 7]), false)
})

const returnsSrc = readFileSync(new URL('../src/views/ReturnsView.vue', import.meta.url), 'utf8')
const chatsSrc = readFileSync(new URL('../src/views/ChatsView.vue', import.meta.url), 'utf8')
const modSrc = readFileSync(new URL('../src/views/ModerationView.vue', import.meta.url), 'utf8')

test('approve no longer asks for staff tags', () => {
  assert.doesNotMatch(returnsSrc, /ClaimStaffTagsModal/)
  assert.doesNotMatch(returnsSrc, /staff_tag_ids/)
  assert.match(returnsSrc, /confirm\(`\$\{a\.label\}/)
  assert.doesNotMatch(chatsSrc, /ClaimStaffTagsModal/)
  assert.doesNotMatch(chatsSrc, /staff_tag_ids/)
  assert.match(chatsSrc, /confirm\(`\$\{a\.label\}/)
})

test('moderation has mismatch filter and staff tags', () => {
  assert.match(modSrc, /Расхождения/)
  assert.match(modSrc, /mismatchCount/)
  assert.match(modSrc, /staffTagIds/)
  assert.match(modSrc, /Теги сотрудника/)
})
