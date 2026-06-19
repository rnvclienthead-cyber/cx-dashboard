import { createRouter, createWebHistory } from 'vue-router'
import { usePermissionsStore } from '../stores/permissions'

import SynchronizerView from '../views/SynchronizerView.vue'
import AITaggingView from '../views/AITaggingView.vue'
import ModerationView from '../views/ModerationView.vue'
import AITrainingView from '../views/AITrainingView.vue'
import SystemLogsView from '../views/SystemLogsView.vue'
import ProductionView from '../views/ProductionView.vue'
import PPMView from '../views/PPMView.vue'
import RatingsView from '../views/RatingsView.vue'
import LoginView from '../views/LoginView.vue'
import AdminView from '../views/AdminView.vue'
import FinancialLossView from '../views/FinancialLossView.vue'
import VocView from '../views/VocView.vue'
import ExecutiveDashboardView from '../views/ExecutiveDashboardView.vue'
import RegistryView from '../views/RegistryView.vue'
import ReshipmentView from '../views/ReshipmentView.vue'
import ReshipmentFormView from '../views/ReshipmentFormView.vue'
import ReshipmentConfirmView from '../views/ReshipmentConfirmView.vue'
import ResetPasswordView from '../views/ResetPasswordView.vue'

// Порядок совпадает с порядком в сайдбаре — первый доступный модуль станет стартовой страницей
export const MODULE_ROUTES = [
  { module: 'dashboard',   path: '/' },
  { module: 'admin_panel', path: '/admin' },
  { module: 'sync',        path: '/sync' },
  { module: 'ai_tagging',  path: '/ai-tagging' },
  { module: 'moderation',  path: '/moderation' },
  { module: 'ai_training', path: '/ai-training' },
  { module: 'logs',        path: '/logs' },
  { module: 'production',  path: '/production' },
  { module: 'ppm',         path: '/ppm' },
  { module: 'voc',         path: '/voc' },
  { module: 'ratings',     path: '/ratings' },
  { module: 'reshipment',  path: '/reshipment' },
  { module: 'finances',    path: '/finances' },
]

const router = createRouter({
  history: createWebHistory(import.meta.env.BASE_URL),
  routes: [
    { path: '/',                         name: 'dashboard',         component: ExecutiveDashboardView, meta: { requiresAuth: true } },
    { path: '/sync',                     name: 'sync',              component: SynchronizerView,       meta: { requiresAuth: true } },
    { path: '/ai-tagging',               name: 'tagging',           component: AITaggingView,          meta: { requiresAuth: true } },
    { path: '/moderation',               name: 'moderation',        component: ModerationView,         meta: { requiresAuth: true } },
    { path: '/ai-training',              name: 'training',          component: AITrainingView,         meta: { requiresAuth: true } },
    { path: '/logs',                     name: 'logs',              component: SystemLogsView,         meta: { requiresAuth: true } },
    { path: '/production',               name: 'production',        component: ProductionView,         meta: { requiresAuth: true } },
    { path: '/ratings',                  name: 'ratings',           component: RatingsView,            meta: { requiresAuth: true } },
    { path: '/ppm',                      name: 'ppm',               component: PPMView,                meta: { requiresAuth: true } },
    { path: '/login',                    name: 'login',             component: LoginView },
    { path: '/admin',                    name: 'admin',             component: AdminView,              meta: { requiresAuth: true } },
    { path: '/finances',                 name: 'finances',          component: FinancialLossView,      meta: { requiresAuth: true } },
    { path: '/voc',                      name: 'voc',               component: VocView,                meta: { requiresAuth: true } },
    { path: '/registry',                 name: 'registry',          component: RegistryView,           meta: { requiresAuth: true } },
    { path: '/reshipment',               name: 'reshipment',        component: ReshipmentView,         meta: { requiresAuth: true } },
    { path: '/reshipment/form',          name: 'reshipment-form',   component: ReshipmentFormView,     meta: { public: true } },
    { path: '/reshipment/confirm/:token',name: 'reshipment-confirm',component: ReshipmentConfirmView,  meta: { public: true } },
    { path: '/reset-password',           name: 'reset-password',    component: ResetPasswordView,      meta: { public: true } },
  ]
})

router.beforeEach(async (to, from, next) => {
  const token = localStorage.getItem('token')

  if (!token) {
    if (to.meta.requiresAuth) return next('/login')
    return next()
  }

  // Грузим права один раз — до первого рендера защищённой страницы
  const permStore = usePermissionsStore()
  if (!permStore.loaded) {
    await permStore.load()
  }

  if (to.meta.requiresAuth) return next()
  next()
})

export default router
