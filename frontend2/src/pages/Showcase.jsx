import { useState } from 'react'
import { PageHeader } from '../components/Layout/AppShell'
import { Card, CardHeader, CardBody, CardFooter } from '../components/Common/Card'
import { Button } from '../components/Common/Button'
import { Badge } from '../components/Common/Badge'
import { Input, Textarea, Select } from '../components/Common/Input'
import { Modal } from '../components/Common/Modal'
import { Spinner } from '../components/Common/Spinner'
import { toast } from '../components/Common/Toast'

// Página de showcase pra você ver o design system funcionando.
// Será removida quando as páginas reais estiverem prontas.
export function Showcase() {
  const [open, setOpen] = useState(false)

  return (
    <>
      <PageHeader
        title="Design System"
        subtitle="Showcase dos componentes premium. Quando aprovado, removemos esta página."
        actions={
          <>
            <Button variant="ghost" onClick={() => toast('Toast info', 'info')}>Ghost</Button>
            <Button variant="secondary" onClick={() => toast.success('Salvo com sucesso')}>Secondary</Button>
            <Button variant="primary" onClick={() => setOpen(true)}>Abrir Modal</Button>
          </>
        }
      />

      <div style={{ display: 'grid', gap: 24 }}>
        {/* Botões */}
        <Card>
          <CardHeader>Botões</CardHeader>
          <CardBody>
            <div style={{ display: 'flex', gap: 12, flexWrap: 'wrap', alignItems: 'center', marginBottom: 16 }}>
              <Button size="sm" variant="primary">Primary sm</Button>
              <Button size="md" variant="primary">Primary md</Button>
              <Button size="lg" variant="primary">Primary lg</Button>
            </div>
            <div style={{ display: 'flex', gap: 12, flexWrap: 'wrap', alignItems: 'center', marginBottom: 16 }}>
              <Button variant="secondary">Secondary</Button>
              <Button variant="ghost">Ghost</Button>
              <Button variant="danger">Danger</Button>
              <Button variant="primary" disabled>Disabled</Button>
              <Button variant="primary" loading>Loading</Button>
            </div>
          </CardBody>
        </Card>

        {/* Badges */}
        <Card>
          <CardHeader>Badges</CardHeader>
          <CardBody>
            <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap', alignItems: 'center' }}>
              <Badge>Default</Badge>
              <Badge variant="success" dot>Em produção</Badge>
              <Badge variant="warning" dot>Pendente</Badge>
              <Badge variant="danger" dot>Erro</Badge>
              <Badge variant="info">Info</Badge>
              <Badge variant="gold">Premium</Badge>
              <Badge size="sm">Small</Badge>
              <Badge size="lg" variant="success">Large success</Badge>
            </div>
          </CardBody>
        </Card>

        {/* Inputs */}
        <Card>
          <CardHeader>Inputs</CardHeader>
          <CardBody>
            <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(280px, 1fr))', gap: 16 }}>
              <Input label="Texto" placeholder="Digite algo..." hint="Ex: nome do canal" />
              <Input label="Com erro" placeholder="..." error="Campo obrigatório" />
              <Select label="Select" options={[
                { value: 'a', label: 'Opção A' },
                { value: 'b', label: 'Opção B' },
              ]} />
              <Input label="Disabled" placeholder="Bloqueado" disabled />
            </div>
            <div style={{ marginTop: 16 }}>
              <Textarea label="Textarea" placeholder="Conteúdo..." rows={4} />
            </div>
          </CardBody>
        </Card>

        {/* Cards interativos */}
        <Card>
          <CardHeader>Cards interativos (canal mock)</CardHeader>
          <CardBody>
            <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(220px, 1fr))', gap: 16 }}>
              {['EN', 'DE', 'EN2', 'EN3'].map((canal) => (
                <Card key={canal} interactive>
                  <div style={{ aspectRatio: '16/9', background: 'var(--panel-2)', borderRadius: 'var(--radius-sm)', marginBottom: 12 }} />
                  <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 4 }}>
                    <strong style={{ fontSize: 'var(--text-md)' }}>{canal}</strong>
                    <Badge variant="success" dot>ativo</Badge>
                  </div>
                  <p style={{ fontSize: 'var(--text-xs)', color: 'var(--text-muted)' }}>
                    Whispers from Arcturus · Arcturian
                  </p>
                </Card>
              ))}
            </div>
          </CardBody>
        </Card>

        {/* Spinner */}
        <Card>
          <CardHeader>Loading states</CardHeader>
          <CardBody>
            <div style={{ display: 'flex', gap: 24, alignItems: 'center' }}>
              <Spinner size={14} />
              <Spinner size={20} />
              <Spinner size={28} color="var(--text-sec)" />
              <div className="skeleton" style={{ height: 18, width: 200 }} />
              <div className="skeleton" style={{ height: 32, width: 100, borderRadius: 'var(--radius)' }} />
            </div>
          </CardBody>
        </Card>

        {/* Toasts */}
        <Card>
          <CardHeader>Toasts</CardHeader>
          <CardBody>
            <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap' }}>
              <Button size="sm" onClick={() => toast.success('Sucesso!')}>Toast success</Button>
              <Button size="sm" onClick={() => toast.warning('Atenção')}>Toast warning</Button>
              <Button size="sm" onClick={() => toast.error('Erro fatal')}>Toast error</Button>
              <Button size="sm" onClick={() => toast.info('Info')}>Toast info</Button>
            </div>
          </CardBody>
        </Card>
      </div>

      <Modal
        open={open}
        onClose={() => setOpen(false)}
        title="Modal Premium"
        size="md"
        footer={
          <>
            <Button variant="ghost" onClick={() => setOpen(false)}>Cancelar</Button>
            <Button variant="primary" onClick={() => { toast.success('Confirmado'); setOpen(false) }}>Confirmar</Button>
          </>
        }
      >
        <p style={{ marginBottom: 16, color: 'var(--text-sec)', lineHeight: 'var(--lh-loose)' }}>
          Este é um modal com backdrop blur, animação de entrada com spring, sombra
          em multi-camadas, focus trap, e fechamento via Esc ou overlay.
        </p>
        <Input label="Exemplo de input" placeholder="Algo aqui" />
      </Modal>
    </>
  )
}
