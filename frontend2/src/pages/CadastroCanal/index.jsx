import { useEffect, useMemo, useState } from 'react'
import { api } from '../../api/client'
import './CadastroCanal.css'

// ============================================================
// CADASTRO DE CANAL — formulário de debut (28/07/2026)
// Cada campo alimenta um destino real: coluna do grid de Temas,
// templates.json, pipeline, style_card VidMator ou checklist.
// req: true = obrigatória pro debut (🔴). Demais têm default.
// ============================================================

const SECOES = [
  {
    titulo: '1. Identidade', campos: [
      { k: 'nome', l: 'Nome do canal (YouTube)', t: 'text', req: true },
      { k: 'handle', l: 'Handle/@ desejado', t: 'text', req: true },
      { k: 'sigla', l: 'Sigla/TAG do canal (ex.: EST, CO3 — vira nome da coluna e prefixo dos arquivos)', t: 'text', req: true },
      { k: 'idioma', l: 'Idioma', t: 'select', req: true, ops: ['EN', 'PT', 'DE', 'ES', 'outro'] },
    ],
  },
  {
    titulo: '2. Nicho', campos: [
      { k: 'nicho', l: 'Nicho principal', t: 'text', req: true },
      { k: 'tipo_a', l: 'Nicho de PRODUTO/MODELO (tipo A — carros, bikes, tênis)? B-roll travado no modelo exato', t: 'toggle', req: true },
    ],
  },
  {
    titulo: '3. Formato do Vídeo', campos: [
      { k: 'motor', l: 'Motor de edição', t: 'select', req: true, ops: ['simples (imagens+zoom)', 'vidmator (edição dinâmica)', 'híbrido'] },
      { k: 'estilo', l: 'Estilo (se vidmator/híbrido)', t: 'select', ops: ['v1 (limpo, aprovado)', 'v2 (trilha por momento + SFX + overlays)'] },
      { k: 'tier', l: 'Tier de footage do canal', t: 'select', req: true, ops: ['T1 — só stock', 'T2 — stock + CC/domínio público', 'T3 — web completo'] },
      { k: 'roteiro_chars', l: 'Tamanho do roteiro', t: 'select', req: true, ops: ['~8k chars (8-10min)', '~13k (12-15min)', '~23-26k (20-30min)', 'outro'] },
      { k: 'hook_tempo', l: 'Tempo do hook', t: 'select', req: true, ops: ['15s', '30s', '1min', '2min', 'custom'] },
      { k: 'abertura', l: 'Estilo de abertura', t: 'select', req: true, ops: ['com frase (cold-open quote + typewriter)', 'sem frase'] },
    ],
  },
  {
    titulo: '4. Voz & Narração', campos: [
      { k: 'voz_modo', l: 'Voz', t: 'select', req: true, ops: ['clonar voz nova (mandar ref 1-3min)', 'usar voz existente'] },
      { k: 'voz_nome', l: 'Nome/ID da voz (se existente — ex.: George, Brian)', t: 'text' },
      { k: 'voz_provider', l: 'Provider', t: 'select', req: true, ops: ['chatterbox (local, pool)', 'minimax_clone', 'inworld', 'ai33'] },
      { k: 'voz_speed', l: 'Speed (default 0.95)', t: 'text' },
    ],
  },
  {
    titulo: '5. Visual', campos: [
      { k: 'paleta', l: 'Paleta (2 cores hex ou mood — ex.: dourado/pedra)', t: 'text', req: true },
      { k: 'fonte', l: 'Fonte-tema', t: 'select', ops: ['default do motor', 'serif (clássico/história)', 'sans (moderno/tech)'] },
      { k: 'fundo', l: 'Fundo (motor simples): pasta de imagens OU arquivo de video loop; n/a se vidmator', t: 'text', req: true },
      { k: 'legenda', l: 'Legenda queimada', t: 'select', ops: ['não', 'sim — estilo 1', 'sim — estilo 2', 'sim — estilo 3', 'sim — estilo 4', 'sim — estilo 5'] },
      { k: 'thumb', l: 'Estilo de thumb (referência ou descrição)', t: 'textarea' },
      { k: 'moldura', l: 'Marca d’água/moldura (caminho ou vazio)', t: 'text' },
    ],
  },
  {
    titulo: '6. Áudio', campos: [
      { k: 'trilha_modo', l: 'Trilha', t: 'select', req: true, ops: ['pasta local (por momento, v2 automático)', 'pasta fixa no Drive', 'arquivo único'] },
      { k: 'trilha_detalhe', l: 'Pasta/arquivo da trilha (se fixa)', t: 'text' },
      { k: 'trilha_volume', l: 'Volume da trilha (default 0.08 v2 / padrão do template)', t: 'text' },
      { k: 'sfx', l: 'SFX/whoosh/transitions (v2)', t: 'select', ops: ['sim (default)', 'reduzido', 'não'] },
    ],
  },
  {
    titulo: '7. Monetização & CTA', campos: [
      { k: 'tem_produto', l: 'Tem produto/eBook pra pitch? (se sim, pitch OBRIGATÓRIO em todo vídeo — 3 gates)', t: 'toggle', req: true },
      { k: 'produto_nome', l: 'Nome do produto', t: 'text' },
      { k: 'link_destino', l: 'link_destino (URL do produto)', t: 'text' },
      { k: 'cta_overlay', l: 'CTA subscribe overlay', t: 'select', ops: ['default (30s, 8s, a cada 300s)', 'custom', 'não'] },
      { k: 'comentario_fixado', l: 'Comentário fixado padrão (com link?)', t: 'textarea' },
    ],
  },
  {
    titulo: '8. Infra & Contas', campos: [
      { k: 'conta_google', l: 'Conta Google (email)', t: 'text', req: true },
      { k: 'proxy_id', l: 'Proxy — ID no drive-to-youtube (canal logado SEMPRE no seu próprio proxy)', t: 'text', req: true },
      { k: 'oauth_feito', l: 'OAuth de upload (drive-to-youtube) já feito?', t: 'toggle' },
      { k: 'coluna_grid', l: 'Coluna no grid (nova — posição? ou substituir qual?)', t: 'text', req: true },
    ],
  },
  {
    titulo: '9. Descrições & Playlists', campos: [
      { k: 'descricao', l: 'Descrição do canal', t: 'textarea' },
      { k: 'descricao_videos', l: 'Descrição default dos vídeos (usada em todo upload; {{titulo}} disponível)', t: 'textarea' },
      { k: 'playlists', l: 'Playlists iniciais', t: 'text' },
    ],
  },
]

