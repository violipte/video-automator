import './Input.css'

export function Input({ label, hint, error, type = 'text', size = 'md', ...props }) {
  return (
    <div className="input-wrap">
      {label && <label className="input-label">{label}</label>}
      <input
        type={type}
        className={`input input-${size} ${error ? 'input-error' : ''}`}
        {...props}
      />
      {hint && !error && <span className="input-hint">{hint}</span>}
      {error && <span className="input-error-msg">{error}</span>}
    </div>
  )
}

export function Textarea({ label, hint, error, rows = 4, ...props }) {
  return (
    <div className="input-wrap">
      {label && <label className="input-label">{label}</label>}
      <textarea
        rows={rows}
        className={`input input-textarea ${error ? 'input-error' : ''}`}
        {...props}
      />
      {hint && !error && <span className="input-hint">{hint}</span>}
      {error && <span className="input-error-msg">{error}</span>}
    </div>
  )
}

export function Select({ label, hint, options = [], ...props }) {
  return (
    <div className="input-wrap">
      {label && <label className="input-label">{label}</label>}
      <select className="input input-md" {...props}>
        {options.map(opt => (
          <option key={opt.value} value={opt.value}>{opt.label}</option>
        ))}
      </select>
      {hint && <span className="input-hint">{hint}</span>}
    </div>
  )
}
