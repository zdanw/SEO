<template>
  <div class="login-page">
    <el-card class="login-card" shadow="hover">
      <h2 class="title">SEO Platform 登录</h2>
      <p class="subtitle">内容创作 · 站内优化 · 社交分发 · 排名监控</p>
      <el-tabs v-model="tab" class="login-tabs">
        <el-tab-pane label="登录" name="login">
          <el-form ref="loginFormRef" :model="loginForm" :rules="loginRules" label-position="top" size="large">
            <el-form-item label="邮箱" prop="email">
              <el-input v-model="loginForm.email" placeholder="请输入邮箱" />
            </el-form-item>
            <el-form-item label="密码" prop="password">
              <el-input v-model="loginForm.password" type="password" placeholder="请输入密码 (至少 6 位)" show-password />
            </el-form-item>
            <el-button type="primary" style="width: 100%" :loading="loading" @click="doLogin">
              登 录
            </el-button>
          </el-form>
        </el-tab-pane>
        <el-tab-pane label="注册新账号" name="register">
          <el-form ref="registerFormRef" :model="registerForm" :rules="registerRules" label-position="top" size="large">
            <el-form-item label="邮箱" prop="email">
              <el-input v-model="registerForm.email" placeholder="请输入邮箱" />
            </el-form-item>
            <el-form-item label="昵称" prop="fullName">
              <el-input v-model="registerForm.fullName" placeholder="请输入昵称（选填）" />
            </el-form-item>
            <el-form-item label="密码" prop="password">
              <el-input v-model="registerForm.password" type="password" placeholder="至少 8 位" show-password />
            </el-form-item>
            <el-button type="success" style="width: 100%" :loading="loading" @click="doRegister">
              注 册
            </el-button>
          </el-form>
        </el-tab-pane>
      </el-tabs>
    </el-card>
  </div>
</template>

<script setup lang="ts">
import { reactive, ref } from 'vue'
import { useRouter } from 'vue-router'
import type { FormInstance, FormRules } from 'element-plus'
import { ElMessage } from 'element-plus'
import http from '@/api/http'

const router = useRouter()
const tab = ref<'login' | 'register'>('login')
const loading = ref(false)

const loginFormRef = ref<FormInstance>()
const registerFormRef = ref<FormInstance>()

const loginForm = reactive({ email: '', password: '' })
const registerForm = reactive({ email: '', password: '', fullName: '' })

const baseRules: FormRules = {
  email: [{ required: true, message: '请输入邮箱', trigger: 'blur' }, { type: 'email', message: '邮箱格式不正确', trigger: 'blur' }],
  password: [{ required: true, message: '请输入密码', trigger: 'blur' }],
}
const loginRules = reactive<FormRules>({ ...baseRules })
const registerRules = reactive<FormRules>({
  ...baseRules,
  password: [{ required: true, min: 8, message: '密码至少 8 位', trigger: 'blur' }],
})

async function doLogin() {
  if (!loginFormRef.value) return
  await loginFormRef.value.validate()
  try {
    loading.value = true
    const res: any = await http.post('/auth/login', loginForm)
    localStorage.setItem('access_token', res.access_token)
    localStorage.setItem('user_email', loginForm.email)
    ElMessage.success('登录成功')
    router.replace('/dashboard')
  } finally {
    loading.value = false
  }
}

async function doRegister() {
  if (!registerFormRef.value) return
  await registerFormRef.value.validate()
  try {
    loading.value = true
    const payload: any = { email: registerForm.email, password: registerForm.password }
    if (registerForm.fullName) payload.full_name = registerForm.fullName
    await http.post('/auth/register', payload)
    ElMessage.success('注册成功，请登录')
    tab.value = 'login'
    loginForm.email = registerForm.email
  } finally {
    loading.value = false
  }
}
</script>

<style lang="scss" scoped>
.login-page {
  height: 100vh;
  display: flex;
  align-items: center;
  justify-content: center;
  background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
}
.login-card {
  width: 440px;
  border-radius: 12px;
}
.title {
  text-align: center;
  margin: 12px 0 4px;
  font-size: 22px;
}
.subtitle {
  text-align: center;
  color: #909399;
  font-size: 13px;
  margin-bottom: 16px;
}
</style>
