import { Sidebar } from './Sidebar'
import { ToastContainer } from '../Common/Toast'
import './AppShell.css'

export function AppShell({ children }) {
  return (
    <div className="app-shell">
      <Sidebar />
      <main className="app-main">
        <div className="app-content fade-in">
          {children}
        </div>
      </main>
      <ToastContainer />
    </div>
  )
}

export function PageHeader({ title, subtitle, actions }) {
  return (
    <header className="page-header">
      <div className="page-header-text">
        <h1 className="page-title">{title}</h1>
        {subtitle && <p className="page-subtitle">{subtitle}</p>}
      </div>
      {actions && <div className="page-header-actions">{actions}</div>}
    </header>
  )
}
