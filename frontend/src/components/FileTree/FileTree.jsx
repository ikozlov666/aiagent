import { useState, useEffect, useRef } from 'react'
import { useStore } from '../../stores/useStore'

function FileTreeNode({ node, level = 0, onFileSelect }) {
  const [expanded, setExpanded] = useState(level < 2) // Auto-expand first 2 levels
  const isDir = node.type === 'dir'
  const hasChildren = isDir && node.children && node.children.length > 0

  const handleClick = () => {
    if (isDir) {
      setExpanded(!expanded)
    } else {
      onFileSelect?.(node.path)
    }
  }

  const icon = isDir 
    ? (expanded ? '📂' : '📁')
    : getFileIcon(node.name)

  return (
    <div>
      <div
        onClick={handleClick}
        className={`flex items-center gap-1.5 px-2 py-1 text-sm cursor-pointer
          hover:bg-dark-600 rounded transition-colors
          ${!isDir ? 'text-gray-300 hover:text-white' : 'text-blue-300'}
        `}
        style={{ paddingLeft: `${level * 16 + 8}px` }}
      >
        <span className="text-xs">{icon}</span>
        <span className="truncate flex-1">{node.name}</span>
      </div>
      
      {isDir && expanded && hasChildren && (
        <div>
          {node.children.map((child, idx) => (
            <FileTreeNode
              key={child.path || idx}
              node={child}
              level={level + 1}
              onFileSelect={onFileSelect}
            />
          ))}
        </div>
      )}
    </div>
  )
}

function getFileIcon(filename) {
  const ext = filename.split('.').pop()?.toLowerCase()
  const icons = {
    'js': '📜', 'jsx': '⚛️', 'ts': '📘', 'tsx': '⚛️',
    'py': '🐍', 'pyc': '🐍',
    'html': '🌐', 'css': '🎨', 'scss': '🎨', 'sass': '🎨',
    'json': '📋', 'yaml': '📋', 'yml': '📋',
    'md': '📝', 'txt': '📄',
    'png': '🖼️', 'jpg': '🖼️', 'jpeg': '🖼️', 'gif': '🖼️', 'svg': '🖼️',
    'pdf': '📕',
    'zip': '📦', 'tar': '📦', 'gz': '📦',
    'git': '🔧', 'gitignore': '🔧',
    'lock': '🔒', 'package': '📦',
  }
  return icons[ext] || '📄'
}

const FILES_FETCH_TIMEOUT_MS = 15000 // не ждать дольше 15 сек
const FIRST_LOAD_RETRIES = 3       // при первом запуске повторять до 3 раз (сандбокс может ещё подниматься)
const RETRY_DELAY_MS = 2000

export default function FileTree({ onFileSelect }) {
  const { projectId } = useStore()
  const [tree, setTree] = useState(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState(null)
  const [slowHint, setSlowHint] = useState(false)
  const [retryCount, setRetryCount] = useState(0) // показываем «Повтор 2/3» при первом запуске
  const abortRef = useRef(null)

  const loadTree = async (retriesLeft = 0) => {
    if (!projectId) return true
    abortRef.current?.abort()

    setLoading(true)
    setError(null)
    setSlowHint(false)
    setRetryCount(FIRST_LOAD_RETRIES - retriesLeft)
    const slowTimer = setTimeout(() => setSlowHint(true), 5000)

    const controller = new AbortController()
    abortRef.current = controller
    const timeoutId = setTimeout(() => controller.abort(), FILES_FETCH_TIMEOUT_MS)

    let isRetrying = false
    try {
      const res = await fetch(`/api/projects/${projectId}/files?tree=true`, {
        signal: controller.signal,
      })
      if (!res.ok) {
        const errBody = await res.json().catch(() => ({}))
        throw new Error(errBody.detail || `Ошибка загрузки: ${res.status}`)
      }
      const data = await res.json()
      setTree(data.tree)
      return true
    } catch (e) {
      if (retriesLeft > 0) {
        isRetrying = true
        clearTimeout(slowTimer)
        clearTimeout(timeoutId)
        await new Promise((r) => setTimeout(r, RETRY_DELAY_MS))
        if (abortRef.current?.signal?.aborted) {
          setLoading(false)
          setSlowHint(false)
          return false
        }
        return loadTree(retriesLeft - 1)
      }
      if (e.name === 'AbortError') {
        setError('Превышено время ожидания. Сандбокс может быть занят — нажмите «Повторить».')
      } else {
        setError(e.message)
      }
      console.error('Failed to load file tree:', e)
      return false
    } finally {
      clearTimeout(slowTimer)
      clearTimeout(timeoutId)
      if (!isRetrying) {
        setLoading(false)
        setSlowHint(false)
      }
    }
  }

  useEffect(() => {
    if (!projectId) return

    let cancelled = false
    const load = async () => {
      if (cancelled) return
      await loadTree(FIRST_LOAD_RETRIES)
    }
    load()
    const interval = setInterval(() => {
      if (cancelled) return
      loadTree(0)
    }, 10000)
    return () => {
      cancelled = true
      clearInterval(interval)
    }
  }, [projectId])

  if (!projectId) {
    return (
      <div className="flex items-center justify-center h-full text-gray-500 text-sm">
        Создайте проект для просмотра файлов
      </div>
    )
  }

  if (loading && !tree) {
    return (
      <div className="flex flex-col items-center justify-center h-full gap-2 px-4">
        <div className="text-gray-500 text-sm">Загрузка файлов...</div>
        {retryCount > 0 && (
          <div className="text-xs text-amber-500/90 text-center">
            Повторная попытка {retryCount}/{FIRST_LOAD_RETRIES + 1}…
          </div>
        )}
        {slowHint && !retryCount && (
          <div className="text-xs text-amber-500/90 text-center">
            Долго загружается — сандбокс может быть занят. Ожидание до 15 сек.
          </div>
        )}
        <button
          type="button"
          onClick={() => { abortRef.current?.abort(); loadTree(0); }}
          className="text-xs text-gray-500 hover:text-gray-300 underline"
        >
          Отменить и повторить
        </button>
      </div>
    )
  }

  if (error) {
    return (
      <div className="flex flex-col items-center justify-center h-full text-red-400 text-sm p-4">
        <div>❌ Ошибка загрузки</div>
        <div className="text-xs text-gray-500 mt-1">{error}</div>
        <button
          onClick={() => loadTree(0)}
          className="mt-2 px-3 py-1 bg-dark-700 hover:bg-dark-600 rounded text-xs"
        >
          Повторить
        </button>
      </div>
    )
  }

  if (!tree || !tree.children || tree.children.length === 0) {
    return (
      <div className="flex items-center justify-center h-full text-gray-500 text-sm">
        Файлы не найдены
      </div>
    )
  }

  return (
    <div className="flex flex-col h-full bg-dark-800">
      {/* Header */}
      <div className="flex items-center gap-2 px-3 py-2 border-b border-dark-500">
        <div className="text-sm font-semibold">📁 Файлы</div>
        <button
          onClick={loadTree}
          className="ml-auto text-xs text-gray-500 hover:text-gray-300 transition-colors"
          title="Обновить"
        >
          🔄
        </button>
      </div>

      {/* Tree */}
      <div className="flex-1 overflow-y-auto p-2">
        {tree.children.map((child, idx) => (
          <FileTreeNode
            key={child.path || idx}
            node={child}
            level={0}
            onFileSelect={onFileSelect}
          />
        ))}
      </div>
    </div>
  )
}
