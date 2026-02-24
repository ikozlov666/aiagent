import { useEffect, useRef } from 'react'
import { useStore } from '../../stores/useStore'

function formatTime(ts) {
  if (!ts) return ''
  const d = new Date(ts * 1000)
  return d.toLocaleTimeString('ru-RU', { hour: '2-digit', minute: '2-digit', second: '2-digit' })
}

/** Only DeepSeek model messages and final responses */
const DIALOG_TYPES = new Set(['llm_text', 'response'])

export default function ProcessDialog() {
  const { agentSteps, agentStatus } = useStore()
  const bottomRef = useRef(null)

  // Filter: only model text output
  const dialogSteps = agentSteps.filter(s => DIALOG_TYPES.has(s.type))

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: 'smooth' })
  }, [dialogSteps])

  const isWorking = agentStatus !== 'idle' && agentStatus !== 'done'

  return (
    <div className="flex flex-col h-full bg-dark-800">
      <div className="flex items-center gap-3 px-4 py-3 border-b border-dark-500">
        <div className="text-lg font-semibold">💬 Диалог процесса</div>
        {isWorking && (
          <div className="ml-auto flex items-center gap-2">
            <div className="w-2 h-2 rounded-full bg-amber-400 animate-pulse-dot" />
            <span className="text-xs text-amber-400">
              {agentStatus === 'thinking' ? 'Думает' : 'Работает'}
            </span>
          </div>
        )}
        <span className="text-xs text-gray-500 ml-auto">
          {dialogSteps.length} сообщ.
        </span>
      </div>

      <div className="flex-1 overflow-y-auto p-3 space-y-2">
        {dialogSteps.length === 0 && (
          <div className="flex flex-col items-center justify-center h-full text-gray-600 space-y-2">
            <div className="text-3xl">💬</div>
            <div className="text-sm">Здесь будет диалог с моделью</div>
            <div className="text-xs text-gray-700">Отправьте задачу в чат — ответы DeepSeek появятся здесь</div>
          </div>
        )}

        {dialogSteps.map((step, i) => {
          const isResponse = step.type === 'response'
          const borderClass = isResponse
            ? 'border-green-500 bg-green-500/5'
            : 'border-amber-500/80 bg-amber-500/10'
          const label = isResponse ? '✅ Ответ' : '🤖 DeepSeek'
          const labelColor = isResponse ? 'text-green-400' : 'text-amber-400'

          return (
            <div
              key={i}
              className={`rounded border-l-4 overflow-hidden ${borderClass}`}
            >
              <div className="flex items-center gap-2 py-1 px-2 bg-dark-800/80 border-b border-dark-600">
                <span className="text-gray-500 font-mono text-xs">{formatTime(step.timestamp)}</span>
                <span className={`font-medium text-xs ${labelColor}`}>{label}</span>
              </div>
              <div className="px-3 py-2">
                <pre className="text-gray-200 whitespace-pre-wrap break-words font-sans text-sm leading-relaxed">
                  {step.content}
                </pre>
              </div>
            </div>
          )
        })}

        <div ref={bottomRef} />
      </div>
    </div>
  )
}
