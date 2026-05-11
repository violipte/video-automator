import './Card.css'

export function Card({ children, padding = 'md', interactive = false, className = '', ...props }) {
  const cls = `card card-pad-${padding} ${interactive ? 'card-interactive' : ''} ${className}`
  return <div className={cls} {...props}>{children}</div>
}

export function CardHeader({ children, action }) {
  return (
    <div className="card-header">
      <div className="card-header-title">{children}</div>
      {action && <div className="card-header-action">{action}</div>}
    </div>
  )
}

export function CardBody({ children }) {
  return <div className="card-body">{children}</div>
}

export function CardFooter({ children }) {
  return <div className="card-footer">{children}</div>
}
