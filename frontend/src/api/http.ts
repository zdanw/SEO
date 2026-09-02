import axios, {
  AxiosInstance,
  InternalAxiosRequestConfig,
  AxiosResponse,
  AxiosError,
} from 'axios'
import { ElMessage } from 'element-plus'
import router from '@/router'

const http: AxiosInstance = axios.create({
  baseURL: '/api/v1',
  timeout: 120000,
  headers: {
    'Content-Type': 'application/json',
  },
})

http.interceptors.request.use(
  (config: InternalAxiosRequestConfig) => {
    const token = localStorage.getItem('access_token')
    if (token && config.headers) {
      config.headers.Authorization = `Bearer ${token}`
    }
    return config
  },
  (error: AxiosError) => Promise.reject(error),
)

// 响应拦截：统一错误处理
http.interceptors.response.use(
  (response: AxiosResponse) => response.data,
  (error: AxiosError<{ detail?: string | Record<string, unknown> }>) => {
    const status = error?.response?.status
    if (status === 401) {
      localStorage.removeItem('access_token')
      ElMessage.error('登录已过期，请重新登录')
      router.replace('/login')
    } else if (status === 403) {
      ElMessage.error('没有权限访问该资源')
    } else if (typeof status === 'number' && status >= 500) {
      ElMessage.error('服务器错误，请稍后重试')
    } else {
      const detail = error?.response?.data?.detail
      const msg = detail ?? error?.message ?? '请求失败'
      ElMessage.error(typeof msg === 'string' ? msg : JSON.stringify(msg))
    }
    return Promise.reject(error)
  },
)

export default http
