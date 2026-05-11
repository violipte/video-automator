import { useEffect, useMemo, useState } from 'react'
import { PageHeader } from '../../components/Layout/AppShell'
import { Card } from '../../components/Common/Card'
import { Button } from '../../components/Common/Button'
import { Spinner } from '../../components/Common/Spinner'
import { toast } from '../../components/Common/Toast'
import { ChatPanel } from '../../components/Chat/ChatPanel'
import { api } from '../../api/client'
import { CellModal } from './CellModal'
import { DistribuirModal } from './DistribuirModal'
import './Temas.css'

// Status visual da celula (compat com v1):
// empty (cinza) < tema (amarelo) < titulo (azul) < roteiro (roxo) < done (verde)
function getCellStatus(cel) {
  if (!cel) return 'empty'
  if (cel.done) return cel.done_type === 'auto' ? 'done-auto' : 'done-manual'
  if (cel.roteiro || cel.tem_roteiro) return 'roteiro'
  if (cel.titulo) return 'titulo'
  if (cel.tema) return 'tema'
  return 'empty'
}

const STATUS_LABEL = {
  empty: 'Vazio',
  tema: 'Tema preenchido',
  titulo: 'Título definido',
  roteiro: 'Roteiro gerado',
  'done-manual': 'Concluído (manual)',
  'done-auto': 'Concluído (automático)',
}

// === Filtros de Data (persistidos em localStorage) ===
const FILTRO_KEY = 'v2:temasFiltro'

function parseDataDDMMYYYY(s) {
  const p = (s || '').split('/')
  if (p.length !== 3) return null
  const d = new Date(parseInt(p[2]), parseInt(p[1]) - 1, parseInt(p[0]))
  return isNaN(d) ? null : d
}
function toIsoYMD(d) {
  const m = String(d.getMonth() + 1).padStart(2, '0')
  const dd = String(d.getDate()).padStart(2, '0')
  return d.getFullYear() + '-' + m + '-' + dd
}
function isoToDate(iso) {
  if (!iso) return null
  const [y, m, d] = iso.split('-')
  return new Date(parseInt(y), parseInt(m) - 1, parseInt(d))
}
function presetRange(preset) {
  const hoje = new Date(); hoje.setHours(0, 0, 0, 0)
  if (preset === 'hoje') return { start: toIsoYMD(hoje), end: toIsoYMD(hoje) }
  if (preset === '7d') {
    const d7 = new Date(hoje); d7.setDate(d7.getDate() - 6)
    return { start: toIsoYMD(d7), end: toIsoYMD(hoje) }
  }
  if (preset === 'mes') {
    const m0 = new Date(hoje.getFullYear(), hoje.getMonth(), 1)
    const m1 = new Date(hoje.getFullYear(), hoje.getMonth() + 1, 0)
    return { start: toIsoYMD(m0), end: toIsoYMD(m1) }
  }
  // 'tudo'
  return { start: '', end: '' }
}
function loadFiltro() {
  try {
    const raw = localStorage.getItem(FILTRO_KEY)
    if (raw) {
      const c = JSON.parse(raw)
      // Se preset=mes mas datas não batem (mudou o mês), recalcula
      if (c.preset === 'mes') return { preset: 'mes', ...presetRange('mes') }
      return c
    }
  } catch {}
  return { preset: 'mes', ...presetRange('mes') }
}
function saveFiltro(f) {
  try { localStorage.setItem(FILTRO_KEY, JSON.stringify(f)) } catch {}
}

