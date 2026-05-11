import { BrowserRouter, Routes, Route, Navigate } from 'react-router-dom'
import { AppShell } from './components/Layout/AppShell'
import { Showcase } from './pages/Showcase'
import { Backlog } from './pages/Backlog'
import { Monitor } from './pages/Monitor'
import { Temas } from './pages/Temas'
import { Templates } from './pages/Templates'
import { Log } from './pages/Log'
import { Config } from './pages/Config'

const BASENAME = import.meta.env.BASE_URL.replace(/\/$/, '') || '/'

export default function App() {
  return (
    <BrowserRouter basename={BASENAME}>
      <AppShell>
        <Routes>
          <Route path="/" element={<Navigate to="/monitor" replace />} />
          <Route path="/monitor"   element={<Monitor />} />
          <Route path="/temas"     element={<Temas />} />
          <Route path="/backlog"   element={<Backlog />} />
          <Route path="/templates" element={<Templates />} />
          <Route path="/log"       element={<Log />} />
          <Route path="/config"    element={<Config />} />
          <Route path="/showcase"  element={<Showcase />} />
          <Route path="*"          element={<Navigate to="/monitor" replace />} />
        </Routes>
      </AppShell>
    </BrowserRouter>
  )
}
