import { useState, useMemo, useEffect } from 'react'
import { Modal } from '../../components/Common/Modal'
import { Button } from '../../components/Common/Button'
import { Spinner } from '../../components/Common/Spinner'
import { toast } from '../../components/Common/Toast'
import { api } from '../../api/client'

// Modal pra distribuir Coringa (BASE → canais filhos).
// Suporta selecao por checkbox e acao em lote nas selecionadas.
// Linhas com 0 canais pendentes vem desmarcadas por default; com pendentes,
// marcadas. Backend (coringa_distribuidor.py:768-776) ja pula 3/3 cheios,
// entao distribuir uma "completa" eh no-op seguro.
export function DistribuirModal({ open, temas, linhaVisivel, onClose, onAfterApply }) {
  // TODOS os hooks no topo - SEM early return antes deles (regras do React).
  const [busy, setBusy] = useState(false)
  const [busyLinha, setBusyLinha] = useState(null)
  const [selecionadas, setSelecionadas] = useState({})  // {idx: true}
  const [progresso, setProgresso] = useState(null)  // {atual, total}

  const linhas = (temas && temas.linhas) || []
  const colunas = (temas && temas.colunas) || []
  const celulas = (temas && temas.celulas) || {}
  const baseIdx = colunas.findIndex(c => c.tipo === 'coringa')

  // Linhas com BASE preenchido (tema ou titulo) e dentro do filtro de data
  const linhasDistribuiveis = useMemo(() => {
    if (!temas) return []
    return linhas
      .map((l, i) => ({ linha: l, idx: i }))
      .filter(({ linha, idx }) => {
        if (linhaVisivel && !linhaVisivel[idx]) return false
        if (baseIdx < 0) return false
        const cel = celulas[`${idx}_${baseIdx}`] || {}
        return !!(cel.tema || cel.titulo)
      })
  }, [temas, linhas, linhaVisivel, baseIdx, celulas])

  // Resumo do que vai acontecer: para cada linha, quantos canais filhos vazios
  function infoLinha(idx) {
    let pendentes = 0
    for (let ci = 0; ci < colunas.length; ci++) {
      if (ci === baseIdx) continue
      const c = colunas[ci]
      if (c.tipo === 'coringa') continue
      const cel = celulas[`${idx}_${ci}`] || {}
      if (!cel.titulo) pendentes++
    }
    return pendentes
  }

  // Default: marca linhas com pendentes; desmarca linhas completas.
  // Roda quando modal abre / lista muda. Hook AQUI no topo (regra React).
  useEffect(() => {
    if (!open || !temas) return
    const novo = {}
    for (const { idx } of linhasDistribuiveis) {
      novo[idx] = infoLinha(idx) > 0
    }
    setSelecionadas(novo)
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open, temas, linhasDistribuiveis.length])

  // Early return DEPOIS dos hooks
  if (!open || !temas) return null

  const numSelecionadas = Object.values(selecionadas).filter(Boolean).length

  function toggleLinha(idx) {
    setSelecionadas(prev => ({ ...prev, [idx]: !prev[idx] }))
  }

  function marcarTodas() {
    const novo = {}
    for (const { idx } of linhasDistribuiveis) novo[idx] = true
    setSelecionadas(novo)
  }

  function desmarcarTodas() {
    setSelecionadas({})
  }

  function marcarSoComPendentes() {
    const novo = {}
    for (const { idx } of linhasDistribuiveis) {
      novo[idx] = infoLinha(idx) > 0
    }
    setSelecionadas(novo)
  }

  // Distribuir selecionadas: chama /distribuir-linha/{ri} em sequencia
  // (zero mudanca no backend, evita conflito de execucao paralela de pipelines)
  async function distribuirSelecionadas() {
    const idxs = linhasDistribuiveis
      .map(d => d.idx)
      .filter(idx => selecionadas[idx])
    if (idxs.length === 0) { toast.error('Nenhuma data selecionada'); return }
    if (!confirm(`Distribuir ${idxs.length} data(s) selecionada(s)?\n\nDatas vazias/parciais vao receber distribuicao; ja preenchidas serao puladas no backend.`)) return

    setBusy(true)
    setProgresso({ atual: 0, total: idxs.length })
    let ok = 0, skip = 0, erro = 0
    for (let i = 0; i < idxs.length; i++) {
      const ri = idxs[i]
      setProgresso({ atual: i + 1, total: idxs.length })
      setBusyLinha(ri)
      try {
        const r = await api.post(`/api/coringa/distribuir-linha/${ri}`, {})
        if (r.erro) {
          erro++
          console.warn(`Linha ${ri} erro:`, r.erro)
        } else {
          const n = (r.distribuidos || []).length
          const pulados = (r.pulados || []).length
          if (n > 0) ok++; else if (pulados > 0) skip++
          else ok++  // sem distribuidos nem pulados = no-op
        }
      } catch (e) {
        erro++
        console.warn(`Linha ${ri} exception:`, e.message)
      }
    }
    setBusyLinha(null)
    setProgresso(null)
    setBusy(false)
    const msg = `Distribuicao concluida: ${ok} OK${skip ? `, ${skip} ja preenchidas` : ''}${erro ? `, ${erro} erros` : ''}`
    if (erro > 0) toast.error(msg); else toast.success(msg)
    onAfterApply && onAfterApply()
  }

  // Distribuir todas: comportamento original (atalho), respeita filtro de data
  async function distribuirTodas() {
    if (busy) return
    if (!confirm(`Distribuir Coringa de TODAS as ${linhasDistribuiveis.length} datas com BASE preenchido?`)) return
    setBusy(true)
    try {
      const r = await api.post('/api/coringa/distribuir-agora', {})
      const n = (r.distribuidos || []).length || r.processados || 0
      toast.success(`Distribuicao disparada${n ? ` (${n} celulas geradas)` : ''}`)
      onAfterApply && onAfterApply()
      onClose()
    } catch (e) {
      toast.error('Erro: ' + e.message)
    } finally {
      setBusy(false)
    }
  }

  // Distribuir 1 linha (botao individual)
  async function distribuirLinha(ri) {
    if (busy || busyLinha != null) return
    setBusyLinha(ri)
    try {
      const r = await api.post(`/api/coringa/distribuir-linha/${ri}`, {})
      if (r.erro) {
        toast.error(`Erro: ${r.erro}`)
      } else {
        const n = (r.distribuidos || []).length
        toast.success(`Linha distribuida${n ? ` (${n} celulas geradas)` : ''}`)
        onAfterApply && onAfterApply()
      }
    } catch (e) {
      toast.error('Erro: ' + e.message)
    } finally {
      setBusyLinha(null)
    }
  }

  return (
    <Modal
      open={open}
      onClose={onClose}
      title="Distribuir Coringa → canais filhos"
      size="md"
      footer={
        <>
          <Button variant="ghost" onClick={onClose}>Fechar</Button>
          <Button
            variant="primary"
            onClick={distribuirSelecionadas}
            disabled={busy || numSelecionadas === 0}
            style={{ fontWeight: 600 }}
          >
            {busy && progresso
              ? <><Spinner size={12} /> Distribuindo {progresso.atual}/{progresso.total}...</>
              : `📤 Distribuir selecionadas (${numSelecionadas})`}
          </Button>
        </>
      }
    >
      <p style={{ fontSize: 'var(--text-sm)', color: 'var(--text-secondary)', marginBottom: 12 }}>
        A BASE de cada data é adaptada e distribuída pros canais filhos configurados.
        A distribuição respeita os modos por canal (prompt-mixer, agente, imagem fixa).
      </p>

      {linhasDistribuiveis.length === 0 ? (
        <div style={{
          padding: 20,
          textAlign: 'center',
          color: 'var(--text-muted)',
          background: 'var(--panel-2)',
          borderRadius: 'var(--radius-sm)',
          fontSize: 'var(--text-sm)'
        }}>
          Nenhuma data com BASE preenchida no filtro atual.<br />
          Preencha o BASE de alguma data antes de distribuir.
        </div>
      ) : (
        <>
          {/* Acoes de selecao em massa */}
          <div style={{
            display: 'flex', gap: 8, marginBottom: 10, flexWrap: 'wrap',
            fontSize: 'var(--text-xs)', alignItems: 'center'
          }}>
            <span style={{ color: 'var(--text-muted)', marginRight: 4 }}>Marcar:</span>
            <button type="button" onClick={marcarTodas}
              style={btnLinkStyle} disabled={busy}>
              ✓ todas ({linhasDistribuiveis.length})
            </button>
            <button type="button" onClick={desmarcarTodas}
              style={btnLinkStyle} disabled={busy}>
              ✗ nenhuma
            </button>
            <button type="button" onClick={marcarSoComPendentes}
              style={btnLinkStyle} disabled={busy}>
              ⚠ só com pendentes
            </button>
            <span style={{ marginLeft: 'auto', color: 'var(--text-muted)' }}>
              {numSelecionadas} selecionada(s)
            </span>
          </div>

          {/* Lista com checkbox por linha */}
          <div className="distribuir-lista">
            {linhasDistribuiveis.map(({ linha, idx }) => {
              const cel = celulas[`${idx}_${baseIdx}`] || {}
              const pendentes = infoLinha(idx)
              const checked = !!selecionadas[idx]
              const status = pendentes === 0 ? '🟢' : (pendentes >= 14 ? '🔴' : '🟡')
              return (
                <div
                  key={idx}
                  className="distribuir-item"
                  style={{ opacity: pendentes === 0 ? 0.65 : 1 }}
                >
                  <label style={{ display: 'flex', alignItems: 'center', cursor: 'pointer', marginRight: 8 }}>
                    <input
                      type="checkbox"
                      checked={checked}
                      onChange={() => toggleLinha(idx)}
                      disabled={busy}
                      style={{ cursor: 'pointer', width: 18, height: 18 }}
                    />
                  </label>
                  <div className="distribuir-item-info" style={{ flex: 1 }}>
                    <div className="distribuir-item-data">{linha.data}</div>
                    <div className="distribuir-item-tema">
                      {(cel.titulo || cel.tema || '').substring(0, 90)}
                    </div>
                    <div className="distribuir-item-meta">
                      {status} {pendentes > 0 ? `${pendentes} canais vazios` : 'todos canais preenchidos'}
                    </div>
                  </div>
                  <Button
                    variant="ghost"
                    onClick={() => distribuirLinha(idx)}
                    disabled={busyLinha != null || busy}
                  >
                    {busyLinha === idx ? <Spinner size={12} /> : 'Distribuir →'}
                  </Button>
                </div>
              )
            })}
          </div>

          {/* Atalho: distribuir todas no atalho global (sem precisar marcar) */}
          <div style={{
            marginTop: 12, paddingTop: 10,
            borderTop: '1px solid var(--border)',
            fontSize: 'var(--text-xs)', color: 'var(--text-muted)',
            display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: 8
          }}>
            <span>Atalho: ignora seleção e dispara TODAS de uma vez via backend</span>
            <button type="button" onClick={distribuirTodas} disabled={busy} style={btnLinkStyle}>
              Distribuir todas ({linhasDistribuiveis.length}) →
            </button>
          </div>
        </>
      )}
    </Modal>
  )
}

const btnLinkStyle = {
  background: 'transparent',
  border: '1px solid var(--border)',
  borderRadius: 4,
  padding: '3px 8px',
  fontSize: 11,
  color: 'var(--text-pri)',
  cursor: 'pointer',
}