export function Temas() {
  const [temas, setTemas] = useState(null)
  const [loading, setLoading] = useState(true)
  const [editing, setEditing] = useState(null)
  const [chatOpen, setChatOpen] = useState(false)
  const [distribuirOpen, setDistribuirOpen] = useState(false)
  const [filtro, setFiltro] = useState(loadFiltro)

  async function load() {
    setLoading(true)
    try {
      const d = await api.get('/api/temas')
      setTemas(d)
    } catch (e) {
      toast.error('Erro: ' + e.message)
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => { load() }, [])

  async function saveCells(updates) {
    if (!temas?.linhas?.length || !temas?.colunas?.length) {
      toast.error('Bloqueado: state inválido. Recarregue.')
      return
    }
    const novo = JSON.parse(JSON.stringify(temas))
    novo.celulas = novo.celulas || {}
    for (const [key, fields] of Object.entries(updates)) {
      novo.celulas[key] = { ...(novo.celulas[key] || {}), ...fields }
    }
    console.group('[saveCells] enviando POST /api/temas')
    console.log(`Modificando ${Object.keys(updates).length} celula(s):`)
    for (const [key, fields] of Object.entries(updates)) {
      console.log(`  ${key}: input=`, fields, ' resultado=', novo.celulas[key])
    }
    console.groupEnd()
    try {
      const resp = await api.post('/api/temas', { temas: novo })
      console.log('[saveCells] resposta backend:', resp)
      try {
        const fresh = await api.get('/api/temas')
        setTemas(fresh)
      } catch (e) {
        setTemas(novo)
        console.warn('[saveCells] reload pos-save falhou:', e.message)
      }
      toast.success(`Salvo (${Object.keys(updates).length} célula${Object.keys(updates).length > 1 ? 's' : ''})`)
    } catch (e) {
      console.error('[saveCells] erro:', e)
      toast.error('Erro: ' + e.message)
    }
  }

  async function saveLinhas(novasLinhas) {
    if (!temas) return
    const novo = JSON.parse(JSON.stringify(temas))
    novo.linhas = novasLinhas
    setTemas(novo)
    try {
      await api.post('/api/temas', { temas: novo })
    } catch (e) {
      console.error('[saveLinhas] erro:', e)
      toast.error('Erro ao salvar: ' + e.message)
      load()
    }
  }

  function toggleCollapse(ri) {
    if (!temas?.linhas) return
    const novas = temas.linhas.map((l, i) =>
      i === ri ? { ...l, collapsed: !l.collapsed } : l
    )
    saveLinhas(novas)
  }

  function toggleAllCollapse() {
    if (!temas?.linhas) return
    const algumExpandido = temas.linhas.some(l => !l.collapsed)
    const novas = temas.linhas.map(l => ({ ...l, collapsed: algumExpandido }))
    saveLinhas(novas)
  }

  function aplicarPreset(preset) {
    const range = presetRange(preset)
    const novo = { preset, ...range }
    setFiltro(novo); saveFiltro(novo)
  }
  function aplicarRange(start, end) {
    const novo = { preset: 'custom', start: start || '', end: end || '' }
    setFiltro(novo); saveFiltro(novo)
  }

  if (loading || !temas) {
    return (
      <>
        <PageHeader title="Temas" />
        <div style={{ textAlign: 'center', padding: 60 }}><Spinner size={24} /></div>
      </>
    )
  }

  const linhas = temas.linhas || []
  const colunas = temas.colunas || []
  const celulas = temas.celulas || {}

  // Aplica filtro: array de booleans linha-por-linha
  const startD = isoToDate(filtro.start)
  const endD = isoToDate(filtro.end)
  const linhaVisivel = linhas.map(l => {
    const d = parseDataDDMMYYYY(l.data)
    if (!d) return !startD && !endD  // se filtro ativo, esconde linhas sem data válida
    if (startD && d < startD) return false
    if (endD && d > endD) return false
    return true
  })
  const visiveis = linhaVisivel.filter(Boolean).length
  const algumExpandido = linhas.some(l => !l.collapsed)
  const toggleAllLabel = algumExpandido ? '⤴ Colapsar' : '⤵ Expandir'

  return (
    <>
      <PageHeader
        title="Temas"
        subtitle={`${visiveis}/${linhas.length} datas × ${colunas.length} canais. Click em qualquer célula pra editar.`}
        actions={
          <>
            <Button variant="primary" onClick={() => setDistribuirOpen(true)}>
              📤 Distribuir
            </Button>
            <Button variant={chatOpen ? 'primary' : 'ghost'} onClick={() => setChatOpen(o => !o)}>
              💬 Chat Títulos
            </Button>
            <Button variant="ghost" onClick={toggleAllCollapse}>{toggleAllLabel}</Button>
            <Button variant="ghost" onClick={load}>↻</Button>
          </>
        }
      />

      {/* Barra de filtros */}
      <div className="temas-filtros">
        <div className="temas-filtro-presets">
          {['hoje', '7d', 'mes', 'tudo'].map(p => (
            <button
              key={p}
              className={`mini-btn ${filtro.preset === p ? 'mini-btn-on' : ''}`}
              onClick={() => aplicarPreset(p)}
            >
              {p === 'hoje' ? 'Hoje' : p === '7d' ? '7 dias' : p === 'mes' ? 'Mês atual' : 'Tudo'}
            </button>
          ))}
        </div>
        <div className="temas-filtro-range">
          <span>de</span>
          <input
            type="date"
            value={filtro.start || ''}
            onChange={(e) => aplicarRange(e.target.value, filtro.end)}
          />
          <span>até</span>
          <input
            type="date"
            value={filtro.end || ''}
            onChange={(e) => aplicarRange(filtro.start, e.target.value)}
          />
        </div>
      </div>

      <Card padding="none" className="temas-grid-wrap">
        <div className="temas-scroll">
          <table className="temas-grid">
            <thead>
              <tr>
                <th className="temas-th-data">Data</th>
                {colunas.map((col, ci) => (
                  <th key={ci} className={col.tipo === 'coringa' ? 'temas-th-base' : ''}>
                    {col.nome}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {linhas.map((linha, ri) => {
                if (!linhaVisivel[ri]) return null
                const collapsed = !!linha.collapsed
                return (
                  <tr key={ri} className={collapsed ? 'temas-row-collapsed' : ''}>
                    <td className="temas-td-data">
                      <div className="temas-data-inner">
                        <button
                          className="temas-collapse-btn"
                          onClick={(e) => { e.stopPropagation(); toggleCollapse(ri) }}
                          title={collapsed ? 'Expandir linha' : 'Colapsar linha'}
                        >
                          {collapsed ? '▶' : '▼'}
                        </button>
                        <span>{linha.data}</span>
                      </div>
                    </td>
                    {colunas.map((col, ci) => {
                      const key = `${ri}_${ci}`
                      const cel = celulas[key] || {}
                      const isBase = col.tipo === 'coringa'
                      const isEmpty = !cel.tema && !cel.titulo && !cel.thumb
                      const status = getCellStatus(cel)
                      return (
                        <td
                          key={ci}
                          className={`temas-cell ${isBase ? 'temas-cell-base' : ''} ${isEmpty ? 'temas-cell-empty' : ''} ${collapsed ? 'temas-cell-collapsed' : ''}`}
                          onClick={() => setEditing({ row: ri, col: ci, colName: col.nome, data: linha.data, isBase })}
                        >
                          {collapsed ? (
                            <div className="cell-status-compact">
                              <span className={`cell-status-dot status-${status}`} title={STATUS_LABEL[status] || ''} />
                            </div>
                          ) : (
                            <>
                              {cel.tema && <div className="cell-tema">{cel.tema}</div>}
                              {cel.titulo && <div className="cell-titulo">{cel.titulo}</div>}
                              {cel.thumb && <div className="cell-thumb">{cel.thumb}</div>}
                              {isEmpty && <div className="cell-empty">—</div>}
                              {!isEmpty && (
                                <span className={`cell-status-dot cell-status-corner status-${status}`} title={STATUS_LABEL[status] || ''} />
                              )}
                            </>
                          )}
                        </td>
                      )
                    })}
                  </tr>
                )
              })}
              {visiveis === 0 && (
                <tr>
                  <td colSpan={colunas.length + 1} style={{ textAlign: 'center', padding: 30, color: 'var(--text-muted)' }}>
                    Nenhuma data dentro do filtro. Ajuste o range acima.
                  </td>
                </tr>
              )}
            </tbody>
          </table>
        </div>
      </Card>

      <CellModal
        editing={editing}
        cel={editing ? (celulas[`${editing.row}_${editing.col}`] || {}) : {}}
        temas={temas}
        onClose={() => setEditing(null)}
        onSave={async (updates) => { await saveCells(updates); setEditing(null) }}
      />

      <ChatPanel
        agent="titulos"
        title="Claude — Títulos"
        placeholder="Pergunte algo… (Enter envia, Shift+Enter pula linha)"
        open={chatOpen}
        onClose={() => setChatOpen(false)}
      />

      <DistribuirModal
        open={distribuirOpen}
        temas={temas}
        linhaVisivel={linhaVisivel}
        onClose={() => setDistribuirOpen(false)}
        onAfterApply={load}
      />
    </>
  )
}
