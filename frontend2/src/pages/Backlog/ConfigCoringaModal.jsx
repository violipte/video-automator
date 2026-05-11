import { useEffect, useState } from 'react'
import { Modal } from '../../components/Common/Modal'
import { Button } from '../../components/Common/Button'
import { toast } from '../../components/Common/Toast'
import { api } from '../../api/client'

const CASING_OPTS = [
  { value: '', label: 'Default (agent decide)' },
  { value: 'uppercase', label: 'ALL CAPS' },
  { value: 'titlecase', label: 'Title Case' },
]

export function ConfigCoringaModal({ open, onClose }) {
  const [canais, setCanais] = useState([])
  const [coSlots, setCoSlots] = useState([])
  const [loading, setLoading] = useState(true)

  useEffect(() => {
    if (!open) return
    setLoading(true)
    api.get('/api/coringa/config')
      .then(d => {
        setCanais(d.canais || [])
        setCoSlots(d.co_slots || [])
      })
      .catch(e => toast.error('Erro: ' + e.message))
      .finally(() => setLoading(false))
  }, [open])

  async function update(nome, campo, valor) {
    try {
      const r = await api.put(`/api/coringa/config/${encodeURIComponent(nome)}`, { [campo]: valor })
      if (!r.ok) { toast.error(r.erro || 'Erro'); return }
      setCanais(prev => prev.map(c => c.nome === nome ? r.config : c))
    } catch (e) {
      toast.error('Erro: ' + e.message)
    }
  }

  return (
    <Modal
      open={open}
      onClose={onClose}
      title="Config BASE — Distribuição por canal"
      size="lg"
      footer={<Button variant="secondary" onClick={onClose}>Fechar</Button>}
    >
      <p style={{ marginBottom: 16, color: 'var(--text-sec)', fontSize: 'var(--text-sm)', lineHeight: 'var(--lh-loose)' }}>
        <strong>Recebe</strong>: canal recebe distribuição automática.
        &nbsp;<strong>Adaptado</strong>: passa pelo Claude CLI (regras de <code>agents/titulos/CLAUDE.md</code>); senão é cópia direta.
        &nbsp;<strong>Casing</strong>: força casing override (Default deixa o agent escolher).
        &nbsp;<strong>Vínculo CO*</strong>: pra NARC/NPD que recebem do CO* via cascade.
      </p>

      {loading ? (
        <div style={{ padding: 20, textAlign: 'center', color: 'var(--text-muted)' }}>Carregando…</div>
      ) : (
        <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 'var(--text-sm)' }}>
          <thead>
            <tr style={{ background: 'var(--panel-2)' }}>
              <th style={th}>Canal</th>
              <th style={{ ...th, width: 80, textAlign: 'center' }}>Recebe</th>
              <th style={{ ...th, width: 80, textAlign: 'center' }}>Adaptado</th>
              <th style={{ ...th, width: 140 }}>Casing</th>
              <th style={{ ...th, width: 140 }}>Vínculo CO*</th>
            </tr>
          </thead>
          <tbody>
            {canais.map(c => {
              const isCO = /^CO\d/.test(c.nome)
              const adaptDisabled = !c.recebe
              const casingDisabled = !c.recebe || !c.adaptado
              return (
                <tr key={c.nome} style={{ borderBottom: '1px solid var(--border)' }}>
                  <td style={td}>
                    <strong>{c.nome}</strong>
                    {c.idioma && <span style={{ color: 'var(--text-muted)', fontSize: 11, marginLeft: 4 }}>({c.idioma})</span>}
                  </td>
                  <td style={{ ...td, textAlign: 'center' }}>
                    <input
                      type="checkbox"
                      checked={c.recebe}
                      onChange={(e) => update(c.nome, 'recebe', e.target.checked)}
                    />
                  </td>
                  <td style={{ ...td, textAlign: 'center' }}>
                    <input
                      type="checkbox"
                      checked={c.adaptado}
                      disabled={adaptDisabled}
                      onChange={(e) => update(c.nome, 'adaptado', e.target.checked)}
                    />
                  </td>
                  <td style={td}>
                    <select
                      className="input input-sm"
                      value={c.casing || ''}
                      disabled={casingDisabled}
                      onChange={(e) => update(c.nome, 'casing', e.target.value)}
                      style={{ width: '100%' }}
                    >
                      {CASING_OPTS.map(o => <option key={o.value} value={o.value}>{o.label}</option>)}
                    </select>
                  </td>
                  <td style={td}>
                    <select
                      className="input input-sm"
                      value={c.vinculo_co_origem || ''}
                      disabled={isCO}
                      title={isCO ? 'CO* não recebe vínculo de outro CO*' : ''}
                      onChange={(e) => update(c.nome, 'vinculo_co_origem', e.target.value)}
                      style={{ width: '100%' }}
                    >
                      <option value="">— sem vínculo —</option>
                      {coSlots.map(s => <option key={s} value={s}>{s}</option>)}
                    </select>
                  </td>
                </tr>
              )
            })}
          </tbody>
        </table>
      )}
    </Modal>
  )
}

const th = {
  padding: 10,
  textAlign: 'left',
  fontWeight: 'var(--fw-medium)',
  color: 'var(--text-sec)',
  fontSize: 'var(--text-xs)',
  letterSpacing: 'var(--track-wide)',
  textTransform: 'uppercase',
  borderBottom: '1px solid var(--border)',
}

const td = {
  padding: 10,
}
