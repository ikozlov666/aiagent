import { useState, useEffect, useRef, useMemo, useCallback, lazy, Suspense } from 'react'
import { useStore } from './stores/useStore'
import { useWebSocket } from './hooks/useWebSocket'
import Chat from './components/Chat/Chat'
import AgentActivity from './components/AgentActivity/AgentActivity'
import ProcessDialog from './components/ProcessDialog/ProcessDialog'
import FileTree from './components/FileTree/FileTree'
import CodeEditor from './components/Editor/Editor'
import NeonIcon from './components/NeonIcon'
import Login from './components/Auth/Login'
import Register from './components/Auth/Register'

// Lazy load Terminal to prevent blocking on import errors
const Terminal = lazy(() => import('./components/Terminal/Terminal'))

function ContainerLogsPanel({ projectId }) {
  const [logs, setLogs] = useState('')
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState(null)

  const fetchLogs = useCallback(async () => {
    if (!projectId) return
    setLoading(true)
    setError(null)
    try {
      const res = await fetch(`/api/projects/${projectId}/logs?tail=300`)
      if (!res.ok) {
        const data = await res.json().catch(() => ({}))
        throw new Error(data.detail || `Ошибка ${res.status}`)
      }
      const data = await res.json()
      setLogs(data.logs || '')
    } catch (e) {
      setError(e.message)
      setLogs('')
    } finally {
      setLoading(false)
    }
  }, [projectId])

  useEffect(() => {
    if (projectId) fetchLogs()
  }, [projectId, fetchLogs])

  if (!projectId) {
    return (
      <div className="flex flex-col h-full bg-dark-800 items-center justify-center text-gray-600">
        <div className="text-sm">Логи контейнера</div>
        <div className="text-xs text-gray-700 mt-1">Создайте проект для просмотра логов</div>
      </div>
    )
  }

  return (
    <div className="flex flex-col h-full bg-dark-800">
      <div className="flex items-center justify-between px-3 py-2 border-b border-dark-500 text-xs bg-dark-700">
        <span className="font-medium">Логи контейнера (sandbox)</span>
        <button
          onClick={fetchLogs}
          disabled={loading}
          className="px-2 py-1 rounded bg-dark-600 hover:bg-dark-500 text-gray-300 disabled:opacity-50"
        >
          {loading ? 'Загрузка…' : 'Обновить'}
        </button>
      </div>
      <div className="flex-1 overflow-auto p-3">
        {error && <div className="text-red-400 text-sm mb-2">{error}</div>}
        <pre className="text-xs text-gray-300 font-mono whitespace-pre-wrap break-words">{logs || (loading ? 'Загрузка…' : 'Нет логов')}</pre>
      </div>
    </div>
  )
}

function NoVNCPanel({ port, projectId }) {
  if (!port) {
    return (
      <div className="flex flex-col h-full bg-dark-800 items-center justify-center text-gray-600">
        <div className="text-3xl mb-2">🌐</div>
        <div className="text-sm">Браузер агента (noVNC)</div>
        <div className="text-xs text-gray-700 mt-1">Порт ещё не назначен</div>
        <div className="text-xs text-gray-600 mt-2 text-center max-w-xs">
          noVNC будет доступен после создания проекта
        </div>
      </div>
    )
  }

  const novncUrl = `http://${window.location.hostname}:${port}/vnc.html?resize=scale&autoconnect=1&password=`
  
  return (
    <div className="flex flex-col h-full bg-dark-800">
      <div className="flex items-center gap-2 px-3 py-2 border-b border-dark-500 text-xs bg-dark-700">
        <span>🌐</span>
        <span className="font-medium">Браузер агента (noVNC)</span>
        <a
          href={novncUrl}
          target="_blank"
          rel="noopener"
          className="ml-auto text-blue-400 hover:text-blue-300 font-mono"
        >
          :{port} ↗
        </a>
        <div className="text-xs text-gray-500">
          {projectId ? '🟢 Активен' : '🔴 Неактивен'}
        </div>
      </div>
      <iframe
        src={novncUrl}
        className="flex-1 w-full border-0 bg-black"
        title="noVNC Browser"
        allow="clipboard-read; clipboard-write"
      />
    </div>
  )
}

