import './Spinner.css'

export function Spinner({ size = 16, color }) {
  return (
    <span
      className="spinner"
      style={{ width: size, height: size, borderColor: color || 'var(--accent)', borderTopColor: 'transparent' }}
    />
  )
}
