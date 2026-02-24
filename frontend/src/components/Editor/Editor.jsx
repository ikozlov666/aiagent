import { useState, useEffect, useRef } from 'react'
import MonacoEditor from '@monaco-editor/react'
import { useStore } from '../../stores/useStore'

const IMAGE_EXT = new Set(['png', 'jpg', 'jpeg', 'gif', 'webp', 'bmp', 'svg', 'ico'])
const VIDEO_EXT = new Set(['mp4', 'webm', 'mov', 'avi', 'mkv'])
const AUDIO_EXT = new Set(['mp3', 'wav', 'ogg', 'm4a', 'flac'])
const PDF_EXT = new Set(['pdf'])
const TEXT_EXT = new Set([
  'js', 'jsx', 'ts', 'tsx', 'py', 'html', 'css', 'scss', 'sass', 'json', 'yaml', 'yml',
  'md', 'txt', 'sh', 'bash', 'xml', 'sql', 'toml', 'ini', 'env', 'csv', 'dockerfile'
])

function getExt(path) {
  return (path?.split('.').pop() || '').toLowerCase()
}

function getFileKind(path) {
  const ext = getExt(path)
  if (IMAGE_EXT.has(ext)) return 'image'
  if (VIDEO_EXT.has(ext)) return 'video'
  if (AUDIO_EXT.has(ext)) return 'audio'
  if (PDF_EXT.has(ext)) return 'pdf'
  if (TEXT_EXT.has(ext) || !ext) return 'text'
  return 'binary'
}

