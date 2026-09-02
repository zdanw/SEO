import http from './http'

export interface Backlink {
  id: number
  user_id: number
  target_url: string
  source_url: string
  anchor_text?: string | null
  domain_authority?: number | null
  is_alive: boolean
  first_seen_at: string
  last_checked_at?: string | null
  lost_at?: string | null
}

export interface BacklinkCreate {
  target_url: string
  source_url: string
  anchor_text?: string
  domain_authority?: number
}

export interface BacklinkUpdate {
  anchor_text?: string
  domain_authority?: number
  is_alive?: boolean
}

export function listBacklinks(params?: { is_alive?: boolean; page?: number; size?: number }) {
  return http.get<any, Backlink[]>('/backlinks', { params })
}

export function createBacklink(payload: BacklinkCreate) {
  return http.post<any, Backlink>('/backlinks', payload)
}

export function updateBacklink(id: number, payload: BacklinkUpdate) {
  return http.patch<any, Backlink>(`/backlinks/${id}`, payload)
}

export function deleteBacklink(id: number) {
  return http.delete(`/backlinks/${id}`)
}

export function checkBacklink(id: number) {
  return http.post<any, Backlink>(`/backlinks/${id}/check`, {})
}

export function checkAllBacklinks(limit = 100) {
  return http.post<any, { total: number; alive: number; dead: number }>('/backlinks/check-all', {}, {
    params: { limit },
  })
}
