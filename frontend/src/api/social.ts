import http from './http'

export type Platform = 'pulseforge' | 'linkedin' | 'twitter' | 'facebook' | 'reddit'
export type PostStatus = 'pending' | 'scheduled' | 'posting' | 'posted' | 'failed' | 'cancelled'

export interface SocialAccount {
  id: number
  user_id: number
  platform: Platform
  account_name: string
  access_token?: string
  token_expires_at?: string | null
  config?: Record<string, any> | null
  is_active: boolean
  created_at: string
}

export interface SocialAccountCreate {
  platform: Platform
  account_name: string
  access_token?: string
  config?: Record<string, any>
  is_active?: boolean
}

export interface SocialAccountUpdate {
  account_name?: string
  access_token?: string
  config?: Record<string, any>
  is_active?: boolean
}

export interface SocialPost {
  id: number
  account_id: number
  title?: string
  summary?: string
  image_url?: string
  external_url?: string
  hashtags?: string[] | null
  scheduled_at?: string | null
  posted_at?: string | null
  platform_post_id?: string | null
  status: PostStatus
  engagement?: Record<string, any> | null
  error_message?: string | null
  retry_count: number
  created_at: string
  updated_at: string
  platform?: string
  account_name?: string
}

export interface SocialPostCreate {
  account_id: number
  title?: string
  summary?: string
  image_url?: string
  external_url?: string
  hashtags?: string[]
  scheduled_at?: string
}

export interface SocialPostUpdate {
  title?: string
  summary?: string
  image_url?: string
  external_url?: string
  hashtags?: string[]
  scheduled_at?: string
}

export function listAccounts() {
  return http.get<any, SocialAccount[]>('/social/accounts')
}

export function createAccount(payload: SocialAccountCreate) {
  return http.post<any, SocialAccount>('/social/accounts', payload)
}

export function updateAccount(id: number, payload: SocialAccountUpdate) {
  return http.patch<any, SocialAccount>(`/social/accounts/${id}`, payload)
}

export function deleteAccount(id: number) {
  return http.delete(`/social/accounts/${id}`)
}

export function listPosts(params?: { status?: string; account_id?: number }) {
  return http.get<any, SocialPost[]>('/social/posts', { params })
}

export function createPost(payload: SocialPostCreate) {
  return http.post<any, SocialPost>('/social/posts', payload)
}

export function updatePost(id: number, payload: SocialPostUpdate) {
  return http.patch<any, SocialPost>(`/social/posts/${id}`, payload)
}

export function deletePost(id: number) {
  return http.delete(`/social/posts/${id}`)
}

export function sendPostNow(id: number) {
  return http.post<any, SocialPost>(`/social/posts/${id}/send-now`, {})
}
