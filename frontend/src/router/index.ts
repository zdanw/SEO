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
        meta: { title: '综合大屏', icon: 'DataBoard' },
      },
      {
        path: 'articles',
        name: 'ArticleList',
        component: () => import('@/views/ArticleList.vue'),
        meta: { title: '文章列表', icon: 'Document' },
      },
      {
        path: 'articles/new',
        name: 'ArticleNew',
        component: () => import('@/views/ArticleEditor.vue'),
        meta: { title: '新建文章', icon: 'EditPen' },
      },
      {
        path: 'articles/:id',
        name: 'ArticleEdit',
        component: () => import('@/views/ArticleEditor.vue'),
        meta: { title: '编辑文章', icon: 'EditPen' },
      },
      {
        path: 'seo-checker',
        name: 'SeoChecker',
        component: () => import('@/views/SeoChecker.vue'),
        meta: { title: 'SEO 检查', icon: 'Check' },
      },
      {
        path: 'social',
        name: 'SocialScheduler',
        component: () => import('@/views/SocialScheduler.vue'),
        meta: { title: '社交分发', icon: 'Share' },
      },
      {
        path: 'serp',
        name: 'SerpMonitor',
        component: () => import('@/views/SerpMonitor.vue'),
        meta: { title: '排名监控', icon: 'TrendCharts' },
      },
      {
        path: 'search-console',
        name: 'SearchConsole',
        component: () => import('@/views/SearchConsole.vue'),
        meta: { title: 'Search Console', icon: 'Monitor' },
      },
      {
        path: 'backlinks',
        name: 'Backlinks',
        component: () => import('@/views/Backlinks.vue'),
        meta: { title: '外链监控', icon: 'Link' },
      },
      {
        path: 'competitors',
        name: 'Competitors',
        component: () => import('@/views/Competitors.vue'),
        meta: { title: '竞品对标', icon: 'Compass' },
      },
      {
        path: 'recommendations',
        name: 'Recommendations',
        component: () => import('@/views/Recommendations.vue'),
        meta: { title: '优化建议', icon: 'Bell' },
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
