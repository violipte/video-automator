import { NavLink } from 'react-router-dom'
import { useEffect, useState } from 'react'
import { api } from '../../api/client'
import './Sidebar.css'

const NAV = [
  { to: '/temas',    label: 'Temas',         icon: 'M9.663 17h4.673M12 3v1M19.07 4.93l-.7.7M21 12h-1M4 12H3M5.64 5.64l-.71-.7M9 18.5h6L13.5 16h-3L9 18.5z' },
  { to: '/backlog',  label: 'Backlog Temas', icon: 'M9 11H5a2 2 0 00-2 2v7a2 2 0 002 2h14a2 2 0 002-2v-7a2 2 0 00-2-2h-4M9 11V5h6v6M12 14v4' },
  { to: '/templates',label: 'Templates',     icon: 'M3 3h7v7H3zM14 3h7v7h-7zM3 14h7v7H3zM14 14h7v7h-7z' },
  { to: '/monitor',  label: 'Monitor',       icon: 'M22 12h-4l-3 9L9 3l-3 9H2' },
  { to: '/log',      label: 'Log',           icon: 'M3 3h18v18H3zM3 9h18M9 3v18' },
  { to: '/config',   label: 'Config',        icon: 'M12 15a3 3 0 100-6 3 3 0 000 6zM19.4 15a1.65 1.65 0 00.33 1.82l.06.06a2 2 0 01-2.83 2.83l-.06-.06a1.65 1.65 0 00-1.82-.33 1.65 1.65 0 00-1 1.51V21a2 2 0 01-4 0v-.09A1.65 1.65 0 009 19.4a1.65 1.65 0 00-1.82.33l-.06.06a2 2 0 01-2.83-2.83l.06-.06A1.65 1.65 0 004.68 15a1.65 1.65 0 00-1.51-1H3a2 2 0 010-4h.09A1.65 1.65 0 004.6 9' },
]

export function Sidebar() {
  const [health, setHealth] = useState(null)

  useEffect(() => {
    let mounted = true
    const tick = async () => {
      try {
        const h = await api.get('/api/health')
        if (mounted) setHealth(h)
      } catch (e) { if (mounted) setHealth({ ok: false }) }
    }
    tick()
    const id = setInterval(tick, 10000)
    return () => { mounted = false; clearInterval(id) }
  }, [])

  return (
    <aside className="sidebar">
      <div className="sidebar-brand">
        <div className="sidebar-logo">
          <svg width="28" height="28" viewBox="0 0 100 100">
            <rect width="100" height="100" rx="22" fill="var(--panel-2)" />
            <polygon points="38,28 38,72 76,50" fill="var(--accent)" />
          </svg>
        </div>
        <div className="sidebar-brand-text">
          <strong>Automator</strong>
          <span>Video Production</span>
        </div>
      </div>

      <nav className="sidebar-nav">
        {NAV.map((n) => (
          <NavLink
            key={n.to}
            to={n.to}
            className={({ isActive }) => `sidebar-link ${isActive ? 'active' : ''}`}
          >
            <svg className="sidebar-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round">
              <path d={n.icon} />
            </svg>
            <span>{n.label}</span>
          </NavLink>
        ))}
      </nav>

      <div className="sidebar-footer">
        <div className="sidebar-status">
          <span className={`status-dot ${health?.producao_ativa ? 'active' : ''}`} />
          <span className="status-text">
            {health == null ? 'conectando…' : health.producao_ativa ? 'em produção' : 'idle'}
          </span>
        </div>
        {health?.pid && <div className="sidebar-pid">pid {health.pid}</div>}
      </div>
    </aside>
  )
}
