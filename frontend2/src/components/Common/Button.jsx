import './Button.css'

export function Button({
  children,
  variant = 'secondary',
  size = 'md',
  type = 'button',
  disabled = false,
  loading = false,
  onClick,
  icon,
  ...props
}) {
  const className = `btn btn-${variant} btn-${size} ${loading ? 'btn-loading' : ''}`
  return (
    <button
      type={type}
      className={className}
      disabled={disabled || loading}
      onClick={onClick}
      {...props}
    >
      {icon && <span className="btn-icon">{icon}</span>}
      <span className="btn-label">{children}</span>
    </button>
  )
}
