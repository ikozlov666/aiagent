import { useEffect, useRef } from 'react'
import { Terminal as XTerm } from '@xterm/xterm'
import { FitAddon } from '@xterm/addon-fit'
import { WebLinksAddon } from '@xterm/addon-web-links'
import '@xterm/xterm/css/xterm.css'
import { useStore } from '../../stores/useStore'

export default function Terminal() {
  const { projectId } = useStore()
  const terminalRef = useRef(null)
  const xtermRef = useRef(null)
  const wsRef = useRef(null)
  const fitAddonRef = useRef(null)

  useEffect(() => {
    if (!projectId) return

    // Initialize xterm
    const terminal = new XTerm({
      theme: {
        background: '#1e1e1e',
        foreground: '#d4d4d4',
        cursor: '#aeafad',
        selection: '#264f78',
        black: '#000000',
        red: '#cd3131',
        green: '#0dbc79',
        yellow: '#e5e510',
        blue: '#2472c8',
        magenta: '#bc3fbc',
        cyan: '#11a8cd',
        white: '#e5e5e5',
        brightBlack: '#666666',
        brightRed: '#f14c4c',
        brightGreen: '#23d18b',
        brightYellow: '#f5f543',
        brightBlue: '#3b8eea',
        brightMagenta: '#d670d6',
        brightCyan: '#29b8db',
        brightWhite: '#e5e5e5',
      },
      fontSize: 14,
      fontFamily: 'Monaco, Menlo, "Ubuntu Mono", monospace',
      cursorBlink: true,
      cursorStyle: 'block',
    })

    const fitAddon = new FitAddon()
    const webLinksAddon = new WebLinksAddon()
    
    terminal.loadAddon(fitAddon)
    terminal.loadAddon(webLinksAddon)

    terminal.open(terminalRef.current)
    fitAddon.fit()

    xtermRef.current = terminal
    fitAddonRef.current = fitAddon

    // Connect WebSocket
    const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:'
    const wsUrl = `${protocol}//${window.location.host}/ws/terminal/${projectId}`
    const ws = new WebSocket(wsUrl)

    ws.onopen = () => {
      console.log('✅ Terminal WebSocket connected')
    }

    ws.onmessage = (event) => {
      try {
        const data = JSON.parse(event.data)
        if (data.type === 'output') {
          terminal.write(data.data)
        } else if (data.type === 'error') {
          terminal.write(`\x1b[31m${data.data}\x1b[0m`) // Red color
        }
      } catch (e) {
        // Fallback: treat as plain text
        terminal.write(event.data)
      }
    }

    ws.onerror = (error) => {
      terminal.write('\r\n❌ Terminal connection error\r\n')
      console.error('Terminal WebSocket error:', error)
    }

    ws.onclose = () => {
      terminal.write('\r\n👋 Terminal disconnected\r\n')
    }

    // Handle terminal input
    let currentLine = ''
    let cursorPos = 0
    
    terminal.onData((data) => {
      const code = data.charCodeAt(0)
      
      if (code === 13) {
        // Enter pressed
        terminal.write('\r\n')
        if (ws.readyState === WebSocket.OPEN && currentLine.trim()) {
          ws.send(JSON.stringify({ command: currentLine }))
        }
        currentLine = ''
        cursorPos = 0
      } else if (code === 127 || code === 8) {
        // Backspace
        if (cursorPos > 0) {
          currentLine = currentLine.slice(0, cursorPos - 1) + currentLine.slice(cursorPos)
          cursorPos--
          terminal.write('\b \b')
        }
      } else if (code === 3) {
        // Ctrl+C
        terminal.write('^C\r\n$ ')
        currentLine = ''
        cursorPos = 0
      } else if (code >= 32) {
        // Printable character
        currentLine = currentLine.slice(0, cursorPos) + data + currentLine.slice(cursorPos)
        cursorPos++
        terminal.write(data)
      }
    })

    wsRef.current = ws

    // Handle resize
    const handleResize = () => {
      fitAddon.fit()
    }
    window.addEventListener('resize', handleResize)

    return () => {
      window.removeEventListener('resize', handleResize)
      ws.close()
      terminal.dispose()
    }
  }, [projectId])

  if (!projectId) {
    return (
      <div className="flex items-center justify-center h-full text-gray-500 text-sm">
        Создайте проект для использования терминала
      </div>
    )
  }

  return (
    <div className="flex flex-col h-full bg-dark-900">
      {/* Header */}
      <div className="flex items-center gap-2 px-3 py-2 border-b border-dark-500 bg-dark-800">
        <div className="text-sm font-semibold">💻 Терминал</div>
        <div className="ml-auto text-xs text-gray-500">
          {wsRef.current?.readyState === WebSocket.OPEN ? '🟢 Подключен' : '🔴 Отключен'}
        </div>
      </div>

      {/* Terminal */}
      <div ref={terminalRef} className="flex-1 p-2" />
    </div>
  )
}
