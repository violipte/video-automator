import { useEffect, useState, useCallback, useMemo } from 'react'
import { PageHeader } from '../../components/Layout/AppShell'
import { Card, CardHeader, CardBody } from '../../components/Common/Card'
import { Button } from '../../components/Common/Button'
import { Badge } from '../../components/Common/Badge'
import { Input, Select } from '../../components/Common/Input'
import { Modal } from '../../components/Common/Modal'
import { Spinner } from '../../components/Common/Spinner'
import { toast } from '../../components/Common/Toast'
import { api } from '../../api/client'
import { CronStatus } from './CronStatus'
import { ConfigCoringaModal } from './ConfigCoringaModal'
import './Backlog.css'

export function Backlog() {
  const [items, setItems] = useState([])
  const [loading, setLoading] = useState(true)
  const [filter, setFilter] = useState('pendentes')
  const [searchTitulo, setSearchTitulo] = useState('')
  const [searchThumb, setSearchThumb] = useState('')
  const [linkInput, setLinkInput] = useState('')
  const [tituloInput, setTituloInput] = useState('')
  const [thumbInput, setThumbInput] = useState('')
  const [adding, setAdding] = useState(false)
  const [configOpen, setConfigOpen] = useState(false)

  // Filtra localmente por titulo E thumb separadamente (AND quando ambos preenchidos)
  const filteredItems = useMemo(() => {
    const qT = searchTitulo.trim().toLowerCase()
    const qH = searchThumb.trim().toLowerCase()
    if (!qT && !qH) return items
    return items.filter(it => {
      const titulo = (it.titulo || '').toLowerCase()
      const thumb = (it.texto_thumb || '').toLowerCase()
      if (qT && !titulo.includes(qT)) return false
      if (qH && !thumb.includes(qH)) return false
      return true
    })
  }, [items, searchTitulo, searchThumb])

  const hasSearch = !!(searchTitulo.trim() || searchThumb.trim())

  const load = useCallback(async () => {
    setLoading(true)
    try {
      let qs = ''
      if (filter === 'pendentes') qs = '?incluir_concluidos=false'
      else if (filter === 'concluidos') qs = '?geral=Ok&co=Ok'
      const r = await api.get('/api/backlog' + qs)
      setItems(r.itens || [])
    } catch (e) {
      toast.error('Erro ao carregar: ' + e.message)
    } finally {
      setLoading(false)
    }
  }, [filter])

  useEffect(() => { load() }, [load])

  async function addItem() {
    const link = linkInput.trim()
    const titulo = tituloInput.trim()
    if (!link && !titulo) {
      toast.warning('Cole um link YT OU digite um título manual')
      return
    }
    setAdding(true)
    try {
      const r = await api.post('/api/backlog', {
        link,
        titulo: tituloInput,
        texto_thumb: thumbInput,
      })
      if (!r.ok) { toast.error(r.erro || 'Erro'); return }
      let msg = link ? `Adicionado: ${r.item.data}` : `Tema manual adicionado: ${r.item.data}`
      if ((r.enrich_aplicado || []).length) msg += ` (auto: ${r.enrich_aplicado.join(', ')})`
      toast.success(msg)
      setLinkInput(''); setTituloInput(''); setThumbInput('')
      load()
    } catch (e) {
      toast.error('Erro: ' + e.message)
    } finally {
      setAdding(false)
    }
  }

  async function updateField(id, campo, valor) {
    const item = items.find(x => x.id === id)
    if (item && (item[campo] || '') === (valor || '')) return
    try {
      const r = await api.put(`/api/backlog/${id}`, { [campo]: valor })
      if (!r.ok) { toast.error(r.erro || 'Erro'); load(); return }
      setItems(prev => prev.map(it => it.id === id ? { ...it, [campo]: r.item?.[campo] ?? valor } : it))
    } catch (e) {
      toast.error('Erro: ' + e.message)
    }
  }

  async function toggleStatus(id, campo) {
    const item = items.find(x => x.id === id)
    if (!item) return
    const novo = item[campo] === 'Ok' ? '' : 'Ok'
    try {
      const r = await api.put(`/api/backlog/${id}`, { [campo]: novo })
      if (!r.ok) { toast.error(r.erro || 'Erro'); return }
      setItems(prev => prev.map(it => it.id === id ? { ...it, [campo]: novo } : it))
    } catch (e) {
      toast.error('Erro: ' + e.message)
    }
  }

  async function deleteItem(id) {
    if (!confirm('Remover este item do backlog?')) return
    try {
      const r = await api.delete(`/api/backlog/${id}`)
      if (!r.ok) { toast.error(r.erro || 'Erro'); return }
      setItems(prev => prev.filter(x => x.id !== id))
      toast.success('Item removido')
    } catch (e) {
      toast.error('Erro: ' + e.message)
    }
  }

  async function reenriquecer(id) {
    try {
      const r = await api.post(`/api/backlog/${id}/reenriquecer`)
      if (!r.ok) { toast.error(r.erro || 'Erro'); return }
      toast.success(`Re-extraído: ${(r.campos || []).join(', ')}`)
      load()
    } catch (e) {
      toast.error('Erro: ' + e.message)
    }
  }

  // Botões manuais
  async function processarBaseAgora(btn) {
    btn.disabled = true
    try {
      const r = await api.post('/api/coringa/processar-agora')
      if (!r.ok) { toast.error(r.erro || 'Erro'); return }
      toast(`BASE: ${r.processados} ok, ${r.erros} erros`, r.erros ? 'warning' : 'success')
      load()
    } catch (e) {
      toast.error('Erro: ' + e.message)
    } finally { btn.disabled = false }
  }

  async function distribuirAgora(btn) {
    if (!confirm('Distribuir agora (ignora delay)?\n\nVai chamar Claude CLI/API pra cada canal Geral configurado de cada BASE pendente. Pode demorar alguns minutos.')) return
    btn.disabled = true
    try {
      const r = await api.post('/api/coringa/distribuir-agora')
      if (!r.ok) { toast.error(r.erro || 'Erro'); return }
      let msg = `Distribuição: ${r.linhas} linha(s), ${r.distribuidos} canais OK`
      if (r.erros) msg += `, ${r.erros} erros`
      toast(msg, r.erros ? 'warning' : 'success')
    } catch (e) {
      toast.error('Erro: ' + e.message)
    } finally { btn.disabled = false }
  }

  async function processarCoAgora(btn) {
    if (!confirm("CO* em cruz: pega 4 itens do Backlog (mais antigos sem co=Ok), preenche CO1/CO2/CO3/CO4 em 2 datas (em cruz) + NARC e NPD adaptado via Claude. Continuar?")) return
    btn.disabled = true
    try {
      const r = await api.post('/api/coringa/processar-co-agora')
      if (!r.ok) { toast.error(r.erro || 'Erro'); return }
      const erros = (r.cascade_erros || []).length
      toast(`CO* em cruz: Dia X=${r.dia_x}, Dia X+1=${r.dia_x1}, ${r.cascade_ok}/4 cascades OK`, erros ? 'warning' : 'success')
      load()
    } catch (e) {
      toast.error('Erro: ' + e.message)
    } finally { btn.disabled = false }
  }

  return (
    <>
      <PageHeader
        title="Backlog Temas"
        subtitle="Cole links do YT — sistema enriquece (oEmbed + OCR), distribui pros canais, gera vídeos."
        actions={
          <>
            <Button variant="ghost" onClick={() => setConfigOpen(true)}>⚙ Config BASE</Button>
            <Button variant="primary" onClick={(e) => processarBaseAgora(e.currentTarget)}>⚡ BASE agora</Button>
            <Button variant="secondary" onClick={(e) => distribuirAgora(e.currentTarget)}>📤 Distribuir</Button>
            <Button variant="secondary" onClick={(e) => processarCoAgora(e.currentTarget)}>🔄 CO* em cruz</Button>
          </>
        }
      />

      <CronStatus />

      <Card style={{ marginBottom: 20 }}>
        <CardHeader>Adicionar item</CardHeader>
        <CardBody>
          <p style={{ fontSize: 'var(--text-xs)', color: 'var(--text-muted)', marginBottom: 12 }}>
            Cole um link YT (sistema enriquece via oEmbed + OCR) <strong>ou</strong> digite só um título manual (tema sem referência).
          </p>
          <div className="backlog-add-row">
            <Input
              placeholder="Link YT (opcional se digitar título)"
              value={linkInput}
              onChange={(e) => setLinkInput(e.target.value)}
              onKeyDown={(e) => e.key === 'Enter' && addItem()}
              style={{ flex: 2, minWidth: 240 }}
            />
            <Input
              placeholder="Título (manual ou auto via oEmbed)"
              value={tituloInput}
              onChange={(e) => setTituloInput(e.target.value)}
              onKeyDown={(e) => e.key === 'Enter' && addItem()}
              style={{ flex: 2, minWidth: 200 }}
            />
            <Input
              placeholder="Texto thumb (opcional)"
              value={thumbInput}
              onChange={(e) => setThumbInput(e.target.value)}
              onKeyDown={(e) => e.key === 'Enter' && addItem()}
              style={{ flex: 1, minWidth: 140 }}
            />
            <Button variant="primary" onClick={addItem} loading={adding}>+ Adicionar</Button>
          </div>
        </CardBody>
      </Card>

      <div className="backlog-toolbar">
        <Select
          value={filter}
          onChange={(e) => setFilter(e.target.value)}
          options={[
            { value: 'todos', label: 'Todos' },
            { value: 'pendentes', label: 'Pendentes' },
            { value: 'concluidos', label: 'Concluídos' },
          ]}
        />
        <div style={{ position: 'relative', display: 'inline-flex', alignItems: 'center', flex: 1, minWidth: 180, maxWidth: 280 }}>
          <Input
            placeholder="🔎 Buscar título..."
            value={searchTitulo}
            onChange={(e) => setSearchTitulo(e.target.value)}
            style={{ width: '100%', paddingRight: searchTitulo ? 32 : 12 }}
          />
          {searchTitulo && (
            <button
              type="button"
              onClick={() => setSearchTitulo('')}
              title="Limpar busca de título"
              style={{ position: 'absolute', right: 6, background: 'transparent', border: 'none', color: 'var(--text-sec)', cursor: 'pointer', fontSize: 14, padding: '2px 6px' }}
            >✕</button>
          )}
        </div>
        <div style={{ position: 'relative', display: 'inline-flex', alignItems: 'center', flex: 1, minWidth: 180, maxWidth: 280 }}>
          <Input
            placeholder="🖼 Buscar thumb..."
            value={searchThumb}
            onChange={(e) => setSearchThumb(e.target.value)}
            style={{ width: '100%', paddingRight: searchThumb ? 32 : 12 }}
          />
          {searchThumb && (
            <button
              type="button"
              onClick={() => setSearchThumb('')}
              title="Limpar busca de thumb"
              style={{ position: 'absolute', right: 6, background: 'transparent', border: 'none', color: 'var(--text-sec)', cursor: 'pointer', fontSize: 14, padding: '2px 6px' }}
            >✕</button>
          )}
        </div>
        <Button variant="ghost" size="sm" onClick={load}>↻ Atualizar</Button>
        <span className="backlog-count">
          {hasSearch
            ? `${filteredItems.length}/${items.length} item${items.length !== 1 ? 's' : ''}`
            : `${items.length} item${items.length !== 1 ? 's' : ''}`}
        </span>
      </div>

      <BacklogTable
        items={filteredItems}
        totalItems={items.length}
        searchTitulo={searchTitulo}
        searchThumb={searchThumb}
        loading={loading}
        onUpdate={updateField}
        onToggle={toggleStatus}
        onDelete={deleteItem}
        onReenriquecer={reenriquecer}
      />

      <ConfigCoringaModal open={configOpen} onClose={() => setConfigOpen(false)} />
    </>
  )
}

