import { useState, useRef, useEffect } from 'react'
import { useStore } from '../../stores/useStore'

function genId() {
  return `f${Date.now()}_${Math.random().toString(36).slice(2, 8)}`
}

export default function Chat() {
  const [input, setInput] = useState('')
  const [images, setImages] = useState([])
  const [attachedFiles, setAttachedFiles] = useState([]) // текст из буфера как файлы: { id, filename, content }
  const messagesEndRef = useRef(null)
  const inputRef = useRef(null)
  const fileInputRef = useRef(null)
  const wasFocusedRef = useRef(false)
  const { messages, sendMessage, agentStatus, wsConnected, streamingContent, liveAssistantContent } = useStore()

  // Auto-scroll to bottom when messages or streaming content change
  useEffect(() => {
    messagesEndRef.current?.scrollIntoView({ behavior: 'smooth' })
  }, [messages, streamingContent, liveAssistantContent])

  // Preserve focus on input when component re-renders
  useEffect(() => {
    if (wasFocusedRef.current && inputRef.current && document.activeElement !== inputRef.current) {
      inputRef.current.focus()
      // Restore cursor position if possible
      const len = inputRef.current.value.length
      inputRef.current.setSelectionRange(len, len)
    }
  })

  // Track focus state
  const handleInputFocus = () => {
    wasFocusedRef.current = true
  }

  const handleInputBlur = () => {
    // Don't clear wasFocusedRef immediately - wait a bit in case it's just a re-render
    setTimeout(() => {
      if (document.activeElement !== inputRef.current) {
        wasFocusedRef.current = false
      }
    }, 100)
  }

  const handleFileSelect = (e) => {
    const files = Array.from(e.target.files)
    if (files.length === 0) return

    files.forEach(file => {
      if (!file.type.startsWith('image/')) {
        alert('Пожалуйста, выберите изображение')
        return
      }

      const reader = new FileReader()
      reader.onload = (event) => {
        const base64 = event.target.result.split(',')[1] // Remove data:image/...;base64, prefix
        setImages(prev => [...prev, { base64, file, preview: URL.createObjectURL(file) }])
      }
      reader.readAsDataURL(file)
    })

    // Reset input
    if (fileInputRef.current) {
      fileInputRef.current.value = ''
    }
  }

  // Вставка из буфера: картинка → в images, текст → как прикреплённый файл
  const handlePaste = (e) => {
    const items = e.clipboardData?.items
    if (!items) return
    for (const item of items) {
      if (item.type.startsWith('image/')) {
        e.preventDefault()
        const file = item.getAsFile()
        if (file) {
          const reader = new FileReader()
          reader.onload = (ev) => {
            const base64 = ev.target.result.split(',')[1]
            setImages(prev => [...prev, { base64, file, preview: URL.createObjectURL(file) }])
          }
          reader.readAsDataURL(file)
        }
        return
      }
    }
    const text = e.clipboardData?.getData('text')
    if (text && text.trim().length > 0) {
      e.preventDefault()
      const ext = /^#+\s/m.test(text) ? 'md' : /^\{[\s\S]*\}/m.test(text.trim()) ? 'json' : 'txt'
      const filename = `pasted_${attachedFiles.length + 1}.${ext}`
      setAttachedFiles(prev => [...prev, { id: genId(), filename, content: text }])
      return
    }
  }

  const removeAttachedFile = (id) => {
    setAttachedFiles(prev => prev.filter((f) => f.id !== id))
  }

  const removeImage = (index) => {
    setImages(prev => {
      const newImages = [...prev]
      if (newImages[index].preview) {
        URL.revokeObjectURL(newImages[index].preview)
      }
      newImages.splice(index, 1)
      return newImages
    })
  }

  const handleSend = () => {
    const text = input.trim()
    const hasAttachments = images.length > 0 || attachedFiles.length > 0
    if ((!text && !hasAttachments) || !wsConnected) return

    const imageData = images.map(img => img.base64)
    const filesToSend = attachedFiles.length > 0 ? attachedFiles.map((f) => ({ filename: f.filename, content: f.content })) : null
    sendMessage(
      text || (images.length > 0 ? 'Проанализируй изображение и создай код' : 'Обработай прикреплённые файлы'),
      imageData.length > 0 ? imageData : null,
      filesToSend
    )
    setInput('')
    setImages([])
    setAttachedFiles([])
    images.forEach(img => {
      if (img.preview) URL.revokeObjectURL(img.preview)
    })
  }

  const handleKeyDown = (e) => {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault()
      handleSend()
    }
  }

  const isWorking = agentStatus === 'thinking' || agentStatus === 'working'
  const { stopAgent } = useStore()

  return (
    <div className="flex flex-col h-full bg-dark-800">
      {/* Header */}
      <div className="flex items-center gap-3 px-4 py-3 border-b border-dark-500">
        <div className="text-lg font-semibold">💬 Чат</div>
        <div className="ml-auto flex items-center gap-2">
          {isWorking && (
            <button
              onClick={stopAgent}
              className="px-3 py-1.5 bg-red-600 hover:bg-red-500 rounded-lg text-white text-xs
                font-medium transition-all active:scale-95"
            >
              ⏹️ Остановить
            </button>
          )}
          <div className="flex items-center gap-2 text-xs">
            <div className={`w-2 h-2 rounded-full ${wsConnected ? 'bg-green-500' : 'bg-red-500'}`} />
            <span className="text-gray-500">{wsConnected ? 'подключен' : 'отключен'}</span>
          </div>
        </div>
      </div>

      {/* Messages */}
      <div className="flex-1 overflow-y-auto p-4 space-y-4">
        {messages.length === 0 && (
          <div className="flex flex-col items-center justify-center h-full text-gray-500 space-y-3">
            <div
              className="text-5xl inline-block"
              style={{
                filter: 'drop-shadow(0 0 8px rgba(255,255,255,0.6)) drop-shadow(0 0 16px rgba(200,220,255,0.4)) drop-shadow(0 0 24px rgba(180,200,255,0.25))',
              }}
            >
              🤖
            </div>
            <div className="text-lg font-medium">AI Agent готов к работе</div>
            <div className="text-sm text-center max-w-md">
              Опишите что хотите создать — агент напишет код, установит зависимости и запустит проект в изолированной среде.
            </div>
            <div className="flex flex-wrap gap-2 mt-4 justify-center">
              {[
                'Создай лендинг для кофейни',
                'Сделай TODO-приложение на React',
                'Напиши API на Express + SQLite',
              ].map((suggestion) => (
                <button
                  key={suggestion}
                  onClick={() => { setInput(suggestion); inputRef.current?.focus() }}
                  className="px-3 py-1.5 rounded-lg bg-dark-600 hover:bg-dark-500 text-gray-400 
                    hover:text-gray-200 text-xs transition-all border border-dark-500 hover:border-blue-500/30"
                >
                  {suggestion}
                </button>
              ))}
            </div>
          </div>
        )}

        {messages.map((msg) => (
          <div
            key={msg.id}
            className={`animate-fade-in-up ${
              msg.role === 'user' ? 'flex justify-end' : ''
            }`}
          >
            <div
              className={`max-w-[85%] rounded-2xl px-4 py-3 ${
                msg.role === 'user'
                  ? 'bg-blue-600 text-white rounded-br-md'
                  : msg.role === 'error'
                  ? 'bg-red-500/10 border border-red-500/30 text-red-300'
                  : 'bg-dark-600 text-gray-200 rounded-bl-md'
              }`}
            >
              {msg.images && msg.images.length > 0 && (
                <div className="flex gap-2 mb-2 flex-wrap">
                  {msg.images.map((img, idx) => (
                    <img
                      key={idx}
                      src={typeof img === 'string' ? `data:image/png;base64,${img}` : `data:image/png;base64,${img.base64 || img}`}
                      alt={`Image ${idx + 1}`}
                      className="max-w-[200px] max-h-[200px] object-contain rounded border border-white/20"
                    />
                  ))}
                </div>
              )}
              {msg.attachedFiles && msg.attachedFiles.length > 0 && (
                <div className="flex flex-wrap gap-1 mb-2 text-xs opacity-90">
                  {msg.attachedFiles.map((f, idx) => (
                    <span key={idx} className="rounded bg-white/10 px-2 py-0.5">📄 {f.filename}</span>
                  ))}
                </div>
              )}
              {msg.role === 'assistant' && (
                <div className="text-xs text-blue-400 mb-1 font-medium flex items-center gap-1.5">
                  <span
                    className="inline-block"
                    style={{
                      filter: 'drop-shadow(0 0 4px rgba(255,255,255,0.5)) drop-shadow(0 0 8px rgba(200,220,255,0.3))',
                    }}
                  >
                    🤖
                  </span>
                  Агент
                </div>
              )}
              {msg.content && (
                <div className="text-sm whitespace-pre-wrap leading-relaxed">
                  {msg.content}
                </div>
              )}
            </div>
          </div>
        ))}

        {/* Streaming reply (simple-chat path) */}
        {streamingContent && (
          <div className="animate-fade-in-up flex justify-start">
            <div className="max-w-[85%] rounded-2xl rounded-bl-md px-4 py-3 bg-dark-600 text-gray-200">
              <div className="text-xs text-blue-400 mb-1 font-medium flex items-center gap-1.5">
                <span style={{ filter: 'drop-shadow(0 0 4px rgba(255,255,255,0.5))' }}>🤖</span>
                Агент
              </div>
              <div className="text-sm whitespace-pre-wrap leading-relaxed inline">
                {streamingContent}
                <span className="inline-block w-2 h-4 ml-0.5 bg-blue-400 animate-pulse align-middle" />
              </div>
            </div>
          </div>
        )}

        {/* Трансляция в чат: Думает / сообщение DeepSeek пока агент работает */}
        {liveAssistantContent && (
          <div className="animate-fade-in-up flex justify-start">
            <div className="max-w-[85%] rounded-2xl rounded-bl-md px-4 py-3 bg-dark-600 text-gray-200 border border-amber-500/30">
              <div className="text-xs text-amber-400 mb-1 font-medium flex items-center gap-1.5">
                <span style={{ filter: 'drop-shadow(0 0 4px rgba(255,255,255,0.5))' }}>🤖</span>
                Агент
                <span className="text-gray-500 font-normal">· в работе</span>
              </div>
              <div className="text-sm whitespace-pre-wrap leading-relaxed">
                {liveAssistantContent}
                <span className="inline-block w-2 h-4 ml-0.5 bg-amber-400 animate-pulse align-middle" />
              </div>
            </div>
          </div>
        )}

        {/* Typing indicator (when not streaming and no live content) */}
        {isWorking && !streamingContent && !liveAssistantContent && (
          <div className="animate-fade-in-up">
            <div className="bg-dark-600 rounded-2xl rounded-bl-md px-4 py-3 inline-block">
              <div className="flex items-center gap-1.5">
                <div className="w-2 h-2 rounded-full bg-blue-400 animate-pulse-dot" style={{ animationDelay: '0ms' }} />
                <div className="w-2 h-2 rounded-full bg-blue-400 animate-pulse-dot" style={{ animationDelay: '300ms' }} />
                <div className="w-2 h-2 rounded-full bg-blue-400 animate-pulse-dot" style={{ animationDelay: '600ms' }} />
                <span className="text-xs text-gray-500 ml-2">
                  {agentStatus === 'thinking' ? 'Думает...' : 'Выполняет...'}
                </span>
              </div>
            </div>
          </div>
        )}

        <div ref={messagesEndRef} />
      </div>

      {/* Input */}
      <div className="p-4 border-t border-dark-500">
        {(images.length > 0 || attachedFiles.length > 0) && (
          <div className="flex flex-wrap gap-2 mb-2">
            {images.map((img, idx) => (
              <span key={idx} className="inline-flex items-center gap-1 rounded-lg bg-dark-600 px-2 py-1 text-xs">
                <img src={img.preview} alt="" className="h-8 w-8 object-cover rounded" />
                <button type="button" onClick={() => removeImage(idx)} className="text-red-400 hover:text-red-300">×</button>
              </span>
            ))}
            {attachedFiles.map((f) => (
              <span key={f.id} className="inline-flex items-center gap-1 rounded-lg bg-dark-600 px-2 py-1 text-xs text-gray-300">
                📄 {f.filename}
                <button type="button" onClick={() => removeAttachedFile(f.id)} className="text-red-400 hover:text-red-300">×</button>
              </span>
            ))}
          </div>
        )}
        <div className="flex gap-2">
          <textarea
            ref={inputRef}
            value={input}
            onChange={(e) => setInput(e.target.value)}
            onKeyDown={handleKeyDown}
            onPaste={handlePaste}
            onFocus={handleInputFocus}
            onBlur={handleInputBlur}
            placeholder={wsConnected ? 'Опишите задачу или вставьте текст/картинку (Ctrl+V) — станет вложением' : 'Подключение...'}
            disabled={!wsConnected}
            rows={1}
            className="flex-1 bg-dark-700 border border-dark-500 rounded-xl px-4 py-3 text-sm
              text-gray-200 placeholder-gray-600 resize-none outline-none
              focus:border-blue-500/50 focus:ring-1 focus:ring-blue-500/20 transition-all
              disabled:opacity-50"
            style={{ minHeight: '44px', maxHeight: '120px' }}
            onInput={(e) => {
              e.target.style.height = '44px'
              e.target.style.height = Math.min(e.target.scrollHeight, 120) + 'px'
            }}
          />
          <button
            onClick={handleSend}
            disabled={(!input.trim() && images.length === 0 && attachedFiles.length === 0) || !wsConnected || isWorking}
            className="px-4 py-3 bg-blue-600 hover:bg-blue-500 rounded-xl text-white text-sm
              font-medium transition-all disabled:opacity-40 disabled:cursor-not-allowed
              active:scale-95"
          >
            ➤
          </button>
        </div>
      </div>
    </div>
  )
}
