import http from './http'

export interface LoginPayload { email: string; password: string }
export interface TokenResp { access_token: string; expires_in: number; token_type: string }
export interface RegisterPayload { email: string; password: string; full_name?: string }
export interface UserResp { id: number; email: string; full_name?: string; is_active: boolean; created_at: string }

export function login(payload: LoginPayload) {
  return http.post<any, TokenResp>('/auth/login', payload)
}

export function register(payload: RegisterPayload) {
  return http.post<any, UserResp>('/auth/register', payload)
}