function BacklogTable({ items, totalItems, searchTitulo, searchThumb, loading, onUpdate, onToggle, onDelete, onReenriquecer }) {
  if (loading && !items.length) {
    return (
      <Card padding="lg">
        <div style={{ textAlign: 'center', padding: 40 }}>
          <Spinner size={20} />
          <div style={{ marginTop: 12, color: 'var(--text-sec)' }}>Carregando…</div>
        </div>
      </Card>
    )
  }

  if (!items.length) {
    // Diferencia "lista vazia" de "busca sem match"
    const qT = (searchTitulo || '').trim()
    const qH = (searchThumb || '').trim()
    const hasSearch = (qT || qH) && totalItems > 0
    let msg = 'Nenhum item. Cole um link YT acima pra adicionar.'
    if (hasSearch) {
      const partes = []
      if (qT) partes.push(`título="${qT}"`)
      if (qH) partes.push(`thumb="${qH}"`)
      msg = `Nenhum item bate com ${partes.join(' + ')} (de ${totalItems} no total).`
    }
    return (
      <Card padding="lg">
        <div style={{ textAlign: 'center', padding: 40, color: 'var(--text-sec)' }}>
          {msg}
        </div>
      </Card>
    )
  }

  return (
    <Card padding="none" className="backlog-table-wrap">
      <table className="backlog-table">
        <thead>
          <tr>
            <th style={{ width: 110 }}>Data</th>
            <th style={{ width: 70, textAlign: 'center' }}>Geral</th>
            <th style={{ width: 70, textAlign: 'center' }}>CO*</th>
            <th>Título</th>
            <th style={{ width: 220 }}>Texto thumb</th>
            <th style={{ width: 90, textAlign: 'center' }}>Link</th>
            <th style={{ width: 80 }}></th>
          </tr>
        </thead>
        <tbody>
          {items.map(it => (
            <BacklogRow
              key={it.id}
              item={it}
              onUpdate={onUpdate}
              onToggle={onToggle}
              onDelete={onDelete}
              onReenriquecer={onReenriquecer}
            />
          ))}
        </tbody>
      </table>

      {/* Mobile cards */}
      <div className="backlog-cards">
        {items.map(it => (
          <BacklogCard
            key={it.id}
            item={it}
            onUpdate={onUpdate}
            onToggle={onToggle}
            onDelete={onDelete}
            onReenriquecer={onReenriquecer}
          />
        ))}
      </div>
    </Card>
  )
}

