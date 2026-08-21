import { defineStore } from 'pinia'
import { ref } from 'vue'
import { api } from '../api'
import type { AuthUser } from '../types'

export const useAuthStore = defineStore('auth', () => {
  const user = ref<AuthUser | null>(null)
  const loaded = ref(false)

  async function load() {
    try {
      user.value = await api<AuthUser>('/auth/me')
    } catch {
      user.value = null
    } finally {
      loaded.value = true
    }
  }

  function setUser(next: AuthUser | null) {
    user.value = next
    loaded.value = true
  }

  return { user, loaded, load, setUser }
})
