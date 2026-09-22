import http from './http'

export interface QueueInfo {
  depth: number | null
  alert: boolean
}

export interface QueueHealth {
  queues: Record<string, QueueInfo>
  alert_threshold: number
  alerts: string[]
  checked_at: string
}

export interface CostSnapshot {
  serp_provider: string
  serp_allow_proxy_fallback: boolean
  serp_today: {
    used: number
    remaining: number | null
    daily_quota: number
    estimated_cost_usd: number
    est_cost_per_request: number
  }
  publish_7d: {
    posts_success_rate: number | null
    posts_ok: number
    posts_total: number
    comments_success_rate: number | null
    comments_ok: number
    comments_total: number
  }
  note: string
}

export interface TaskRunRow {
  id: number
  task_name: string
  celery_task_id: string | null
  request_id: string | null
  business_type: string | null
  business_id: number | null
  status: string
  started_at: string | null
  finished_at: string | null
  error_message: string | null
  retries: number
  meta: Record<string, unknown> | null
}

export function getQueueHealth() {
  return http.get<any, QueueHealth>('/ops/queues')
}

export function getCostSnapshot() {
  return http.get<any, CostSnapshot>('/ops/costs')
}

export function listRecentTasks(params?: {
  business_type?: string
  business_id?: number
  limit?: number
}) {
  return http.get<any, TaskRunRow[]>('/ops/tasks', { params })
}
