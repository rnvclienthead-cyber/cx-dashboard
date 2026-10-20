import test from 'node:test'
import assert from 'node:assert/strict'
import { compactThemeGroups, groupTemplatesByTheme, pickThemeTemplate, templateVariantLabel, templatePreviewText } from '../src/utils/templateGroups.js'
import { readFileSync } from 'node:fs'

test('groups templates by theme and keeps API order', () => {
  const groups = groupTemplatesByTheme([
    { id: 1, theme_key: 'sborka', theme_name: 'Сборка', title: 'Сборка', body: 'a' },
    { id: 2, theme_key: 'vozvrat', theme_name: 'Возврат', title: 'Возврат: причина', branch_label: 'причина', body: 'b' },
    { id: 3, theme_key: 'vozvrat', theme_name: 'Возврат', title: 'Возврат: брак', branch_label: 'брак', body: 'c' },
  ], 'vozvrat')
  assert.equal(groups.length, 2)
  assert.equal(groups[0].theme_key, 'sborka')
  assert.equal(groups[1].theme_key, 'vozvrat')
  assert.equal(groups[1].templates.length, 2)
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

test('template preview shows truncated body', () => {
  assert.equal(
    templatePreviewText({ body: 'Короткий ответ покупателю.' }),
    'Короткий ответ покупателю.',
  )
  const long = 'Слово '.repeat(40).trim()
  const preview = templatePreviewText({ body: long }, 60)
  assert.ok(preview.endsWith('…'))
  assert.ok(preview.length <= 65)
})

test('compact chips pin delivery, missing part and reship consent', () => {
  const groups = groupTemplatesByTheme([
    { id: 1, theme_key: 'utochnenie', theme_name: 'Уточнение: меню 1-5', title: 'Уточнение', body: 'u' },
    { id: 2, theme_key: 'nekomplekt', theme_name: 'Не хватает детали', title: 'Не хватает', body: 'n' },
    { id: 3, theme_key: 'soglasie', theme_name: 'Согласие на доотправку', title: 'Согласие', body: 's' },
    { id: 4, theme_key: 'dostavka_wb', theme_name: 'Где мой заказ / сроки доставки', title: 'Где заказ', body: 'd' },
  ], 'utochnenie')
  const chips = compactThemeGroups(groups)
  assert.deepEqual(chips.map(g => g.theme_key), ['dostavka_wb', 'nekomplekt', 'soglasie'])
  assert.deepEqual(chips.map(g => g.theme_name), [
    'Где мой заказ',
    'Не хватает детали',
    'Согласие на доотправку',
  ])
})

test('compact chips skip missing themes instead of filling with others', () => {
  const groups = groupTemplatesByTheme([
    { id: 1, theme_key: 'dostavka_wb', theme_name: 'Где мой заказ', title: 'Где заказ', body: 'd' },
    { id: 2, theme_key: 'nekomplekt', theme_name: 'Не хватает детали', title: 'Не хватает', body: 'n' },
    { id: 3, theme_key: 'utochnenie', theme_name: 'Уточнение', title: 'Уточнение', body: 'u' },
  ])
  const chips = compactThemeGroups(groups)
  assert.deepEqual(chips.map(g => g.theme_key), ['dostavka_wb', 'nekomplekt'])
})

test('dostavka chip picks our delay for FBS and marketplace text for FBO', () => {
  const group = {
    theme_key: 'dostavka_wb',
    theme_name: 'Где мой заказ',
    templates: [
      { id: 'm1', branch_key: '', body: 'смотрите в приложении' },
      { id: 'm2', branch_key: '', body: 'уточните в поддержке' },
      { id: 'o1', branch_key: 'nasha_zaderzhka', body: 'передали логистам' },
      { id: 'o2', branch_key: 'nasha_zaderzhka', body: 'ускоряем отгрузку' },
    ],
  }
  const fbs = pickThemeTemplate(group, 'FBS', () => 0)
  const fbo = pickThemeTemplate(group, 'FBO', () => 0)
  const unknown = pickThemeTemplate(group, '', () => 0)
  assert.equal(fbs.branch_key, 'nasha_zaderzhka')
  assert.equal(fbs.body, 'передали логистам')
  assert.equal(fbo.branch_key, '')
  assert.equal(fbo.body, 'смотрите в приложении')
  assert.equal(unknown, null)
})

test('nekomplekt chip picks allowed or denied by sku flag', () => {
  const group = {
    theme_key: 'nekomplekt',
    theme_name: 'Не хватает детали',
    templates: [
      { id: 'ok', branch_key: 'sku_allowed', body: 'отправим деталь' },
      { id: 'no', branch_key: 'sku_denied', body: 'оформите возврат' },
    ],
  }
  const allowed = pickThemeTemplate(group, '', { nekomplektBranch: 'sku_allowed', rng: () => 0 })
  const denied = pickThemeTemplate(group, '', { nekomplektBranch: 'sku_denied', rng: () => 0 })
  const unknown = pickThemeTemplate(group, '', { rng: () => 0 })
  assert.equal(allowed.body, 'отправим деталь')
  assert.equal(denied.body, 'оформите возврат')
  assert.equal(unknown, null)
})

test('chats quick templates use theme folders', () => {
  const source = readFileSync(new URL('../src/views/ChatsView.vue', import.meta.url), 'utf8')
  assert.match(source, /groupTemplatesByTheme/)
  assert.match(source, /chatTemplateGroups/)
  assert.match(source, /Темы · \$\{chatTemplateGroups\.length\}/)
  assert.match(source, /onChatThemeChip/)
  assert.match(source, /templatePreviewText/)
  assert.doesNotMatch(source, /Все · \$\{visibleChatTemplates\.length\}/)
  assert.doesNotMatch(source, /suggestedTheme && a.theme_key === suggestedTheme/)
})

test('questions quick templates use theme folders', () => {
  const source = readFileSync(new URL('../src/views/QuestionsView.vue', import.meta.url), 'utf8')
  assert.match(source, /groupTemplatesByTheme/)
  assert.match(source, /chipGroupsFor/)
  assert.match(source, /onQuestionThemeChip/)
  assert.match(source, /templatePreviewText/)
})
