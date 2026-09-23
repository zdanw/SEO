import {
  createRouter,
  createWebHistory,
  type RouteRecordRaw,
  type RouteLocationNormalized,
  type NavigationGuardNext,
} from 'vue-router'
import NProgress from 'nprogress'
import 'nprogress/nprogress.css'

NProgress.configure({ showSpinner: false })

const routes: RouteRecordRaw[] = [
  {
    path: '/login',
    name: 'Login',
    component: () => import('@/views/Login.vue'),
    meta: { title: '登录', layout: 'blank' },
  },
  {
    path: '/',
    component: () => import('@/layout/DefaultLayout.vue'),
    redirect: '/dashboard',
    children: [
      {
        path: 'dashboard',
        name: 'Dashboard',
        component: () => import('@/views/Dashboard.vue'),
        meta: { title: '数据监控', icon: 'DataBoard' },
      },
      {
        path: 'sites',
        name: 'Sites',
        component: () => import('@/views/Sites.vue'),
        meta: { title: '站点', icon: 'Platform' },
      },
      {
        path: 'social',
        name: 'SocialScheduler',
        component: () => import('@/views/SocialScheduler.vue'),
        meta: { title: '社交分发', icon: 'Share' },
      },
      {
        path: 'accounts',
        name: 'SocialAccounts',
        component: () => import('@/views/SocialAccounts.vue'),
        meta: { title: '社交账号', icon: 'User' },
      },
      {
        path: 'brands',
        name: 'Brands',
        component: () => import('@/views/Brands.vue'),
        meta: { title: '品牌', icon: 'OfficeBuilding' },
      },
      {
        path: 'products',
        name: 'Products',
        component: () => import('@/views/Products.vue'),
        meta: { title: '产品', icon: 'Goods' },
      },
      {
        path: 'serp',
        name: 'SerpMonitor',
        component: () => import('@/views/SerpMonitor.vue'),
        meta: { title: '排名监控', icon: 'TrendCharts' },
      },
      {
        path: 'ops',
        name: 'Ops',
        component: () => import('@/views/Ops.vue'),
        meta: { title: '运维看板', icon: 'Monitor' },
      },
    ],
  },
  { path: '/:pathMatch(.*)*', redirect: '/dashboard' },
]

const router = createRouter({
  history: createWebHistory(),
  routes,
})

router.beforeEach(
  (
    to: RouteLocationNormalized,
    _from: RouteLocationNormalized,
    next: NavigationGuardNext,
  ) => {
    NProgress.start()
    document.title = (to.meta.title ? `${to.meta.title} - ` : '') + 'SEO Platform'

    const token = localStorage.getItem('access_token')
    if (to.path === '/login') {
      if (token) {
        next('/dashboard')
      } else {
        next()
      }
      return
    }
    if (!token) {
      next('/login')
      return
    }
    next()
  },
)

router.afterEach(() => NProgress.done())

export default router
