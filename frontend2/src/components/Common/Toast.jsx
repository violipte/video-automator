import { create } from 'zustand'
import { useEffect } from 'react'
import './Toast.css'

let nextId = 1

export const useToastStore = create((set) => ({
  toasts: [],
  push: (toast) => {
    const id = nextId++
    set((s) => ({ toasts: [...s.toasts, { id, ...toast }] }))
    return id
  },
  remove: (id) => set((s) => ({ toasts: s.toasts.filter(t => t.id !== id) })),
}))

export function toast(message, variant = 'info', duration = 4000) {
  const id = useToastStore.getState().push({ message, variant })
  if (duration > 0) {
    setTimeout(() => useToastStore.getState().remove(id), duration)
  }
  return id
}

toast.success = (msg, dur) => toast(msg, 'success', dur)
toast.error   = (msg, dur) => toast(msg, 'danger', dur)
toast.warning = (msg, dur) => toast(msg, 'warning', dur)
toast.info    = (msg, dur) => toast(msg, 'info', dur)

export function ToastContainer() {
  const toasts = useToastStore(s => s.toasts)
  const remove = useToastStore(s => s.remove)

  return (
    <div className="toast-container" role="status" aria-live="polite">
      {toasts.map(t => (
        <ToastItem key={t.id} toast={t} onClose={() => remove(t.id)} />
      ))}
    </div>
  )
}

function ToastItem({ toast, onClose }) {
  return (
    <div className={`toast toast-${toast.variant}`}>
      <span>{toast.message}</span>
      <button className="toast-close" onClick={onClose} aria-label="Fechar">×</button>
    </div>
  )
}
