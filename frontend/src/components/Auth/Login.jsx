import { useState } from 'react'
import { useStore } from '../../stores/useStore'

export default function Login({ onSwitchToRegister }) {
  const [email, setEmail] = useState('')
  const [password, setPassword] = useState('')
  const [error, setError] = useState('')
  const [loading, setLoading] = useState(false)
  const login = useStore(s => s.login)

  const handleSubmit = async (e) => {
    e.preventDefault()
    setError('')
    setLoading(true)

    try {
      await login(email, password)
      // Success - parent component will handle navigation
    } catch (e) {
      setError(e.message || 'Ошибка входа')
    } finally {
      setLoading(false)
    }
  }

  return (
    <div className="min-h-screen bg-dark-900 flex items-center justify-center p-4">
      <div className="w-full max-w-md">
        <div className="bg-dark-800 rounded-lg p-8 border border-dark-500">
          <div className="text-center mb-6">
            <div className="text-4xl mb-2">🤖</div>
            <h1 className="text-2xl font-bold text-white mb-2">AI Agent Platform</h1>
            <p className="text-gray-400 text-sm">Войдите в свой аккаунт</p>
          </div>

          {error && (
            <div className="mb-4 p-3 bg-red-500/10 border border-red-500/30 rounded text-red-300 text-sm">
              {error}
            </div>
          )}

          <form onSubmit={handleSubmit} className="space-y-4">
            <div>
              <label className="block text-sm font-medium text-gray-300 mb-1">
                Email
              </label>
              <input
                type="email"
                value={email}
                onChange={(e) => setEmail(e.target.value)}
                required
                className="w-full px-4 py-2 bg-dark-700 border border-dark-500 rounded-lg 
                  text-white placeholder-gray-500 focus:outline-none focus:border-blue-500 
                  focus:ring-1 focus:ring-blue-500"
                placeholder="your@email.com"
                disabled={loading}
              />
            </div>

            <div>
              <label className="block text-sm font-medium text-gray-300 mb-1">
                Пароль
              </label>
              <input
                type="password"
                value={password}
                onChange={(e) => setPassword(e.target.value)}
                required
                className="w-full px-4 py-2 bg-dark-700 border border-dark-500 rounded-lg 
                  text-white placeholder-gray-500 focus:outline-none focus:border-blue-500 
                  focus:ring-1 focus:ring-blue-500"
                placeholder="••••••••"
                disabled={loading}
              />
            </div>

            <button
              type="submit"
              disabled={loading}
              className="w-full py-2 bg-blue-600 hover:bg-blue-700 disabled:bg-dark-600 
                disabled:text-gray-500 text-white rounded-lg transition-colors font-medium"
            >
              {loading ? 'Вход...' : 'Войти'}
            </button>
          </form>

          <div className="mt-4 text-center">
            <button
              onClick={onSwitchToRegister}
              className="text-sm text-blue-400 hover:text-blue-300"
            >
              Нет аккаунта? Зарегистрироваться
            </button>
          </div>
        </div>
      </div>
    </div>
  )
}
