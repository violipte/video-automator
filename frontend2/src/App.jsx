import { BrowserRouter, Routes, Route, Navigate } from 'react-router-dom'
import { AppShell } from './components/Layout/AppShell'
import { Showcase } from './pages/Showcase'
import { Backlog } from './pages/Backlog'
import { Monitor } from './pages/Monitor'
import { Temas } from './pages/Temas'
import { Templates } from './pages/Templates'
import { VidMator } from './pages/VidMator'
import { Log } from './pages/Log'
import { Config } from './pages/Config'
import { NicheSpy } from './pages/NicheSpy'
import { PainelYoutube } from './pages/PainelYoutube'

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
          <Route path="/vidmator"  element={<VidMator />} />
          {/* Cadastro Canal fundido no Painel Youtube > Canais > aba Produção (31/07) */}
          <Route path="/cadastro-canal" element={<Navigate to="/painel-youtube" replace />} />
          <Route path="/niche-spy" element={<NicheSpy />} />
          <Route path="/painel-youtube" element={<PainelYoutube />} />
          <Route path="/log"       element={<Log />} />
          <Route path="/config"    element={<Config />} />
          <Route path="/showcase"  element={<Showcase />} />
          <Route path="*"          element={<Navigate to="/monitor" replace />} />
        </Routes>
      </AppShell>
    </BrowserRouter>
  )
}