const OBRIGATORIOS = SECOES.flatMap((s) => s.campos.filter((c) => c.req).map((c) => c.k))

const preenchido = (v) => v !== undefined && v !== null && String(v).trim() !== '' && v !== false

export function CadastroCanal() {
  const [lista, setLista] = useState([])
  const [form, setForm] = useState({})
  const [salvando, setSalvando] = useState(false)
  const [msg, setMsg] = useState(null)

  const carregar = async () => {
    try { setLista(await api.get('/api/cadastros-canal')) } catch (e) { /* backend antigo sem endpoint */ }
  }
  useEffect(() => { carregar() }, [])

  const okCount = useMemo(
    () => OBRIGATORIOS.filter((k) => (k === 'tipo_a' || k === 'tem_produto')
      ? form[k] !== undefined : preenchido(form[k])).length,
    [form],
  )
  const completo = okCount === OBRIGATORIOS.length

  const salvar = async () => {
    if (!preenchido(form.nome)) { setMsg({ tipo: 'erro', txt: 'Preencha ao menos o nome do canal (1.1)' }); return }
    setSalvando(true)
    setMsg(null)
    try {
      const salvo = await api.post('/api/cadastros-canal', { ...form, status: completo ? 'completo' : 'rascunho' })
      setForm(salvo)
      await carregar()
      setMsg({ tipo: 'ok', txt: completo ? 'Salvo — cadastro COMPLETO, pronto pro debut ✔' : `Salvo como rascunho (${okCount}/${OBRIGATORIOS.length} obrigatórias)` })
    } catch (e) {
      setMsg({ tipo: 'erro', txt: `Erro ao salvar: ${e.message}` })
    } finally {
      setSalvando(false)
    }
  }

  const excluir = async (id) => {
    if (!window.confirm('Excluir este cadastro?')) return
    try {
      await api.delete(`/api/cadastros-canal/${id}`)
      if (form.id === id) setForm({})
      await carregar()
    } catch (e) { setMsg({ tipo: 'erro', txt: e.message }) }
  }

  const set = (k, v) => setForm((f) => ({ ...f, [k]: v }))

  return (
    <div className="cadcanal">
      <aside className="cadcanal-lista">
        <div className="cadcanal-lista-head">
          <h2>Canais</h2>
          <button className="cadcanal-btn" onClick={() => { setForm({}); setMsg(null) }}>+ Novo</button>
        </div>
        {lista.length === 0 && <div className="cadcanal-vazio">Nenhum cadastro ainda</div>}
        {lista.map((c) => (
          <div key={c.id} className={`cadcanal-item ${form.id === c.id ? 'ativo' : ''}`}
            onClick={() => { setForm(c); setMsg(null) }}>
            <div className="cadcanal-item-nome">{c.nome || '(sem nome)'}</div>
            <div className="cadcanal-item-meta">
              <span className={`cadcanal-pill ${c.status === 'completo' ? 'ok' : ''}`}>{c.status || 'rascunho'}</span>
              <button className="cadcanal-del" onClick={(e) => { e.stopPropagation(); excluir(c.id) }}>×</button>
            </div>
          </div>
        ))}
      </aside>

      <main className="cadcanal-form">
        <div className="cadcanal-head">
          <div>
            <h1>Cadastro de Canal</h1>
            <p className="cadcanal-sub">Preencha as obrigatórias (•) e o Claude gera: coluna no grid, template, pipeline, style_card e checklist de debut.</p>
          </div>
          <div className="cadcanal-progresso">
            <div className="cadcanal-prog-txt">{okCount}/{OBRIGATORIOS.length} obrigatórias</div>
            <div className="cadcanal-prog-bar"><div style={{ width: `${(okCount / OBRIGATORIOS.length) * 100}%` }} /></div>
          </div>
        </div>

        {SECOES.map((s) => (
          <section key={s.titulo} className="cadcanal-secao">
            <h3>{s.titulo}</h3>
            <div className="cadcanal-grid">
              {s.campos.map((c) => (
                <label key={c.k} className={`cadcanal-campo ${c.t === 'textarea' ? 'wide' : ''}`}>
                  <span className="cadcanal-label">{c.req && <em>•</em>} {c.l}</span>
                  {c.t === 'text' && (
                    <input value={form[c.k] || ''} onChange={(e) => set(c.k, e.target.value)} />
                  )}
                  {c.t === 'textarea' && (
                    <textarea rows={c.rows || 3} value={form[c.k] || ''} onChange={(e) => set(c.k, e.target.value)} />
                  )}
                  {c.t === 'select' && (
                    <select value={form[c.k] || ''} onChange={(e) => set(c.k, e.target.value)}>
                      <option value="">— escolher —</option>
                      {c.ops.map((o) => <option key={o} value={o}>{o}</option>)}
                    </select>
                  )}
                  {c.t === 'toggle' && (
                    <div className="cadcanal-toggle">
                      <button type="button" className={form[c.k] === true ? 'on' : ''} onClick={() => set(c.k, true)}>sim</button>
                      <button type="button" className={form[c.k] === false ? 'on' : ''} onClick={() => set(c.k, false)}>não</button>
                    </div>
                  )}
                </label>
              ))}
            </div>
          </section>
        ))}

        <div className="cadcanal-rodape">
          <div className="cadcanal-guardrails">
            Guardrails fixos (valem pra todo canal, não são pergunta): child safety absoluto · áudio 0% em footage ·
            identidades fictícias em social · retrato real só com fonte nomeada · tier desconhecido ⇒ T3
          </div>
          {msg && <div className={`cadcanal-msg ${msg.tipo}`}>{msg.txt}</div>}
          <button className="cadcanal-btn primario" disabled={salvando} onClick={salvar}>
            {salvando ? 'Salvando…' : form.id ? 'Salvar alterações' : 'Salvar cadastro'}
          </button>
        </div>
      </main>
    </div>
  )
}
