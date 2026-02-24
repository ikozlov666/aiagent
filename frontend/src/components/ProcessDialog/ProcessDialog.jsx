import { useEffect, useRef } from 'react'
import { useStore } from '../../stores/useStore'

function formatTime(ts) {
  if (!ts) return ''
  const d = new Date(ts * 1000)
  return d.toLocaleTimeString('ru-RU', { hour: '2-digit', minute: '2-digit', second: '2-digit' })
}

/** Only DeepSeek model messages and final responses */
const DIALOG_TYPES = new Set(['llm_text', 'response'])
const ACTION_TYPES = new Set(['thinking', 'tool_call', 'tool_result'])

function renderToolSummary(step) {
  if (!step) return 'Ожидание действий…'

  if (step.type === 'thinking') return step.content || 'Планирую следующий шаг'

  if (step.type === 'tool_call') {
    const name = step.tool_name || 'tool'
    const args = step.tool_args || {}
    if (name === 'write_file' && args.filepath) {
      return `Создаю файл: ${args.filepath}`
    }
    if (name === 'write_files' && Array.isArray(args.files)) {
      const first = args.files[0]?.filepath
      return first
        ? `Создаю ${args.files.length} файлов (например, ${first})`
        : `Создаю ${args.files.length} файлов`
    }
    if (name === 'execute_command' && args.command) {
      return `Запускаю команду: ${String(args.command).slice(0, 70)}`
    }
    if ((name === 'read_file' || name === 'list_files') && (args.filepath || args.path)) {
      return `${name === 'read_file' ? 'Читаю файл' : 'Сканирую файлы'}: ${args.filepath || args.path}`
    }
    return `Выполняю: ${name}`
  }

  if (step.type === 'tool_result') {
    if (step.tool_result?.success) return 'Шаг выполнен успешно ✅'
    if (step.tool_result?.error) return `Ошибка шага: ${String(step.tool_result.error).slice(0, 90)}`
    return 'Получен результат шага'
  }

  return step.content || 'Выполняется действие…'
}

export default function ProcessDialog() {
  const { agentSteps, agentStatus } = useStore()
  const bottomRef = useRef(null)

  // Filter: only model text output
  const dialogSteps = agentSteps.filter(s => DIALOG_TYPES.has(s.type))
  const actionSteps = agentSteps.filter(s => ACTION_TYPES.has(s.type))
  const lastActionStep = actionSteps[actionSteps.length - 1]

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
        {isWorking && (
          <div className="rounded-lg border border-blue-500/30 bg-blue-500/10 px-3 py-2">
            <div className="text-xs text-blue-300 mb-1">Что сейчас происходит</div>
            <div className="text-sm text-blue-100 whitespace-pre-wrap break-words">
              {renderToolSummary(lastActionStep)}
            </div>
            <div className="text-xs text-blue-300/80 mt-1">
              Шагов выполнено: {actionSteps.length}
            </div>
          </div>
        )}

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

        {actionSteps.length > 0 && (
          <div className="rounded border border-dark-500 bg-dark-700/50 p-2">
            <div className="text-xs text-gray-400 mb-2">Последние действия агента</div>
            <div className="space-y-1">
              {actionSteps.slice(-5).map((step, i) => (
                <div key={`${step.timestamp || i}-${i}`} className="text-xs text-gray-300 truncate">
                  • {renderToolSummary(step)}
                </div>
              ))}
            </div>
          </div>
        )}

        <div ref={bottomRef} />
      </div>
    </div>
  )
}
