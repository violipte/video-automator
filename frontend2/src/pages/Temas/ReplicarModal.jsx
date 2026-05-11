import { useEffect, useState } from 'react'
import { Modal } from '../../components/Common/Modal'
import { Button } from '../../components/Common/Button'

// Replicar valores pra outras células do grid.
// spec: { campos: ['tema','titulo','thumb'], valores: { tema, titulo, thumb } }
export function ReplicarModal({ spec, srcRow, srcCol, temas, onClose, onApply }) {
  const [datasSel, setDatasSel] = useState(new Set())
  const [canaisSel, setCanaisSel] = useState(new Set())

  useEffect(() => {
    if (!spec) { setDatasSel(new Set()); setCanaisSel(new Set()); return }
    // default: nenhum selecionado
    setDatasSel(new Set())
    setCanaisSel(new Set())
  }, [spec])

  if (!spec || !temas) return null

  const linhas = temas.linhas || []
  const colunas = temas.colunas || []
  const srcDataLabel = (linhas[srcRow] || {}).data || `linha ${srcRow}`
  const srcCanalLabel = (colunas[srcCol] || {}).nome || `coluna ${srcCol}`

  function toggleData(idx) {
    if (idx === srcRow) return  // origem nao pode ser destino
    const novo = new Set(datasSel)
    if (novo.has(idx)) novo.delete(idx); else novo.add(idx)
    setDatasSel(novo)
  }

  function toggleCanal(idx) {
    if (idx === srcCol) return
    const novo = new Set(canaisSel)
    if (novo.has(idx)) novo.delete(idx); else novo.add(idx)
    setCanaisSel(novo)
  }

  function selectAllDatas() {
    setDatasSel(new Set(linhas.map((_, i) => i).filter(i => i !== srcRow)))
  }
  function clearDatas() { setDatasSel(new Set()) }
  function selectAllCanais() {
    setCanaisSel(new Set(colunas.map((_, i) => i).filter(i => i !== srcCol)))
  }
  function clearCanais() { setCanaisSel(new Set()) }

  // Atalho: replicar pra TODOS os canais da MESMA data (caso de uso mais comum)
  function presetTodosCanaisMesmaData() {
    setDatasSel(new Set())  // vazio = mantem data atual
    setCanaisSel(new Set(colunas.map((_, i) => i).filter(i => i !== srcCol)))
  }

  // Atalho: replicar pra TODAS as datas mantendo o mesmo canal
  function presetMesmoCanalTodasDatas() {
    setDatasSel(new Set(linhas.map((_, i) => i).filter(i => i !== srcRow)))
    setCanaisSel(new Set())  // vazio = mantem canal atual
  }

  // Lista de células destino: produto cartesiano dos selecionados, exceto a origem
  function calcularDestinos() {
    const destinos = []
    const datas = datasSel.size > 0 ? [...datasSel] : [srcRow]
    const cols = canaisSel.size > 0 ? [...canaisSel] : [srcCol]
    for (const ri of datas) {
      for (const ci of cols) {
        if (ri === srcRow && ci === srcCol) continue
        destinos.push({ ri, ci })
      }
    }
    return destinos
  }

  const destinos = calcularDestinos()

  async function aplicar() {
    if (!destinos.length) {
      onClose()
      return
    }
    const updates = {}
    for (const d of destinos) {
      // Cópia defensiva: cada destino recebe um objeto novo
      const fields = {}
      for (const c of spec.campos) {
        fields[c] = spec.valores[c] != null ? spec.valores[c] : ''
      }
      updates[`${d.ri}_${d.ci}`] = fields
    }

    // Log defensivo pra debug do bug do Replicar
    console.group('[Replicar] aplicando')
    console.log('campos:', spec.campos)
    console.log('valores:', spec.valores)
    console.log('origem:', `${srcCanalLabel} — ${srcDataLabel} (${srcRow}_${srcCol})`)
    console.log(`destinos (${destinos.length}):`, destinos.map(d => `${(colunas[d.ci]||{}).nome} — ${(linhas[d.ri]||{}).data} (${d.ri}_${d.ci})`))
    console.log('updates payload:', updates)
    console.groupEnd()

    await onApply(updates)
  }

  // Preview dos destinos resolvidos (max 5 + "+N mais")
  function renderDestinosPreview() {
    if (!destinos.length) {
      return <span style={{ color: 'var(--text-muted)' }}>nenhum destino selecionado</span>
    }
    const preview = destinos.slice(0, 5).map(d => `${(colunas[d.ci] || {}).nome || '?'} ${(linhas[d.ri] || {}).data || ''}`)
    const extra = destinos.length > 5 ? ` + ${destinos.length - 5} mais` : ''
    return <span>{preview.join(', ')}{extra}</span>
  }

  return (
    <Modal
      open={!!spec}
      onClose={onClose}
      title={`Replicar ${spec.campos.length === 1 ? `campo "${spec.campos[0]}"` : 'TUDO'}`}
      size="lg"
      footer={
        <>
          <Button variant="ghost" onClick={onClose}>Cancelar</Button>
          <Button variant="primary" onClick={aplicar} disabled={!destinos.length}>
            Replicar pra {destinos.length} célula{destinos.length !== 1 ? 's' : ''}
          </Button>
        </>
      }
    >
      {/* Banner Origem */}
      <div style={{
        marginBottom: 12,
        padding: '10px 14px',
        background: 'var(--accent-soft)',
        borderRadius: 'var(--radius-sm)',
        border: '1px solid var(--accent)',
        fontSize: 'var(--text-sm)'
      }}>
        <strong style={{ color: 'var(--accent)' }}>Origem:</strong>{' '}
        <code style={{ background: 'transparent', padding: 0 }}>
          {srcCanalLabel} — {srcDataLabel}
        </code>
      </div>

      {/* Campos a replicar */}
      <div style={{
        marginBottom: 12,
        padding: 10,
        background: 'var(--bg-card)',
        borderRadius: 'var(--radius-sm)',
        fontSize: 'var(--text-sm)'
      }}>
        <strong>Campos a replicar:</strong>{' '}
        {spec.campos.map(c => (
          <code key={c} style={{ marginRight: 8 }}>{c}</code>
        ))}
      </div>

      {/* Atalhos */}
      <div style={{ marginBottom: 12, display: 'flex', gap: 6, flexWrap: 'wrap' }}>
        <button className="mini-btn" onClick={presetTodosCanaisMesmaData}>
          ⤴ todos canais (mesma data)
        </button>
        <button className="mini-btn" onClick={presetMesmoCanalTodasDatas}>
          ⤵ mesmo canal (todas datas)
        </button>
      </div>

      <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 16 }}>
        {/* Datas */}
        <div>
          <div className="replicar-section-head">
            <span>Datas destino ({datasSel.size}/{linhas.length - 1})</span>
            <div style={{ display: 'flex', gap: 4 }}>
              <button className="mini-btn" onClick={selectAllDatas}>todas</button>
              <button className="mini-btn" onClick={clearDatas}>nenhuma</button>
            </div>
          </div>
          <div className="replicar-list">
            {linhas.map((l, i) => {
              const isSrc = i === srcRow
              return (
                <label key={i} className={`replicar-item ${isSrc ? 'replicar-src' : ''} ${datasSel.has(i) ? 'replicar-on' : ''}`}>
                  <input
                    type="checkbox"
                    checked={datasSel.has(i)}
                    onChange={() => toggleData(i)}
                    disabled={isSrc}
                  />
                  <span>{l.data}</span>
                  {isSrc && <span className="replicar-src-tag">origem</span>}
                </label>
              )
            })}
          </div>
        </div>

        {/* Canais */}
        <div>
          <div className="replicar-section-head">
            <span>Canais destino ({canaisSel.size}/{colunas.length - 1})</span>
            <div style={{ display: 'flex', gap: 4 }}>
              <button className="mini-btn" onClick={selectAllCanais}>todos</button>
              <button className="mini-btn" onClick={clearCanais}>nenhum</button>
            </div>
          </div>
          <div className="replicar-list">
            {colunas.map((c, i) => {
              const isSrc = i === srcCol
              return (
                <label key={i} className={`replicar-item ${isSrc ? 'replicar-src' : ''} ${canaisSel.has(i) ? 'replicar-on' : ''}`}>
                  <input
                    type="checkbox"
                    checked={canaisSel.has(i)}
                    onChange={() => toggleCanal(i)}
                    disabled={isSrc}
                  />
                  <span>{c.nome}</span>
                  {c.tipo === 'coringa' && <span className="replicar-base-tag">BASE</span>}
                  {isSrc && <span className="replicar-src-tag">origem</span>}
                </label>
              )
            })}
          </div>
        </div>
      </div>

      {/* Preview dos destinos resolvidos */}
      <div style={{
        marginTop: 12,
        padding: 10,
        background: 'var(--bg-card)',
        borderRadius: 'var(--radius-sm)',
        fontSize: 'var(--text-xs)',
        color: 'var(--text-secondary)'
      }}>
        <strong style={{ color: 'var(--text-primary)' }}>Será aplicado em:</strong>{' '}
        {renderDestinosPreview()}
      </div>

      <p style={{ marginTop: 8, fontSize: 'var(--text-xs)', color: 'var(--text-muted)' }}>
        💡 Sem nada selecionado em datas → mantém data atual da origem. Sem nada em canais → mantém canal da origem.
      </p>
    </Modal>
  )
}
