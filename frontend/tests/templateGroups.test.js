import test from 'node:test'
import assert from 'node:assert/strict'
import { groupTemplatesByTheme, templateVariantLabel } from '../src/utils/templateGroups.js'
import { readFileSync } from 'node:fs'

test('groups templates by theme and keeps suggested theme first', () => {
  const groups = groupTemplatesByTheme([
    { id: 1, theme_key: 'sborka', theme_name: 'Сборка', title: 'Сборка', body: 'a' },
    { id: 2, theme_key: 'vozvrat', theme_name: 'Возврат', title: 'Возврат: причина', branch_label: 'причина', body: 'b' },
    { id: 3, theme_key: 'vozvrat', theme_name: 'Возврат', title: 'Возврат: брак', branch_label: 'брак', body: 'c' },
  ], 'vozvrat')
  assert.equal(groups.length, 2)
  assert.equal(groups[0].theme_key, 'vozvrat')
  assert.equal(groups[0].templates.length, 2)
  assert.equal(groups[1].theme_key, 'sborka')
})

test('variant label prefers branch label', () => {
  assert.equal(templateVariantLabel({
    theme_name: 'Возврат',
    title: 'Возврат: Уточнить причину',
    branch_label: 'Уточнить причину',
  }), 'Уточнить причину')
  assert.equal(templateVariantLabel({
    theme_name: 'Сборка',
    title: 'Сборка',
  }), 'Основной')
})

test('chats quick templates use theme folders', () => {
  const source = readFileSync(new URL('../src/views/ChatsView.vue', import.meta.url), 'utf8')
  assert.match(source, /groupTemplatesByTheme/)
  assert.match(source, /chatTemplateGroups/)
  assert.match(source, /Темы · \$\{chatTemplateGroups\.length\}/)
  assert.match(source, /onChatThemeChip/)
  assert.doesNotMatch(source, /Все · \$\{visibleChatTemplates\.length\}/)
})

test('questions quick templates use theme folders', () => {
  const source = readFileSync(new URL('../src/views/QuestionsView.vue', import.meta.url), 'utf8')
  assert.match(source, /groupTemplatesByTheme/)
  assert.match(source, /chipGroupsFor/)
  assert.match(source, /onQuestionThemeChip/)
})