function BacklogRow({ item: it, onUpdate, onToggle, onDelete, onReenriquecer }) {
  const isOk = it.geral === 'Ok'
  const isCoOk = it.co === 'Ok'
  const done = isOk && isCoOk

  return (
    <tr style={{ opacity: done ? 0.5 : 1 }}>
      <td>
        <input
          className="cell-input"
          defaultValue={it.data || ''}
          onBlur={(e) => onUpdate(it.id, 'data', e.target.value)}
        />
      </td>
      <td style={{ textAlign: 'center' }}>
        <button
          className={`status-toggle ${isOk ? 'on' : ''}`}
          onClick={() => onToggle(it.id, 'geral')}
        >
          {isOk ? 'Ok' : '—'}
        </button>
      </td>
      <td style={{ textAlign: 'center' }}>
        <button
          className={`status-toggle ${isCoOk ? 'on' : ''}`}
          onClick={() => onToggle(it.id, 'co')}
        >
          {isCoOk ? 'Ok' : '—'}
        </button>
      </td>
      <td>
        <input
          className="cell-input"
          defaultValue={it.titulo || ''}
          onBlur={(e) => onUpdate(it.id, 'titulo', e.target.value)}
        />
      </td>
      <td>
        <input
          className="cell-input"
          defaultValue={it.texto_thumb || ''}
          onBlur={(e) => onUpdate(it.id, 'texto_thumb', e.target.value)}
          placeholder="—"
        />
      </td>
      <td style={{ textAlign: 'center' }}>
        {it.link ? (
          <a href={it.link} target="_blank" rel="noopener" className="link-pill">▶ YT</a>
        ) : (
          <span className="link-pill" style={{ opacity: 0.4, cursor: 'default' }} title="Tema manual (sem link)">manual</span>
        )}
      </td>
      <td style={{ textAlign: 'center', whiteSpace: 'nowrap' }}>
        {it.link && <button className="row-action" onClick={() => onReenriquecer(it.id)} title="Re-extrair título + thumb">↻</button>}
        <button className="row-action danger" onClick={() => onDelete(it.id)} title="Remover">×</button>
      </td>
    </tr>
  )
}

