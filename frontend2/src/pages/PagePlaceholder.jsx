import { PageHeader } from '../components/Layout/AppShell'
import { Card } from '../components/Common/Card'

export function PagePlaceholder({ title, subtitle, description }) {
  return (
    <>
      <PageHeader title={title} subtitle={subtitle} />
      <Card padding="lg">
        <div style={{ textAlign: 'center', padding: '40px 20px' }}>
          <div style={{ fontSize: 36, marginBottom: 12, opacity: 0.4 }}>🚧</div>
          <h3 style={{ marginBottom: 8, fontSize: 'var(--text-md)', fontWeight: 'var(--fw-semi)' }}>
            Em construção
          </h3>
          <p style={{ color: 'var(--text-sec)', maxWidth: 480, margin: '0 auto' }}>
            {description || 'Esta página será migrada da UI antiga em breve.'}
          </p>
        </div>
      </Card>
    </>
  )
}