function BrowserPanel({ port, title, icon, allPorts }) {
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState(false)
  const [selectedPort, setSelectedPort] = useState(port)

  // Get all dev server ports (exclude noVNC port 6080)
  const devPorts = allPorts ? Object.entries(allPorts)
    .filter(([containerPort]) => containerPort !== '6080')
    .map(([containerPort, hostPort]) => ({ containerPort, hostPort }))
    : []

  // Use selected port or fallback to provided port
  const activePort = selectedPort || port

  useEffect(() => {
    if (activePort) {
      setLoading(true)
      setError(false)
      // Update selected port if port prop changes
      if (port && port !== selectedPort) {
        setSelectedPort(port)
      }
    }
  }, [activePort, port, selectedPort])

  if (!activePort) {
    console.log('[Preview] BrowserPanel: no activePort', { title, port, allPorts: allPorts || {} })
    return (
      <div className="flex flex-col h-full bg-dark-800 items-center justify-center text-gray-600">
        <div className="text-3xl mb-2">{icon}</div>
        <div className="text-sm">{title}</div>
        <div className="text-xs text-gray-700 mt-1">Порт ещё не назначен</div>
        <div className="text-xs text-gray-600 mt-2 text-center max-w-xs">
          Dev-сервер будет автоматически обнаружен<br/>после запуска агентом
        </div>
      </div>
    )
  }

  const url = `http://${window.location.hostname}:${activePort}`
  console.log('[Preview] BrowserPanel: loading iframe', { title, activePort, url, loading, error })
  
  return (
    <div className="flex flex-col h-full bg-dark-800">
      <div className="flex items-center gap-2 px-3 py-2 border-b border-dark-500 text-xs bg-dark-700">
        <span>{icon}</span>
        <span className="font-medium">{title}</span>
        
        {/* Port selector if multiple ports available */}
        {devPorts.length > 1 && (
          <select
            value={activePort}
            onChange={(e) => {
              setSelectedPort(e.target.value)
              setLoading(true)
              setError(false)
            }}
            className="ml-2 px-2 py-0.5 bg-dark-600 border border-dark-500 rounded text-xs text-gray-300 focus:outline-none focus:border-blue-500"
          >
            {devPorts.map(({ containerPort, hostPort }) => (
              <option key={hostPort} value={hostPort}>
                {containerPort} → {hostPort}
              </option>
            ))}
          </select>
        )}
        
        <a
          href={url}
          target="_blank"
          rel="noopener"
          className="ml-auto text-blue-400 hover:text-blue-300 font-mono"
        >
          :{activePort} ↗
        </a>
        <button
          onClick={() => {
            setLoading(true)
            setError(false)
          }}
          className="text-gray-500 hover:text-gray-300 transition-colors"
          title="Обновить"
        >
          🔄
        </button>
      </div>
      {loading && (
        <div className="flex items-center justify-center h-full">
          <div className="text-gray-500 text-sm">Загрузка превью...</div>
        </div>
      )}
      <iframe
        src={url}
        className="flex-1 w-full border-0 bg-white"
        title={title}
        onLoad={() => { setLoading(false); console.log('[Preview] iframe onLoad ok', url) }}
        onError={() => {
          setLoading(false)
          setError(true)
          console.log('[Preview] iframe onError', url)
        }}
        style={{ display: loading || error ? 'none' : 'block' }}
      />
      {error && (
        <div className="flex flex-col items-center justify-center h-full text-red-400 text-sm p-4">
          <div>❌ Не удалось загрузить превью</div>
          <div className="text-xs text-gray-500 mt-1">Проверьте, что dev-сервер запущен на порту {port}</div>
          <button
            onClick={() => {
              setError(false)
              setLoading(true)
            }}
            className="mt-2 px-3 py-1 bg-dark-700 hover:bg-dark-600 rounded text-xs"
          >
            Повторить
          </button>
        </div>
      )}
    </div>
  )
}

