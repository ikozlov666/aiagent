import { useState, useEffect, useRef } from 'react'
import MonacoEditor from '@monaco-editor/react'
import { useStore } from '../../stores/useStore'

export default function CodeEditor({ filepath, onSave }) {
  const { projectId } = useStore()
  const [content, setContent] = useState('')
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState(null)
  const [saving, setSaving] = useState(false)
  const editorRef = useRef(null)

  const loadFile = async () => {
    if (!projectId || !filepath) return
    
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
    if (!projectId || !filepath || saving) return
    
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
    if (filepath) {
      loadFile()
    } else {
      setContent('')
      setError(null)
    }
  }, [projectId, filepath])

  const handleEditorDidMount = (editor, monaco) => {
    editorRef.current = editor
    // Configure editor
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
        <button
          onClick={loadFile}
          className="mt-2 px-3 py-1 bg-dark-700 hover:bg-dark-600 rounded text-xs"
        >
          Повторить
        </button>
      </div>
    )
  }

  return (
    <div className="flex flex-col h-full bg-dark-900">
      {/* Header */}
      <div className="flex items-center gap-2 px-3 py-2 border-b border-dark-500 bg-dark-800">
        <div className="text-sm font-medium text-gray-300 truncate flex-1">
          {filepath}
        </div>
        <button
          onClick={saveFile}
          disabled={saving}
          className="px-3 py-1 bg-blue-600 hover:bg-blue-500 rounded text-xs font-medium
            transition-all disabled:opacity-50 disabled:cursor-not-allowed"
        >
          {saving ? 'Сохранение...' : '💾 Сохранить'}
        </button>
      </div>

      {/* Editor */}
      <div className="flex-1">
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
      </div>
    </div>
  )
}
