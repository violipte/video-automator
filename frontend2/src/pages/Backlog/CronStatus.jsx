import { useEffect, useState } from 'react'
import { Card } from '../../components/Common/Card'
import { Button } from '../../components/Common/Button'
import { Badge } from '../../components/Common/Badge'
import { toast } from '../../components/Common/Toast'
import { api } from '../../api/client'

export function CronStatus() {
  const [status, setStatus] = useState(null)
  const [distStatus, setDistStatus] = useState(null)
  const [auto, setAuto] = useState(null)
  const [toggling, setToggling] = useState(false)

  async function refresh() {
    try {
      const [s, d, a] = await Promise.all([
        api.get('/api/coringa/status'),
        api.get('/api/coringa/dist-status'),
        api.get('/api/coringa/automacao'),
      ])
      setStatus(s); setDistStatus(d); setAuto(a)
    } catch (e) {}
  }

  useEffect(() => {
    refresh()
    const id = setInterval(refresh, 30000)
    return () => clearInterval(id)
  }, [])

  async function toggleAuto() {
    if (!auto) return
    const novo = !auto.habilitada
    if (novo && !confirm('Ligar a AUTOMAÇÃO BASE?\n\nA cada 15min:\n- Processa items Backlog → coluna BASE no grid\n- Distribui BASE pros canais Geral configurados (Claude CLI)\n\nContinuar?')) return
    if (!novo && !confirm('DESLIGAR a automação BASE?\n\nOs crons param de processar (botões manuais continuam).')) return
    setToggling(true)
    try {
      const r = await api.put('/api/coringa/automacao', { habilitada: novo })
      if (!r.ok) { toast.error('Erro ao alterar'); return }
      setAuto({ habilitada: r.habilitada })
      toast.success(r.habilitada ? '🟢 Automação LIGADA' : '🔴 Automação DESLIGADA')
    } catch (e) {
      toast.error('Erro: ' + e.message)
    } finally {
      setToggling(false)
    }
  }

  if (!auto || !status || !distStatus) {
    return (
      <Card padding="md" style={{ marginBottom: 16 }}>
        <div style={{ color: 'var(--text-muted)' }}>Carregando status…</div>
      </Card>
    )
  }

  const fmt = (ts) => ts ? ts.replace('T', ' ').slice(0, 16) : '—'
  const intMin = (sec) => Math.round((sec || 0) / 60)

  return (
    <Card padding="md" style={{ marginBottom: 20 }}>
      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: 16, flexWrap: 'wrap' }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 16, flexWrap: 'wrap' }}>
          <Button
            variant={auto.habilitada ? 'primary' : 'secondary'}
            onClick={toggleAuto}
            loading={toggling}
            size="md"
          >
            {auto.habilitada ? '🟢 Automação LIGADA' : '🔴 Automação DESLIGADA'}
          </Button>
          <span style={{ color: 'var(--text-muted)', fontSize: 'var(--text-xs)' }}>
            Click pra {auto.habilitada ? 'desligar' : 'ligar'}. Botões manuais funcionam independente.
          </span>
        </div>

        <div style={{ display: 'flex', gap: 12, fontSize: 'var(--text-xs)', color: 'var(--text-sec)', flexWrap: 'wrap' }}>
          <div>
            <Badge variant={status.rodando ? 'success' : 'default'} dot={status.rodando} size="sm">
              BASE
            </Badge>
            <span style={{ marginLeft: 8 }}>
              ({intMin(status.intervalo_seg)}min)
              {status.ultimo_ciclo && ` · último ${fmt(status.ultimo_ciclo.ts)}: ${status.ultimo_ciclo.processados} ok, ${status.ultimo_ciclo.erros} erros`}
            </span>
          </div>
          <div>
            <Badge variant={distStatus.rodando ? 'success' : 'default'} dot={distStatus.rodando} size="sm">
              DIST
            </Badge>
            <span style={{ marginLeft: 8 }}>
              ({intMin(distStatus.intervalo_seg)}min, delay {distStatus.delay_min}min)
              {distStatus.ultimo_ciclo && ` · último ${fmt(distStatus.ultimo_ciclo.ts)}: ${distStatus.ultimo_ciclo.linhas} linhas, ${distStatus.ultimo_ciclo.distribuidos} canais`}
            </span>
          </div>
        </div>
      </div>
    </Card>
  )
}