function CreateProjectScreen({ onCreated }) {
  const [name, setName] = useState('')
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState(null)
  const createProject = useStore(s => s.createProject)

  const handleCreate = async () => {
    setLoading(true)
    setError(null)
    try {
      const project = await createProject(name || undefined)
      onCreated(project)
    } catch (e) {
      setError(e.message)
    } finally {
      setLoading(false)
    }
  }

  return (
    <div className="min-h-screen bg-dark-900 flex items-center justify-center p-4">
      <div className="max-w-md w-full space-y-8 text-center">
        <div>
          <h1 className="text-4xl font-bold bg-gradient-to-r from-blue-400 via-purple-400 to-pink-400 bg-clip-text text-transparent">
            AI Agent Platform
          </h1>
          <p className="text-gray-500 mt-3">
            Среда разработки с ИИ-агентом
          </p>
        </div>

        <div className="bg-dark-800 rounded-2xl p-6 border border-dark-500 space-y-4">
          <input
            type="text"
            value={name}
            onChange={(e) => setName(e.target.value)}
            placeholder="Название проекта (необязательно)"
            className="w-full bg-dark-700 border border-dark-500 rounded-xl px-4 py-3 text-sm
              text-gray-200 placeholder-gray-600 outline-none focus:border-blue-500/50 transition-all"
            onKeyDown={(e) => e.key === 'Enter' && handleCreate()}
          />

          <button
            onClick={handleCreate}
            disabled={loading}
            className="w-full py-3 bg-blue-600 hover:bg-blue-500 rounded-xl text-white font-medium
              transition-all disabled:opacity-50 active:scale-[0.98]"
          >
            {loading ? (
              <span className="flex items-center justify-center gap-2">
                <div className="w-4 h-4 border-2 border-white/30 border-t-white rounded-full animate-spin" />
                Создаю sandbox...
              </span>
            ) : (
              '🚀 Создать проект'
            )}
          </button>

          {error && (
            <div className="text-red-400 text-sm bg-red-500/10 border border-red-500/20 rounded-lg px-3 py-2">
              {error}
            </div>
          )}
        </div>

        <div className="text-xs text-gray-600 space-y-1">
          <p>Каждый проект запускается в изолированном Docker-контейнере</p>
          <p>Ubuntu 22.04 • Node.js 20 • Python 3.12 • Playwright</p>
        </div>
      </div>
    </div>
  )
}

