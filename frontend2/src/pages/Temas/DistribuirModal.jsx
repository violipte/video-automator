import { useState } from 'react'
import { Modal } from '../../components/Common/Modal'
import { Button } from '../../components/Common/Button'
import { Spinner } from '../../components/Common/Spinner'
import { toast } from '../../components/Common/Toast'
import { api } from '../../api/client'

// Modal pra distribuir Coringa (BASE → canais filhos).
// Opções:
//   - Distribuir todas pendentes (POST /api/coringa/distribuir-agora)
//   - Distribuir uma data específica (POST /api/coringa/distribuir-linha/{ri})
//
// Filtro: lista só linhas que têm BASE preenchido (tema ou titulo) E que
// estão dentro do filtro de data ativo (linhaVisivel passado por prop).
export function DistribuirModal({ open, temas, linhaVisivel, onClose, onAfterApply }) {
  const [busy, setBusy] = useState(false)
  const [busyLinha, setBusyLinha] = useState(null)

  if (!open || !temas) return null

  const linhas = temas.linhas || []
  const colunas = temas.colunas || []
  const celulas = temas.celulas || {}
  const baseIdx = colunas.findIndex(c => c.tipo === 'coringa')

  // Linhas com BASE preenchido (tema ou titulo) e dentro do filtro de data
  const linhasDistribuiveis = linhas
    .map((l, i) => ({ linha: l, idx: i }))
    .filter(({ linha, idx }) => {
      if (linhaVisivel && !linhaVisivel[idx]) return false
      if (baseIdx < 0) return false
      const cel = celulas[`${idx}_${baseIdx}`] || {}
      return !!(cel.tema || cel.titulo)
    })

  async function distribuirTudo() {
    if (busy) return
    if (!confirm(`Distribuir Coringa de TODAS as datas pendentes?\n(${linhasDistribuiveis.length} datas com BASE preenchido visíveis no filtro atual)`)) return
    setBusy(true)
    try {
      const r = await api.post('/api/coringa/distribuir-agora', {})
      const n = (r.distribuidos || []).length || r.processados || 0
      toast.success(`Distribuição disparada${n ? ` (${n} células geradas)` : ''}`)
      onAfterApply && onAfterApply()
      onClose()
    } catch (e) {
      toast.error('Erro: ' + e.message)
    } finally {
      setBusy(false)
    }
  }

  async function distribuirLinha(ri) {
    if (busy || busyLinha != null) return
    setBusyLinha(ri)
    try {
      const r = await api.post(`/api/coringa/distribuir-linha/${ri}`, {})
      if (r.erro) {
        toast.error(`Erro: ${r.erro}`)
      } else {
        const n = (r.distribuidos || []).length
        toast.success(`Linha ${ri} distribuída${n ? ` (${n} células geradas)` : ''}`)
        onAfterApply && onAfterApply()
      }
    } catch (e) {
      toast.error('Erro: ' + e.message)
    } finally {
      setBusyLinha(null)
    }
  }

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

  return (
    <Modal
      open={open}
      onClose={onClose}
      title="Distribuir Coringa → canais filhos"
      size="md"
      footer={
        <>
          <Button variant="ghost" onClick={onClose}>Fechar</Button>
          <Button variant="primary" onClick={distribuirTudo} disabled={busy || linhasDistribuiveis.length === 0}>
            {busy ? <Spinner size={12} /> : `📤 Distribuir todas (${linhasDistribuiveis.length})`}
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
        <div className="distribuir-lista">
          {linhasDistribuiveis.map(({ linha, idx }) => {
            const cel = celulas[`${idx}_${baseIdx}`] || {}
            const pendentes = infoLinha(idx)
            return (
              <div key={idx} className="distribuir-item">
                <div className="distribuir-item-info">
                  <div className="distribuir-item-data">{linha.data}</div>
                  <div className="distribuir-item-tema">
                    {(cel.titulo || cel.tema || '').substring(0, 90)}
                  </div>
                  <div className="distribuir-item-meta">
                    {pendentes > 0 ? `${pendentes} canais vazios` : 'todos canais preenchidos'}
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
      )}
    </Modal>
  )
}
