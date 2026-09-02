import http from './http'

export type PostType = 'consultation' | 'experience'
export type ReviewStatus =
  | 'draft' | 'pending_review' | 'approved' | 'rejected' | 'posting' | 'posted' | 'failed'

export interface RedditAccount {
  id: number
  account_name: string
  is_active: boolean
}

export interface RedditStatus {
  configured: boolean
  connected: boolean
  accounts: RedditAccount[]
}

export interface RedditPost {
  id: number
  site_id: number
  account_id: number
  post_type: PostType
  subreddit: string
  keyword: string
  title: string
  body: string
  site_url?: string | null
  status: ReviewStatus
  reddit_post_id?: string | null
  reddit_permalink?: string | null
  error_message?: string | null
  account_name?: string | null
  created_at: string
  updated_at: string
}

export interface RedditComment {
  id: number
  site_id: number
  account_id: number
  target_post_url: string
  target_thing_id: string
  subreddit: string
  target_post_title?: string | null
  body: string
  keyword?: string | null
  discover_source: string
  status: ReviewStatus
  reddit_comment_id?: string | null
  error_message?: string | null
  account_name?: string | null
  created_at: string
  updated_at: string
}

export interface RedditDiscoverItem {
  title: string
  url: string
  thing_id: string
  subreddit: string
  score: number
  num_comments: number
  created_utc: number
}

export function getRedditStatus() {
  return http.get<any, RedditStatus>('/reddit/status')
}

export function startRedditOAuth() {
  return http.get<any, { auth_url: string }>('/reddit/oauth/start')
}

export function generateRedditPost(payload: {
  account_id: number
  post_type: PostType
  subreddit: string
  keyword: string
  include_site_url?: boolean
}) {
  return http.post<any, RedditPost>('/reddit/posts/generate', payload)
}

export function listRedditPosts(params?: { status?: string }) {
  return http.get<any, RedditPost[]>('/reddit/posts', { params })
}

export function updateRedditPost(id: number, payload: { title?: string; body?: string; subreddit?: string }) {
  return http.patch<any, RedditPost>(`/reddit/posts/${id}`, payload)
}

export function approveRedditPost(id: number) {
  return http.post<any, RedditPost>(`/reddit/posts/${id}/approve`, {})
}

export function rejectRedditPost(id: number) {
  return http.post<any, RedditPost>(`/reddit/posts/${id}/reject`, {})
}

export function publishRedditPost(id: number) {
  return http.post<any, RedditPost>(`/reddit/posts/${id}/publish`, {})
}

export function generateRedditComment(payload: {
  account_id: number
  target_post_url: string
  include_site_url?: boolean
}) {
  return http.post<any, RedditComment>('/reddit/comments/generate', payload)
}

export function generateRedditCommentBatch(payload: {
  account_id: number
  subreddit: string
  keyword: string
  post_urls: string[]
  include_site_url?: boolean
}) {
  return http.post<any, RedditComment[]>('/reddit/comments/generate-batch', payload)
}

export function listRedditComments(params?: { status?: string }) {
  return http.get<any, RedditComment[]>('/reddit/comments', { params })
}

export function updateRedditComment(id: number, payload: { body?: string }) {
  return http.patch<any, RedditComment>(`/reddit/comments/${id}`, payload)
}

export function approveRedditComment(id: number) {
  return http.post<any, RedditComment>(`/reddit/comments/${id}/approve`, {})
}

export function rejectRedditComment(id: number) {
  return http.post<any, RedditComment>(`/reddit/comments/${id}/reject`, {})
}

export function publishRedditComment(id: number) {
  return http.post<any, RedditComment>(`/reddit/comments/${id}/publish`, {})
}

export function searchRedditPosts(params: {
  subreddit: string
  keyword: string
  limit?: number
}) {
  return http.get<any, { items: RedditDiscoverItem[]; meta: { auto_mode: boolean; queued_count: number } }>(
    '/reddit/discover/search',
    { params },
  )
}