function WorkspaceScreen() {
  const { projectId, projectPorts } = useStore()
  const [activeRightPanel, setActiveRightPanel] = useState('editor') // editor | terminal | preview | browser | logs
  const [activeCenterPanel, setActiveCenterPanel] = useState('files') // files | activity | dialog
  const savedCenterSizeRef = useRef(20)
  const [layoutPercents, setLayoutPercents] = useState(() => ({ chat: 25, center: 20, right: 55 }))
  const [draggingHandle, setDraggingHandle] = useState(null) // 0 = чат|центр, 1 = центр|право
  const groupContainerRef = useRef(null)
  const dragLayoutRef = useRef({ chat: 25, center: 20, right: 55 })
  const [selectedFile, setSelectedFile] = useState(null)
  const PANEL_IDS = { chat: 'workspace-chat', center: 'workspace-center', right: 'workspace-right' }
  useWebSocket(projectId)

  // Ответственно за сворачивание/разворачивание центральной панели (Файлы/Активность/Диалог).
  // Библиотека не применяет resize/setLayout/collapse — сворачиваем через условный рендер: при true рендерим только 2 панели (чат + правую).
  const toggleCenterPanel = () => {
    const wantExpand = centerPanelCollapsed
    if (wantExpand) {
      const center = savedCenterSizeRef.current || 20
      setCenterPanelCollapsed(false)
      setLayoutPercents(prev => {
        const chat = prev.chat ?? 25
        const next = { chat, center, right: 100 - chat - center }
        localStorage.setItem('panel-layout', JSON.stringify(next))
        return next
      })
    } else {
      savedCenterSizeRef.current = layoutPercents.center ?? 20
      const chat = layoutPercents.chat ?? 25
      const next = { chat, center: 0, right: 100 - chat }
      localStorage.setItem('panel-layout', JSON.stringify(next))
      setLayoutPercents(next)
      setCenterPanelCollapsed(true)
    }
  }

  // Библиотека отдаёт layout не в 0–100, а в своей шкале (~15). Нормализуем к процентам для сохранения и проверки.
  const normalizeLayoutToPercents = (layout) => {
    if (!layout || typeof layout !== 'object') return layout
    const sum = Object.values(layout).reduce((a, b) => a + (Number(b) || 0), 0)
    if (sum <= 0) return layout
    const out = {}
    for (const [id, val] of Object.entries(layout)) {
      out[id] = Math.round((Number(val) / sum) * 1000) / 10
    }
    return out
  }

  // Восстанавливаем layout из localStorage. Всегда в процентах 0–100 (если сохранено в старой шкале — нормализуем).
  const getDefaultLayout = () => {
    const defaultThree = { [PANEL_IDS.chat]: 25, [PANEL_IDS.center]: 20, [PANEL_IDS.right]: 55 }
    const saved = localStorage.getItem('panel-layout')
    if (saved) {
      try {
        let parsed = JSON.parse(saved)
        if (parsed && typeof parsed === 'object') {
          if (Array.isArray(parsed) && parsed.length === 3) {
            parsed = { [PANEL_IDS.chat]: parsed[0], [PANEL_IDS.center]: parsed[1], [PANEL_IDS.right]: parsed[2] }
          }
          if (parsed[PANEL_IDS.chat] != null && parsed[PANEL_IDS.right] != null) {
            const hasCenter = parsed[PANEL_IDS.center] != null
            const sum = (Number(parsed[PANEL_IDS.chat]) || 0) + (Number(parsed[PANEL_IDS.center]) || 0) + (Number(parsed[PANEL_IDS.right]) || 0)
            if (sum > 0 && (sum < 50 || sum > 150)) {
              parsed = normalizeLayoutToPercents(parsed)
            }
            if (!hasCenter) parsed[PANEL_IDS.center] = 20
            return parsed
          }
        }
      } catch {
        // ignore
      }
    }
    return defaultThree
  }

  const defaultLayout = useMemo(() => getDefaultLayout(), [])
  const layout2Panels = useMemo(() => ({ [PANEL_IDS.chat]: 25, [PANEL_IDS.right]: 75 }), [])
  const [centerPanelCollapsed, setCenterPanelCollapsed] = useState(() => (defaultLayout[PANEL_IDS.center] ?? 20) < 2)

  useEffect(() => {
    setLayoutPercents(prev => ({ ...prev, ...defaultLayout }))
  }, [])

  const onResizeHandlePointerDown = (e, handleIndex) => {
    e.preventDefault()
    setDraggingHandle(handleIndex)
    e.currentTarget.setPointerCapture(e.pointerId)
  }
  useEffect(() => {
    if (draggingHandle == null) return
    dragLayoutRef.current = { ...layoutPercents }
    const handleIndex = draggingHandle
    const isTwoPanels = centerPanelCollapsed
    const onMove = (e) => {
      const rect = groupContainerRef.current?.getBoundingClientRect?.()
      if (!rect?.width) return
      const x = (e.clientX - rect.left) / rect.width * 100
      const { chat, center, right } = dragLayoutRef.current
      if (isTwoPanels) {
        const newChat = Math.max(10, Math.min(70, x))
        const newRight = 100 - newChat
        dragLayoutRef.current = { chat: newChat, center: 0, right: newRight }
        setLayoutPercents(dragLayoutRef.current)
      } else if (handleIndex === 0) {
        const newChat = Math.max(10, Math.min(70, x))
        const newCenter = Math.max(0, Math.min(60, center + chat - newChat))
        const newRight = 100 - newChat - newCenter
        dragLayoutRef.current = { chat: newChat, center: newCenter, right: newRight }
        setLayoutPercents(dragLayoutRef.current)
      } else {
        const chatPlusCenter = Math.max(chat, Math.min(90, x))
        const newCenter = Math.max(0, Math.min(60, chatPlusCenter - chat))
        const newRight = 100 - chat - newCenter
        dragLayoutRef.current = { chat, center: newCenter, right: newRight }
        setLayoutPercents(dragLayoutRef.current)
      }
    }
    const onUp = () => {
      setDraggingHandle(null)
      localStorage.setItem('panel-layout', JSON.stringify(dragLayoutRef.current))
    }
    document.addEventListener('pointermove', onMove)
    document.addEventListener('pointerup', onUp)
    document.addEventListener('pointercancel', onUp)
    return () => {
      document.removeEventListener('pointermove', onMove)
      document.removeEventListener('pointerup', onUp)
      document.removeEventListener('pointercancel', onUp)
    }
  }, [draggingHandle, centerPanelCollapsed])

  const novncPort = projectPorts?.['6080']
  
  // Find first available dev server port (priority order)
  const getPreviewPort = () => {
    if (!projectPorts || Object.keys(projectPorts).length === 0) return null
    
    // Priority order: Vite (5173), React (3000), Vue (8080), Next.js (3000), others
    const priorityPorts = ['5173', '3000', '8080', '5000', '8000', '4000']
    
    // First try priority ports
    for (const port of priorityPorts) {
      if (projectPorts[port]) {
        return projectPorts[port]
      }
    }
    
    // If no priority port found, return first available port (except noVNC port 6080)
    for (const [port, hostPort] of Object.entries(projectPorts)) {
      if (port !== '6080' && hostPort) {
        return hostPort
      }
    }
    
    return null
  }
  
  const previewPort = getPreviewPort()

  // Auto-switch to preview when a dev server port appears
  const prevPreviewPort = useRef(null)
  useEffect(() => {
    const ports = projectPorts || {}
    const preview = getPreviewPort()
    console.log('[Preview] projectPorts=', ports, 'previewPort=', preview, 'url=', preview ? `http://${window.location.hostname}:${preview}` : null)

    if (preview && preview !== prevPreviewPort.current) {
      prevPreviewPort.current = preview
      if (activeRightPanel !== 'preview') {
        console.log('[Preview] Auto-switching to preview panel, port:', preview)
        setActiveRightPanel('preview')
      }
    }
  }, [projectPorts, previewPort])

  const handleFileSelect = (filepath) => {
    // Remove /workspace/ prefix if present
    const relativePath = filepath.startsWith('/workspace/') 
      ? filepath.slice('/workspace/'.length)
      : filepath
    setSelectedFile(relativePath)
    setActiveRightPanel('editor')
  }

  const handleFileSave = (filepath) => {
    // Refresh file tree after save
    console.log('File saved:', filepath)
  }

  const { user, logout } = useStore()

  return (
    <div className="h-screen flex flex-col bg-dark-900">
      {/* Один раз определяем SVG-фильтр неона для иконок панели */}
      <svg width="0" height="0" className="absolute" aria-hidden="true">
        <defs>
          <filter id="neon-nav-filter" x="-50%" y="-50%" width="200%" height="200%">
            <feGaussianBlur in="SourceGraphic" stdDeviation="1" result="blur1" />
            <feGaussianBlur in="SourceGraphic" stdDeviation="2" result="blur2" />
            <feMerge>
              <feMergeNode in="blur2" />
              <feMergeNode in="blur1" />
              <feMergeNode in="SourceGraphic" />
            </feMerge>
          </filter>
        </defs>
      </svg>
      {/* Top bar */}
      <div className="flex items-center gap-4 px-4 py-2 bg-dark-800 border-b border-dark-500">
        <div className="text-sm font-semibold bg-gradient-to-r from-blue-400 to-purple-400 bg-clip-text text-transparent">
          AI Agent Platform
        </div>
        <div className="text-xs text-gray-600 font-mono">
          project: {projectId}
        </div>
        <div className="ml-auto flex items-center gap-3">
          {/* Center panel switcher (Файлы / Активность / Диалог) + свернуть/развернуть */}
          <div className="flex items-center gap-1">
            {centerPanelCollapsed ? (
              <button
                onClick={toggleCenterPanel}
                className="px-3 py-1 rounded-md text-xs font-medium flex items-center gap-1.5 bg-dark-600 hover:bg-dark-500 text-gray-400 hover:text-white border border-dark-500 transition-all"
                title="Развернуть панель (Файлы, Активность, Диалог)"
              >
                <span className="text-sm">▶</span>
                Развернуть
              </button>
            ) : (
              <>
                <div className="flex bg-dark-700 rounded-lg p-0.5 gap-0.5">
                  <button
                    onClick={() => setActiveCenterPanel('files')}
                    className={`px-3 py-1 rounded-md text-xs font-medium transition-all flex items-center ${
                      activeCenterPanel === 'files'
                        ? 'bg-blue-600 text-white'
                        : 'text-gray-500 hover:text-gray-300'
                    }`}
                  >
                    <NeonIcon name="files" active={activeCenterPanel === 'files'} />
                    Файлы
                  </button>
                  <button
                    onClick={() => setActiveCenterPanel('activity')}
                    className={`px-3 py-1 rounded-md text-xs font-medium transition-all flex items-center ${
                      activeCenterPanel === 'activity'
                        ? 'bg-blue-600 text-white'
                        : 'text-gray-500 hover:text-gray-300'
                    }`}
                  >
                    <NeonIcon name="activity" active={activeCenterPanel === 'activity'} />
                    Активность
                  </button>
                  <button
                    onClick={() => setActiveCenterPanel('dialog')}
                    className={`px-3 py-1 rounded-md text-xs font-medium transition-all flex items-center ${
                      activeCenterPanel === 'dialog'
                        ? 'bg-blue-600 text-white'
                        : 'text-gray-500 hover:text-gray-300'
                    }`}
                    title="Лог процесса в реальном времени"
                  >
                    <NeonIcon name="dialog" active={activeCenterPanel === 'dialog'} />
                    Диалог
                  </button>
                </div>
                <button
                  onClick={toggleCenterPanel}
                  className="p-1 rounded text-gray-500 hover:text-white hover:bg-dark-600 transition-all"
                  title="Свернуть панель влево (больше места чату и редактору)"
                >
                  <span className="text-xs">◀</span>
                </button>
              </>
            )}
          </div>
          {/* Right panel switcher */}
          <div className="flex bg-dark-700 rounded-lg p-0.5 gap-0.5">
            <button
              onClick={() => setActiveRightPanel('editor')}
              className={`px-3 py-1 rounded-md text-xs font-medium transition-all flex items-center ${
                activeRightPanel === 'editor'
                  ? 'bg-blue-600 text-white'
                  : 'text-gray-500 hover:text-gray-300'
              }`}
            >
              <NeonIcon name="editor" active={activeRightPanel === 'editor'} />
              Редактор
            </button>
            <button
              onClick={() => setActiveRightPanel('terminal')}
              className={`px-3 py-1 rounded-md text-xs font-medium transition-all flex items-center ${
                activeRightPanel === 'terminal'
                  ? 'bg-blue-600 text-white'
                  : 'text-gray-500 hover:text-gray-300'
              }`}
            >
              <NeonIcon name="terminal" active={activeRightPanel === 'terminal'} />
              Терминал
            </button>
            <button
              onClick={() => setActiveRightPanel('preview')}
              className={`px-3 py-1 rounded-md text-xs font-medium transition-all flex items-center ${
                activeRightPanel === 'preview'
                  ? 'bg-blue-600 text-white'
                  : 'text-gray-500 hover:text-gray-300'
              }`}
            >
              <NeonIcon name="preview" active={activeRightPanel === 'preview'} />
              Превью
            </button>
            <button
              onClick={() => setActiveRightPanel('browser')}
              className={`px-3 py-1 rounded-md text-xs font-medium transition-all flex items-center ${
                activeRightPanel === 'browser'
                  ? 'bg-blue-600 text-white'
                  : 'text-gray-500 hover:text-gray-300'
              }`}
            >
              <NeonIcon name="browser" active={activeRightPanel === 'browser'} />
              Браузер
            </button>
            <button
              onClick={() => setActiveRightPanel('logs')}
              className={`px-3 py-1 rounded-md text-xs font-medium transition-all flex items-center ${
                activeRightPanel === 'logs'
                  ? 'bg-blue-600 text-white'
                  : 'text-gray-500 hover:text-gray-300'
              }`}
            >
              Логи
            </button>
          </div>
          <a
            href="/neon-icons-demo.html"
            target="_blank"
            rel="noopener noreferrer"
            className="px-3 py-1 text-xs text-gray-400 hover:text-white rounded hover:bg-dark-600 transition-colors border border-dark-500 hover:border-dark-400 flex items-center"
            title="Открыть демо неоновых иконок в новой вкладке"
          >
            <NeonIcon name="neon" active={false} />
            Неон
          </a>
          {/* User info and logout */}
          {user && (
            <div className="flex items-center gap-2 pl-3 border-l border-dark-500">
              <span className="text-xs text-gray-400">{user.username}</span>
              <button
                onClick={logout}
                className="px-2 py-1 text-xs bg-dark-700 hover:bg-dark-600 rounded text-gray-400 hover:text-gray-200 transition-colors"
                title="Выйти"
              >
                Выйти
              </button>
            </div>
          )}
        </div>
      </div>

      {/* Main workspace: своя flex-разметка по layoutPercents, чтобы ресайз работал без setLayout библиотеки */}
      <div ref={groupContainerRef} className="flex-1 min-h-0 overflow-hidden flex flex-col relative" style={{ width: '100%' }}>
        <div className="flex flex-1 min-h-0 w-full" style={{ overflow: 'hidden' }}>
          {/* Чат */}
          <div
            className="flex flex-col flex-shrink-0 h-full overflow-hidden border-r border-dark-500"
            style={{
              flex: `0 0 ${layoutPercents.chat ?? 25}%`,
              minWidth: '10%',
              maxWidth: '70%',
            }}
          >
            <Chat />
          </div>

          {!centerPanelCollapsed && (
            <>
              {/* Центр: Файлы / Активность / Диалог */}
              <div
                className="flex flex-col flex-shrink-0 h-full overflow-hidden border-r border-dark-500 min-w-0"
                style={{
                  flex: `0 0 ${layoutPercents.center ?? 20}%`,
                  minWidth: 0,
                  maxWidth: '60%',
                }}
              >
                {activeCenterPanel === 'files' && <FileTree onFileSelect={handleFileSelect} />}
                {activeCenterPanel === 'activity' && <AgentActivity />}
                {activeCenterPanel === 'dialog' && <ProcessDialog />}
              </div>
            </>
          )}

          {/* Право: Редактор / Терминал / Превью / Браузер */}
          <div
            className="flex flex-col flex-shrink-0 min-w-0 h-full overflow-hidden"
            style={{
              flex: `0 0 ${layoutPercents.right ?? (centerPanelCollapsed ? 75 : 55)}%`,
              minWidth: '10%',
              maxWidth: '85%',
            }}
          >
            {activeRightPanel === 'editor' ? (
              <CodeEditor filepath={selectedFile} onSave={handleFileSave} />
            ) : activeRightPanel === 'terminal' ? (
              <Suspense fallback={<div className="flex items-center justify-center h-full text-gray-500">Загрузка терминала...</div>}>
                <Terminal />
              </Suspense>
            ) : activeRightPanel === 'preview' ? (
              <BrowserPanel port={previewPort} title="Превью проекта" icon="👁️" allPorts={projectPorts} />
            ) : activeRightPanel === 'logs' ? (
              <ContainerLogsPanel projectId={projectId} />
            ) : (
              <NoVNCPanel port={novncPort} projectId={projectId} />
            )}
          </div>
        </div>
        {/* Ручки ресайза: при перетаскивании меняется layoutPercents → колонки пересчитываются */}
        <div className="absolute inset-0 pointer-events-none" aria-hidden>
          <div
            className="absolute top-0 bottom-0 w-4 -ml-2 pointer-events-auto cursor-col-resize z-20 hover:bg-blue-500/20"
            style={{ left: `${layoutPercents.chat ?? 25}%` }}
            onPointerDown={(e) => onResizeHandlePointerDown(e, 0)}
            role="separator"
            title="Перетащите для изменения ширины"
          />
          {!centerPanelCollapsed && (
            <div
              className="absolute top-0 bottom-0 w-4 -ml-2 pointer-events-auto cursor-col-resize z-20 hover:bg-blue-500/20"
              style={{ left: `${(layoutPercents.chat ?? 25) + (layoutPercents.center ?? 20)}%` }}
              onPointerDown={(e) => onResizeHandlePointerDown(e, 1)}
              role="separator"
              title="Перетащите для изменения ширины"
            />
          )}
        </div>
      </div>
    </div>
  )
}

