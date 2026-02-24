import { useEffect, useRef } from 'react'
import { useStore } from '../../stores/useStore'

const STEP_ICONS = {
  thinking: '🧠',
  llm_text: '💬',
  tool_call: '🔧',
  tool_result: '📋',
  response: '✅',
  error: '❌',
}

const STEP_COLORS = {
  thinking: 'text-purple-400',
  llm_text: 'text-amber-400',
  tool_call: 'text-blue-400',
  tool_result: 'text-gray-400',
  response: 'text-green-400',
  error: 'text-red-400',
}

/** Activity shows operational steps: thinking, tool calls, results, errors.
 *  DeepSeek model text (llm_text) and final responses go to ProcessDialog. */
const ACTIVITY_TYPES = new Set(['thinking', 'tool_call', 'tool_result', 'error'])

export default function AgentActivity() {
  const { agentSteps, agentStatus } = useStore()
  const bottomRef = useRef(null)

  // Filter: only operational steps
  const activitySteps = agentSteps.filter(s => ACTIVITY_TYPES.has(s.type))

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: 'smooth' })
  }, [activitySteps])

  return (
    <div className="flex flex-col h-full bg-dark-800">
      {/* Header */}
      <div className="flex items-center gap-3 px-4 py-3 border-b border-dark-500">
        <div className="text-lg font-semibold">⚡ Активность</div>
        {agentStatus !== 'idle' && agentStatus !== 'done' && (
          <div className="ml-auto flex items-center gap-2">
            <div className="w-2 h-2 rounded-full bg-amber-400 animate-pulse-dot" />
            <span className="text-xs text-amber-400">
              {agentStatus === 'thinking' ? 'Думает' : 'Работает'}
            </span>
          </div>
        )}
        <span className="text-xs text-gray-500 ml-auto">
          {activitySteps.length} шагов
        </span>
      </div>

      {/* Steps */}
      <div className="flex-1 overflow-y-auto p-3 space-y-1.5">
        {activitySteps.length === 0 && (
          <div className="flex flex-col items-center justify-center h-full text-gray-600 space-y-2">
            <div className="text-3xl">⚡</div>
            <div className="text-sm">Здесь будут шаги агента</div>
          </div>
        )}

        {activitySteps.map((step, i) => (
          <div
            key={i}
            className="animate-fade-in-up bg-dark-700 rounded-lg px-3 py-2 border border-dark-500"
          >
            <div className="flex items-start gap-2">
              <span className="text-base flex-shrink-0 mt-0.5">
                {STEP_ICONS[step.type] || '📌'}
              </span>
              <div className="flex-1 min-w-0">
                <div className={`text-xs font-medium ${STEP_COLORS[step.type] || 'text-gray-400'}`}>
                  {step.type === 'tool_call' && step.tool_name
                    ? `${step.tool_name}()`
                    : step.type === 'thinking'
                    ? `Думаю (шаг ${step.step_number})`
                    : step.type}
                </div>
                <div className="text-xs text-gray-400 mt-0.5 whitespace-pre-wrap">
                  {step.content}
                </div>

                {/* Show tool arguments for tool_call */}
                {step.type === 'tool_call' && step.tool_args && (
                  <div className="mt-1.5 bg-dark-900 rounded px-2 py-1.5 text-xs font-mono text-gray-500 max-h-24 overflow-y-auto">
                    {step.tool_name === 'execute_command' && (
                      <span className="text-amber-400">$ {step.tool_args.command}</span>
                    )}
                    {step.tool_name === 'write_file' && (
                      <span className="text-blue-400">📄 {step.tool_args.filepath}</span>
                    )}
                    {step.tool_name === 'read_file' && (
                      <span className="text-green-400">📖 {step.tool_args.filepath}</span>
                    )}
                    {step.tool_name === 'list_files' && (
                      <span className="text-purple-400">📁 {step.tool_args.path || '.'}</span>
                    )}
                  </div>
                )}

                {/* Show tool result snippet */}
                {step.type === 'tool_result' && step.tool_result && (
                  <div className="mt-1.5 bg-dark-900 rounded px-2 py-1.5 text-xs font-mono text-gray-500 max-h-32 overflow-y-auto">
                    {step.tool_result.success
                      ? <span className="text-green-400">{step.content}</span>
                      : <span className="text-red-400">{step.tool_result.error}</span>
                    }
                  </div>
                )}
              </div>
            </div>
          </div>
        ))}

        <div ref={bottomRef} />
      </div>
    </div>
  )
}
