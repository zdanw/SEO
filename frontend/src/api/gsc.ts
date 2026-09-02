import http from './http'

export interface GscStatus {
  configured: boolean
  connected: boolean
  google_email?: string | null
  site_url?: string | null
  updated_at?: string | null
}

export interface GscSite {
  site_url: string
  permission_level?: string | null
}

export interface GscSummary {
  start_date: string
  end_date: string
  site_url: string
  clicks: number
  impressions: number
  ctr: number
  position: number
}

export interface GscAnalyticsRow {
  key: string
  clicks: number
  impressions: number
  ctr: number
  position: number
}

export interface GscAnalytics {
  start_date: string
  end_date: string
  site_url: string
  dimension: string
  rows: GscAnalyticsRow[]
}

export function getGscStatus() {
  return http.get<any, GscStatus>('/gsc/status')
}

export function startGscOAuth() {
  return http.get<any, { auth_url: string }>('/gsc/oauth/start')
}

export function disconnectGsc() {
  return http.delete<any, void>('/gsc/disconnect')
}

export function listGscSites() {
  return http.get<any, GscSite[]>('/gsc/sites')
}

export function selectGscSite(siteUrl: string) {
  return http.put<any, GscStatus>('/gsc/sites/active', { site_url: siteUrl })
}

export function getGscSummary(days = 28) {
  return http.get<any, GscSummary>('/gsc/analytics/summary', { params: { days } })
}

export function getGscAnalytics(params: {
  days?: number
  dimension?: 'query' | 'page'
  row_limit?: number
}) {
  return http.get<any, GscAnalytics>('/gsc/analytics', { params })
}
