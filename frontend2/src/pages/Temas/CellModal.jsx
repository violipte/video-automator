import { useEffect, useState } from 'react'
import { Modal } from '../../components/Common/Modal'
import { Button } from '../../components/Common/Button'
import { Badge } from '../../components/Common/Badge'
import { ReplicarModal } from './ReplicarModal'

const FIELDS = [
  { key: 'tema',   label: 'Tema do Vídeo',     hint: 'Gancho central',           color: 'tema' },
  { key: 'titulo', label: 'Título do Vídeo',   hint: 'Título do YouTube',        color: 'titulo' },
  { key: 'thumb',  label: 'Texto da Thumbnail', hint: 'Texto sobre a thumb',      color: 'thumb' },
]

export function CellModal({ editing, cel, temas, onClose, onSave }) {
  const [tema, setTema] = useState('')
  const [titulo, setTitulo] = useState('')
  const [thumb, setThumb] = useState('')
  const [replicar, setReplicar] = useState(null) // { campos: ['tema'|'titulo'|'thumb'], valores: {...} }

  useEffect(() => {
    if (!editing) return
    setTema(cel.tema || '')
    setTitulo(cel.titulo || '')
    setThumb(cel.thumb || '')
  }, [editing])

  if (!editing) return null

  const values = { tema, titulo, thumb }
  const setters = { tema: setTema, titulo: setTitulo, thumb: setThumb }

  function applyCase(field, mode) {
    const v = values[field] || ''
    let novo = v
    if (mode === 'upper') novo = v.toUpperCase()
    else if (mode === 'lower') novo = v.toLowerCase()
    else if (mode === 'title') novo = toTitleCase(v)
    setters[field](novo)
  }

  async function salvar() {
    const key = `${editing.row}_${editing.col}`
    await onSave({ [key]: { tema, titulo, thumb } })
  }

  function abrirReplicar(field) {
    setReplicar({ campos: [field], valores: { [field]: values[field] } })
  }

  function abrirReplicarTudo() {
    setReplicar({ campos: ['tema', 'titulo', 'thumb'], valores: { tema, titulo, thumb } })
  }

  return (
    <>
      <Modal
        open={!!editing}
        onClose={onClose}
        title={
          <span>
            <strong>{editing.colName}</strong>
            <span style={{ color: 'var(--text-muted)', marginLeft: 8, fontWeight: 400 }}>— {editing.data}</span>
            {editing.isBase && <Badge variant="gold" size="sm" style={{ marginLeft: 8 }}>BASE</Badge>}
          </span>
        }
        size="md"
        footer={
          <>
            <Button variant="ghost" onClick={onClose}>Cancelar</Button>
            <Button variant="secondary" onClick={abrirReplicarTudo}>Replicar Tudo →</Button>
            <Button variant="primary" onClick={salvar}>Salvar</Button>
          </>
        }
      >
        {FIELDS.map(f => (
          <FieldEditor
            key={f.key}
            field={f}
            value={values[f.key]}
            onChange={setters[f.key]}
            onCase={(mode) => applyCase(f.key, mode)}
            onReplicar={() => abrirReplicar(f.key)}
          />
        ))}
      </Modal>

      <ReplicarModal
        spec={replicar}
        srcRow={editing.row}
        srcCol={editing.col}
        temas={temas}
        onClose={() => setReplicar(null)}
        onApply={async (updates) => {
          // updates = { "ri_ci": {campo: valor, ...}, ... }
          // Aplica primeiro o salvamento da própria célula + replicação
          const key = `${editing.row}_${editing.col}`
          const merged = { [key]: { tema, titulo, thumb }, ...updates }
          await onSave(merged)
          setReplicar(null)
        }}
      />
    </>
  )
}

function FieldEditor({ field, value, onChange, onCase, onReplicar }) {
  const chars = (value || '').length
  return (
    <div className="cell-field">
      <label className={`cell-field-label cell-field-label-${field.color}`}>
        {field.label}
      </label>
      <textarea
        className={`cell-field-textarea cell-field-textarea-${field.color}`}
        value={value}
        onChange={(e) => onChange(e.target.value)}
        rows={3}
      />
      <div className="cell-field-meta">
        <span className="cell-field-hint">
          {field.hint} <strong className="cell-field-chars">{chars} chars</strong>
        </span>
        <div className="cell-field-actions">
          <button className="case-btn" onClick={() => onCase('upper')} title="ALL CAPS">AA</button>
          <button className="case-btn" onClick={() => onCase('lower')} title="lowercase">aa</button>
          <button className="case-btn" onClick={() => onCase('title')} title="Title Case">Aa</button>
          <button className="replicar-btn" onClick={onReplicar} title="Replicar este campo">
            Replicar →
          </button>
        </div>
      </div>
    </div>
  )
}

function toTitleCase(str) {
  // Stop words (lowercase exceto se for primeira palavra)
  const STOP = new Set(['a', 'an', 'the', 'and', 'or', 'but', 'of', 'in', 'on', 'at', 'to', 'for', 'with', 'as', 'by', 'is', 'do', 'da', 'de', 'do', 'das', 'dos', 'em', 'por', 'para', 'com', 'e', 'ou'])
  const words = str.toLowerCase().split(/\s+/)
  return words.map((w, i) => {
    if (i > 0 && STOP.has(w)) return w
    return w.charAt(0).toUpperCase() + w.slice(1)
  }).join(' ')
}