function BacklogCard({ item: it, onUpdate, onToggle, onDelete, onReenriquecer }) {
  const isOk = it.geral === 'Ok'
  const isCoOk = it.co === 'Ok'
  const done = isOk && isCoOk

  return (
    <div className="backlog-card" style={{ opacity: done ? 0.5 : 1 }}>
      <div className="backlog-card-head">
        <input
          className="cell-input"
          defaultValue={it.data || ''}
          onBlur={(e) => onUpdate(it.id, 'data', e.target.value)}
          style={{ width: 110, fontWeight: 600 }}
        />
        <div style={{ display: 'flex', gap: 6 }}>
          {it.link && <button className="row-action" onClick={() => onReenriquecer(it.id)}>↻</button>}
          <button className="row-action danger" onClick={() => onDelete(it.id)}>×</button>
        </div>
      </div>
      <input
        className="cell-input"
        defaultValue={it.titulo || ''}
        onBlur={(e) => onUpdate(it.id, 'titulo', e.target.value)}
        placeholder="Título"
        style={{ marginBottom: 6 }}
      />
      <input
        className="cell-input"
        defaultValue={it.texto_thumb || ''}
        onBlur={(e) => onUpdate(it.id, 'texto_thumb', e.target.value)}
        placeholder="Texto thumb"
        style={{ marginBottom: 10 }}
      />
      <div style={{ display: 'flex', gap: 6 }}>
        {it.link ? (
          <a href={it.link} target="_blank" rel="noopener" className="link-pill" style={{ flex: 1 }}>▶ YouTube</a>
        ) : (
          <span className="link-pill" style={{ flex: 1, opacity: 0.4 }}>tema manual (sem link)</span>
        )}
        <button
          className={`status-toggle ${isOk ? 'on' : ''}`}
          onClick={() => onToggle(it.id, 'geral')}
          style={{ flex: 1 }}
        >Geral: {isOk ? 'Ok' : '—'}</button>
        <button
          className={`status-toggle ${isCoOk ? 'on' : ''}`}
          onClick={() => onToggle(it.id, 'co')}
          style={{ flex: 1 }}
        >CO*: {isCoOk ? 'Ok' : '—'}</button>
      </div>
    </div>
  )
}