export default function App() {
  const { projectId, isAuthenticated, checkAuth, user } = useStore()
  const [projectReady, setProjectReady] = useState(false)
  const [authMode, setAuthMode] = useState('login') // 'login' | 'register'
  const [authChecked, setAuthChecked] = useState(false)

  // Check authentication on mount
  useEffect(() => {
    const initAuth = async () => {
      if (isAuthenticated) {
        await checkAuth()
      }
      setAuthChecked(true)
    }
    initAuth()
  }, [])

  // Error boundary
  try {
    // Show loading while checking auth
    if (!authChecked) {
      return (
        <div className="min-h-screen bg-dark-900 flex items-center justify-center">
          <div className="text-gray-400">Загрузка...</div>
        </div>
      )
    }

    // Show auth screen if not authenticated
    if (!isAuthenticated) {
      if (authMode === 'login') {
        return <Login onSwitchToRegister={() => setAuthMode('register')} />
      } else {
        return <Register onSwitchToLogin={() => setAuthMode('login')} />
      }
    }

    // Show project creation if no project
    if (!projectId || !projectReady) {
      return <CreateProjectScreen onCreated={() => setProjectReady(true)} />
    }

    // Show workspace
    return <WorkspaceScreen />
  } catch (error) {
    console.error('App error:', error)
    return (
      <div className="min-h-screen bg-dark-900 flex items-center justify-center p-4">
        <div className="text-red-400 text-center">
          <div className="text-2xl mb-2">❌ Ошибка загрузки</div>
          <div className="text-sm text-gray-500">{error.message}</div>
          <button
            onClick={() => window.location.reload()}
            className="mt-4 px-4 py-2 bg-blue-600 hover:bg-blue-500 rounded"
          >
            Перезагрузить
          </button>
        </div>
      </div>
    )
  }
}
