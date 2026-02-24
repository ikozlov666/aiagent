import { useEffect, useRef, useCallback } from 'react'
import { useStore } from '../stores/useStore'

const HEARTBEAT_TIMEOUT_MS = 45_000 // If no heartbeat for 45s, consider dead

export function useWebSocket(projectId) {
  const wsRef = useRef(null)
  const reconnectTimeout = useRef(null)
  const storeRef = useRef(null)
  const isConnectingRef = useRef(false)
  const heartbeatTimer = useRef(null)
  
  // Get store functions once and store in ref
  const store = useStore()
  storeRef.current = store

  const resetHeartbeatTimer = useCallback(() => {
    if (heartbeatTimer.current) clearTimeout(heartbeatTimer.current)
    heartbeatTimer.current = setTimeout(() => {
      console.warn('💔 No heartbeat received — connection may be dead, reconnecting...')
      if (wsRef.current && wsRef.current.readyState === WebSocket.OPEN) {
        wsRef.current.close(4000, 'Heartbeat timeout')
      }
    }, HEARTBEAT_TIMEOUT_MS)
  }, [])

  const handleMessage = useCallback((data) => {
    const { updatePorts, addAgentStep, setAgentStatus, addMessage, addStreamingChunk, setLiveAssistantContent } = storeRef.current || {}
    if (!updatePorts || !addAgentStep || !setAgentStatus || !addMessage) return

    // Any message from the server resets the heartbeat watchdog
    resetHeartbeatTimer()

    if (data.type === 'heartbeat') return

    if (data.type === 'agent_stream_chunk') {
      if (addStreamingChunk) addStreamingChunk(data.content || '')
      setAgentStatus?.('working')
      return
    }
    
    switch (data.type) {
      case 'connected':
        console.log('🎉 Connected to project:', data.project_id)
        updatePorts(data.ports || {})
        break

      case 'agent_step': {
        const stepData = {
          ...data,
          type: data.step_type || data.type,
        }
        addAgentStep(stepData)
        const stepType = data.step_type || data.type
        const content = (data.content || '').trim()
        if (stepType === 'thinking') {
          setAgentStatus('thinking')
          if (setLiveAssistantContent) setLiveAssistantContent(content || 'Думаю...')
        } else if (stepType === 'llm_text') {
          setAgentStatus('working')
          if (setLiveAssistantContent) setLiveAssistantContent(content || '')
        } else if (stepType === 'tool_call' || stepType === 'tool_result') {
          setAgentStatus('working')
        }
        break
      }

      case 'agent_response':
        addMessage({ role: 'assistant', content: data.content })
        if (storeRef.current?.clearStreamingContent) storeRef.current.clearStreamingContent()
        if (storeRef.current?.setLiveAssistantContent) storeRef.current.setLiveAssistantContent('')
        setAgentStatus('done')
        break

      case 'agent_stopped':
        addMessage({ role: 'assistant', content: data.content || '⏹️ Агент остановлен' })
        if (storeRef.current?.clearStreamingContent) storeRef.current.clearStreamingContent()
        if (storeRef.current?.setLiveAssistantContent) storeRef.current.setLiveAssistantContent('')
        setAgentStatus('idle')
        break

      case 'ports_update':
        console.log('[Preview] WS ports_update received', data.ports || {})
        updatePorts(data.ports || {})
        break

      case 'error':
        addMessage({ role: 'error', content: data.content })
        if (storeRef.current?.clearStreamingContent) storeRef.current.clearStreamingContent()
        if (storeRef.current?.setLiveAssistantContent) storeRef.current.setLiveAssistantContent('')
        setAgentStatus('error')
        break

      case 'user_message':
        // Already added locally, skip
        break

      default:
        console.log('Unknown WS message type:', data.type)
    }
  }, []) // No dependencies - use ref instead

  const connect = useCallback(() => {
    if (!projectId) return
    
    // Prevent multiple simultaneous connections
    if (isConnectingRef.current || (wsRef.current && wsRef.current.readyState === WebSocket.CONNECTING)) {
      return
    }
    
    // Don't reconnect if already connected
    if (wsRef.current && wsRef.current.readyState === WebSocket.OPEN) {
      return
    }
    
    isConnectingRef.current = true
    
    // Close existing connection
    if (wsRef.current) {
      wsRef.current.close()
      wsRef.current = null
    }

    // Clear any pending reconnect
    if (reconnectTimeout.current) {
      clearTimeout(reconnectTimeout.current)
      reconnectTimeout.current = null
    }

    const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:'
    const wsUrl = `${protocol}//${window.location.host}/ws/chat/${projectId}`
    
    console.log(`🔌 Connecting to ${wsUrl}`)
    const ws = new WebSocket(wsUrl)

    ws.onopen = () => {
      console.log('✅ WebSocket connected')
      isConnectingRef.current = false
      resetHeartbeatTimer()
      const { setWs } = storeRef.current || {}
      if (setWs) setWs(ws)
    }

    ws.onmessage = (event) => {
      try {
        const data = JSON.parse(event.data)
        handleMessage(data)
      } catch (e) {
        console.error('Failed to parse WS message:', e)
      }
    }

    ws.onclose = (event) => {
      console.log('❌ WebSocket disconnected', event.code, event.reason)
      isConnectingRef.current = false
      if (heartbeatTimer.current) clearTimeout(heartbeatTimer.current)
      const { setWs } = storeRef.current || {}
      if (setWs) setWs(null)
      
      // Reconnect on non-manual close (1000) or heartbeat timeout (4000)
      if (event.code !== 1000 && !reconnectTimeout.current && wsRef.current === ws) {
        const delay = event.code === 4000 ? 1000 : 5000
        reconnectTimeout.current = setTimeout(() => {
          reconnectTimeout.current = null
          connect()
        }, delay)
      }
    }

    ws.onerror = (error) => {
      console.error('WebSocket error:', error)
      isConnectingRef.current = false
    }

    wsRef.current = ws
  }, [projectId]) // Only depend on projectId

  useEffect(() => {
    connect()
    return () => {
      if (heartbeatTimer.current) {
        clearTimeout(heartbeatTimer.current)
        heartbeatTimer.current = null
      }
      if (reconnectTimeout.current) {
        clearTimeout(reconnectTimeout.current)
        reconnectTimeout.current = null
      }
      if (wsRef.current) {
        wsRef.current.close(1000, 'Component unmounting')
        wsRef.current = null
      }
      isConnectingRef.current = false
    }
  }, [projectId]) // Only depend on projectId, not connect

  return { connected: wsRef.current?.readyState === WebSocket.OPEN }
}
