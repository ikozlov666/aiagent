import { create } from 'zustand'

export const useStore = create((set, get) => ({
  // Authentication
  user: null,
  token: localStorage.getItem('auth_token') || null,
  isAuthenticated: !!localStorage.getItem('auth_token'),

  // Project
  projectId: null,
  projectPorts: {},
  projectStatus: 'idle', // idle | creating | ready | error

  // Chat messages
  messages: [],

  // Streaming reply (пока идёт стрим простого ответа; сбрасывается при agent_response или при новом сообщении)
  streamingContent: '',

  // Трансляция в чат: текущий «живой» текст (Думает / сообщение DeepSeek), пока агент работает
  liveAssistantContent: '',

  // Agent activity steps
  agentSteps: [],
  agentStatus: 'idle', // idle | thinking | working | done | error

  // WebSocket
  ws: null,
  wsConnected: false,

  // Authentication actions
  login: async (email, password) => {
    try {
      const res = await fetch('/api/auth/login', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ email, password }),
      })
      
      if (!res.ok) {
        const text = await res.text()
        let detail = 'Ошибка входа'
        try {
          const err = text ? JSON.parse(text) : {}
          const d = err.detail
          if (typeof d === 'string') detail = d
          else if (Array.isArray(d)) detail = d.map(x => x.msg || x).join(', ') || detail
          else if (d && typeof d === 'object' && d.msg) detail = d.msg
        } catch {
          if (res.status === 401) detail = 'Неверный email или пароль'
          else if (res.status === 404 || res.status === 502 || res.status === 503 || res.status === 504) detail = 'Сервер недоступен. Проверьте, что бэкенд запущен (docker compose up).'
          else if (res.status >= 500) detail = 'Ошибка сервера. Попробуйте позже.'
          else if (text) detail = text.slice(0, 200)
        }
        throw new Error(detail)
      }
      
      const data = await res.json()
      localStorage.setItem('auth_token', data.access_token)
      set({ 
        token: data.access_token,
        user: { id: data.user_id, username: data.username },
        isAuthenticated: true 
      })
      return data
    } catch (e) {
      throw e
    }
  },

  register: async (email, username, password) => {
    try {
      const res = await fetch('/api/auth/register', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ email, username, password }),
      })
      
      if (!res.ok) {
        const error = await res.json().catch(() => ({ detail: 'Ошибка регистрации' }))
        throw new Error(error.detail || 'Ошибка регистрации')
      }
      
      const data = await res.json()
      localStorage.setItem('auth_token', data.access_token)
      set({ 
        token: data.access_token,
        user: { id: data.user_id, username: data.username },
        isAuthenticated: true 
      })
      return data
    } catch (e) {
      throw e
    }
  },

  logout: () => {
    localStorage.removeItem('auth_token')
    set({ 
      token: null,
      user: null,
      isAuthenticated: false,
      projectId: null,
      projectPorts: {},
      messages: [],
      agentSteps: [],
    })
  },

  checkAuth: async () => {
    const token = get().token
    if (!token) return false
    
    try {
      const res = await fetch('/api/auth/me', {
        headers: { 'Authorization': `Bearer ${token}` },
      })
      
      if (!res.ok) {
        get().logout()
        return false
      }
      
      const user = await res.json()
      set({ user, isAuthenticated: true })
      return true
    } catch (e) {
      get().logout()
      return false
    }
  },

  // Actions
  setProject: (projectId, ports) => set({ 
    projectId, 
    projectPorts: ports || {},
    projectStatus: 'ready' 
  }),

  addMessage: (message) => set((state) => ({
    messages: [...state.messages, { ...message, id: Date.now(), timestamp: new Date() }],
    streamingContent: message.role === 'assistant' ? '' : state.streamingContent,
    liveAssistantContent: message.role === 'user' ? '' : state.liveAssistantContent,
  })),

  addStreamingChunk: (chunk) => set((state) => ({
    streamingContent: (state.streamingContent || '') + (chunk || ''),
  })),

  clearStreamingContent: () => set({ streamingContent: '' }),

  setLiveAssistantContent: (text) => set({ liveAssistantContent: text ?? '' }),

  addAgentStep: (step) => set((state) => ({
    agentSteps: [...state.agentSteps, step],
  })),

  clearAgentSteps: () => set({ agentSteps: [] }),

  setAgentStatus: (status) => set({ agentStatus: status }),

  setWs: (ws) => set({ ws, wsConnected: !!ws }),

  updatePorts: (ports) => set({ projectPorts: ports }),

  // Create new project
  createProject: async (name) => {
    set({ projectStatus: 'creating' })
    try {
      const { token } = get()
      const headers = { 'Content-Type': 'application/json' }
      if (token) {
        headers['Authorization'] = `Bearer ${token}`
      }
      
      const res = await fetch('/api/projects', {
        method: 'POST',
        headers,
        body: JSON.stringify({ name }),
      })
      
      // Check if response is ok
      if (!res.ok) {
        const errorText = await res.text()
        let errorMessage = `Ошибка сервера: ${res.status} ${res.statusText}`
        try {
          const errorJson = JSON.parse(errorText)
          errorMessage = errorJson.detail || errorJson.message || errorMessage
        } catch {
          if (errorText) {
            errorMessage = errorText
          }
        }
        throw new Error(errorMessage)
      }
      
      // Check if response has content
      const contentType = res.headers.get('content-type')
      if (!contentType || !contentType.includes('application/json')) {
        const text = await res.text()
        if (!text) {
          throw new Error('Сервер вернул пустой ответ')
        }
        throw new Error(`Неожиданный тип ответа: ${contentType}`)
      }
      
      const data = await res.json()
      set({ 
        projectId: data.project_id,
        projectPorts: data.ports,
        projectStatus: 'ready',
        messages: [],
        agentSteps: [],
        streamingContent: '',
      })
      return data
    } catch (e) {
      set({ projectStatus: 'error' })
      throw e
    }
  },

  // Send message via WebSocket (attachedFiles: [{ filename, content }] — из буфера обмена)
  sendMessage: (message, images = null, attachedFiles = null) => {
    const { ws, wsConnected } = get()
    if (ws && wsConnected) {
      const payload = { message }
      if (images && images.length > 0) {
        payload.images = images
      }
      if (attachedFiles && attachedFiles.length > 0) {
        payload.attached_files = attachedFiles.map((f) => ({ filename: f.filename, content: f.content }))
      }
      ws.send(JSON.stringify(payload))
      get().addMessage({ role: 'user', content: message, images, attachedFiles: attachedFiles || undefined })
      set({ agentStatus: 'thinking', agentSteps: [], streamingContent: '' })
    }
  },

  // Stop agent execution
  stopAgent: () => {
    const { ws, wsConnected } = get()
    if (ws && wsConnected) {
      ws.send(JSON.stringify({ type: 'stop' }))
      set({ agentStatus: 'idle' })
    }
  },
}))
