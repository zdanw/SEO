import http from './http'

export interface Keyword {
  id: number
  user_id: number
  keyword: string
  target_url?: string
  search_engine: string
  region: string
  priority: number
  status: string
  created_at: string
}

export interface KeywordCreate {
  keyword: string
  target_url?: string
  search_engine?: string
  region?: string
  priority?: number
}

export function listKeywords() {
  return http.get<any, Keyword[]>('/keywords')
}

export function createKeyword(payload: KeywordCreate) {
  return http.post<any, Keyword>('/keywords', payload)
}

export function deleteKeyword(id: number) {
  return http.delete(`/keywords/${id}`)
}
