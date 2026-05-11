import { useEffect, useRef, useState } from 'react'
import { Modal } from '../Common/Modal'
import { Button } from '../Common/Button'
import { Spinner } from '../Common/Spinner'
import { toast } from '../Common/Toast'
import { api } from '../../api/client'
import './ChatPanel.css'

// Painel de chat com Claude (via API direta, /api/chat com agent={agent}).
// Usado em sidebar flutuante na aba Temas (e onde mais quisermos integrar).
//
// Props:
//   agent       — string, nome do agente (corresponde a agents/{agent}/CLAUDE.md no servidor)
//   title       — string, titulo exibido no header (ex: "Claude — Títulos")
//   placeholder — string, placeholder do input
//   open        — boolean, controla se o panel está aberto
//   onClose     — function, fecha o panel
//
export function ChatPanel({ agent, title, placeholder, open, onClose }) {
  const [historico, setHistorico] = useState([])
  const [input, setInput] = useState('')
  const [enviando, setEnviando] = useState(false)
  const [carregando, setCarregando] = useState(false)
  const [editingInstr, setEditingInstr] = useState(null) // string ou null
  const messagesEndRef = useRef(null)

  // Scroll auto pro final quando historico atualiza
  useEffect(() => {
    if (messagesEndRef.current) {
      messagesEndRef.current.scrollTop = messagesEndRef.current.scrollHeight
    }
  }, [historico, enviando])

  // Carrega historico ao abrir
  useEffect(() => {
    if (!open) return
    setCarregando(true)
    api.get(`/api/chat/history?agent=${encodeURIComponent(agent)}`)
      .then(d => setHistorico(Array.isArray(d) ? d : []))
      .catch(e => {
        console.error('chat history erro:', e)
        toast.error('Não consegui carregar o histórico do chat')
      })
      .finally(() => setCarregando(false))
  }, [open, agent])

  async function enviar() {
    const prompt = input.trim()
    if (!prompt || enviando) return
    setEnviando(true)
    // Optimistic: adiciona msg do user no historico local
    const userMsg = { role: 'user', text: prompt, ts: new Date().toISOString() }
    setHistorico(h => [...h, userMsg])
    setInput('')
    try {
      const resp = await api.post('/api/chat', { prompt, agent })
      const assistantMsg = { role: 'assistant', text: resp.resposta || '', ts: new Date().toISOString() }
      setHistorico(h => [...h, assistantMsg])
    } catch (e) {
      console.error('chat erro:', e)
      const errMsg = { role: 'assistant', text: `Erro: ${e.message}`, ts: new Date().toISOString(), erro: true }
      setHistorico(h => [...h, errMsg])
      toast.error('Erro ao falar com Claude: ' + e.message)
    } finally {
      setEnviando(false)
    }
  }

  async function limparHistorico() {
    if (!confirm('Limpar todo o histórico do chat?')) return
    try {
      await api.delete(`/api/chat/history?agent=${encodeURIComponent(agent)}`)
      setHistorico([])
      toast.success('Histórico limpo')
    } catch (e) {
      toast.error('Erro: ' + e.message)
    }
  }

  async function abrirEditarInstrucoes() {
    try {
      const d = await api.get(`/api/chat/instructions?agent=${encodeURIComponent(agent)}`)
      setEditingInstr(d.instrucoes || '')
    } catch (e) {
      toast.error('Erro ao carregar instruções: ' + e.message)
    }
  }

  async function salvarInstrucoes() {
    if (editingInstr == null) return
    try {
      await api.put('/api/chat/instructions', { agent, instrucoes: editingInstr })
      toast.success('Instruções salvas')
      setEditingInstr(null)
    } catch (e) {
      toast.error('Erro: ' + e.message)
    }
  }

  function onInputKeyDown(e) {
    // Enter sozinho envia. Shift+Enter = nova linha.
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault()
      enviar()
    }
  }

  if (!open) return null

  return (
    <>
      <aside className="chat-panel" role="complementary">
        <header className="chat-header">
          <div className="chat-title">
            <span className="chat-status-dot" />
            <strong>{title || `Claude — ${agent}`}</strong>
          </div>
          <div className="chat-header-actions">
            <button className="chat-icon-btn" onClick={abrirEditarInstrucoes} title="Editar instruções (CLAUDE.md)">⚙</button>
            <button className="chat-icon-btn" onClick={limparHistorico} title="Limpar histórico">🗑</button>
            <button className="chat-icon-btn" onClick={onClose} title="Fechar">✕</button>
          </div>
        </header>

        <div className="chat-messages" ref={messagesEndRef}>
          {carregando && (
            <div style={{ textAlign: 'center', padding: 20 }}><Spinner size={16} /></div>
          )}
          {!carregando && historico.length === 0 && (
            <div className="chat-empty">
              Sem mensagens ainda.<br />
              Mande algo pro Claude — ele tá usando as instruções de <code>agents/{agent}/CLAUDE.md</code>.
            </div>
          )}
          {historico.map((m, i) => (
            <div key={i} className={`chat-msg chat-msg-${m.role} ${m.erro ? 'chat-msg-erro' : ''}`}>
              <div className="chat-msg-role">{m.role === 'user' ? 'Você' : 'Claude'}</div>
              <div className="chat-msg-text">{m.text}</div>
            </div>
          ))}
          {enviando && (
            <div className="chat-msg chat-msg-assistant chat-msg-typing">
              <div className="chat-msg-role">Claude</div>
              <div className="chat-msg-text"><Spinner size={12} /> pensando…</div>
            </div>
          )}
        </div>

        <form
          className="chat-input-row"
          onSubmit={(e) => { e.preventDefault(); enviar() }}
        >
          <textarea
            className="chat-input"
            value={input}
            onChange={(e) => setInput(e.target.value)}
            onKeyDown={onInputKeyDown}
            placeholder={placeholder || 'Pergunte algo…'}
            rows={2}
            disabled={enviando}
          />
          <Button type="submit" variant="primary" disabled={enviando || !input.trim()}>
            {enviando ? '...' : 'Enviar'}
          </Button>
        </form>
      </aside>

      <Modal
        open={editingInstr != null}
        onClose={() => setEditingInstr(null)}
        title={`Instruções — ${title || agent}`}
        size="lg"
        footer={
          <>
            <Button variant="ghost" onClick={() => setEditingInstr(null)}>Cancelar</Button>
            <Button variant="primary" onClick={salvarInstrucoes}>Salvar</Button>
          </>
        }
      >
        <p style={{ fontSize: 'var(--text-xs)', color: 'var(--text-muted)', marginBottom: 8 }}>
          Conteúdo de <code>agents/{agent}/CLAUDE.md</code>. Editar aqui afeta como o Claude responde nas próximas mensagens.
        </p>
        <textarea
          className="chat-instr-textarea"
          value={editingInstr || ''}
          onChange={(e) => setEditingInstr(e.target.value)}
          rows={20}
          spellCheck={false}
        />
      </Modal>
    </>
  )
}
