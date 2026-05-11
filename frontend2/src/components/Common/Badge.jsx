import './Badge.css'

const VARIANTS = ['default', 'success', 'warning', 'danger', 'info', 'gold']

export function Badge({ children, variant = 'default', dot = false, size = 'md' }) {
  const v = VARIANTS.includes(variant) ? variant : 'default'
  return (
    <span className={`badge badge-${v} badge-${size}`}>
      {dot && <span className="badge-dot" />}
      {children}
    </span>
  )
}