export default function CodeEditor({ filepath, onSave }) {
  const { projectId } = useStore()
  const [content, setContent] = useState('')
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState(null)
  const [saving, setSaving] = useState(false)
  const editorRef = useRef(null)

  const fileKind = getFileKind(filepath)
  const isTextMode = fileKind === 'text'
  const rawFileUrl = projectId && filepath
    ? `/api/projects/${projectId}/files/raw?filepath=${encodeURIComponent(filepath)}`
    : ''

  const loadFile = async () => {
    if (!projectId || !filepath || !isTextMode) return

    setLoading(true)
    setError(null)
    try {
      const res = await fetch(`/api/projects/${projectId}/files/read?filepath=${encodeURIComponent(filepath)}`)
      if (!res.ok) {
        throw new Error(`Failed to load file: ${res.status}`)
      }
      const data = await res.json()
      setContent(data.content || '')
    } catch (e) {
      setError(e.message)
      console.error('Failed to load file:', e)
    } finally {
      setLoading(false)
    }
  }

  const saveFile = async () => {
    if (!projectId || !filepath || saving || !isTextMode) return

    setSaving(true)
    try {
      const res = await fetch(`/api/projects/${projectId}/files/write`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          filepath: filepath,
          content: content
        })
      })
      if (!res.ok) {
        throw new Error(`Failed to save file: ${res.status}`)
      }
      onSave?.(filepath)
    } catch (e) {
      setError(e.message)
      console.error('Failed to save file:', e)
    } finally {
      setSaving(false)
    }
  }

  useEffect(() => {
    if (!filepath) {
      setContent('')
      setError(null)
      return
    }

    if (isTextMode) {
      loadFile()
    } else {
      setContent('')
      setError(null)
      setLoading(false)
    }
  }, [projectId, filepath, isTextMode])

  const handleEditorDidMount = (editor) => {
    editorRef.current = editor
    editor.updateOptions({
      fontSize: 14,
      minimap: { enabled: true },
      wordWrap: 'on',
      automaticLayout: true,
    })
  }

  const getLanguage = (filename) => {
    const ext = filename.split('.').pop()?.toLowerCase()
    const langMap = {
      'js': 'javascript',
      'jsx': 'javascript',
      'ts': 'typescript',
      'tsx': 'typescript',
      'py': 'python',
      'html': 'html',
      'css': 'css',
      'scss': 'scss',
      'json': 'json',
      'yaml': 'yaml',
      'yml': 'yaml',
      'md': 'markdown',
      'sh': 'shell',
      'bash': 'shell',
      'dockerfile': 'dockerfile',
      'xml': 'xml',
    }
    return langMap[ext] || 'plaintext'
  }

  if (!filepath) {
    return (
      <div className="flex items-center justify-center h-full text-gray-500 text-sm">
        Выберите файл для просмотра
      </div>
    )
  }

  if (loading) {
    return (
      <div className="flex items-center justify-center h-full">
        <div className="text-gray-500 text-sm">Загрузка файла...</div>
      </div>
    )
  }

  if (error) {
    return (
      <div className="flex flex-col items-center justify-center h-full text-red-400 text-sm p-4">
        <div>❌ Ошибка загрузки</div>
        <div className="text-xs text-gray-500 mt-1">{error}</div>
        {isTextMode && (
          <button
            onClick={loadFile}
            className="mt-2 px-3 py-1 bg-dark-700 hover:bg-dark-600 rounded text-xs"
          >
            Повторить
          </button>
        )}
      </div>
    )
  }

  return (
    <div className="flex flex-col h-full bg-dark-900">
      <div className="flex items-center gap-2 px-3 py-2 border-b border-dark-500 bg-dark-800">
        <div className="text-sm font-medium text-gray-300 truncate flex-1">
          {filepath}
        </div>
        {isTextMode ? (
          <button
            onClick={saveFile}
            disabled={saving}
            className="px-3 py-1 bg-blue-600 hover:bg-blue-500 rounded text-xs font-medium
              transition-all disabled:opacity-50 disabled:cursor-not-allowed"
          >
            {saving ? 'Сохранение...' : '💾 Сохранить'}
          </button>
        ) : (
          <a
            href={rawFileUrl}
            target="_blank"
            rel="noreferrer"
            className="px-3 py-1 bg-dark-700 hover:bg-dark-600 rounded text-xs font-medium text-gray-200"
          >
            Открыть файл
          </a>
        )}
      </div>

      <div className="flex-1 overflow-auto p-3">
        {fileKind === 'image' && (
          <div className="h-full w-full flex items-center justify-center">
            <img src={rawFileUrl} alt={filepath} className="max-w-full max-h-full object-contain rounded border border-dark-500" />
          </div>
        )}

        {fileKind === 'video' && (
          <div className="h-full w-full flex items-center justify-center">
            <video src={rawFileUrl} controls className="max-w-full max-h-full rounded border border-dark-500" />
          </div>
        )}

        {fileKind === 'audio' && (
          <div className="h-full w-full flex items-center justify-center">
            <audio src={rawFileUrl} controls className="w-full max-w-2xl" />
          </div>
        )}

        {fileKind === 'pdf' && (
          <iframe src={rawFileUrl} title={filepath} className="w-full h-full min-h-[300px] border border-dark-500 rounded" />
        )}

        {fileKind === 'binary' && (
          <div className="h-full w-full flex flex-col items-center justify-center text-gray-300 gap-3">
            <div className="text-sm">Для этого типа файла встроенный предпросмотр пока недоступен.</div>
            <a href={rawFileUrl} target="_blank" rel="noreferrer" className="px-3 py-1.5 bg-blue-600 hover:bg-blue-500 rounded text-xs font-medium">
              Скачать / Открыть
            </a>
          </div>
        )}

        {isTextMode && (
          <MonacoEditor
            height="100%"
            language={getLanguage(filepath)}
            value={content}
            onChange={(value) => setContent(value || '')}
            onMount={handleEditorDidMount}
            theme="vs-dark"
            options={{
              fontSize: 14,
              minimap: { enabled: true },
              wordWrap: 'on',
              automaticLayout: true,
              scrollBeyondLastLine: false,
              renderWhitespace: 'selection',
              tabSize: 2,
              insertSpaces: true,
            }}
          />
        )}
      </div>
    </div>
  )
}
