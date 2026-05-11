import { Component } from 'react'

export class ErrorBoundary extends Component {
  constructor(props) {
    super(props)
    this.state = { error: null, info: null }
  }

  static getDerivedStateFromError(error) {
    return { error }
  }

  componentDidCatch(error, info) {
    console.error('[ErrorBoundary]', error, info)
    this.setState({ info })
  }

  render() {
    if (this.state.error) {
      return (
        <div style={{
          padding: 40,
          background: '#08090a',
          color: '#f0f0f2',
          minHeight: '100vh',
          fontFamily: 'monospace',
        }}>
          <h1 style={{ color: '#dc2626', marginBottom: 16 }}>⚠ Erro de runtime React</h1>
          <pre style={{
            background: '#111113',
            border: '1px solid #1f2024',
            padding: 16,
            borderRadius: 8,
            overflow: 'auto',
            fontSize: 13,
            color: '#a1a1aa',
            whiteSpace: 'pre-wrap',
          }}>
            <strong style={{ color: '#f0f0f2' }}>{this.state.error.message}</strong>
            {'\n\n'}
            {this.state.error.stack}
            {this.state.info?.componentStack && (
              <>
                {'\n\nComponent stack:'}
                {this.state.info.componentStack}
              </>
            )}
          </pre>
          <button onClick={() => window.location.reload()} style={{
            marginTop: 20,
            padding: '8px 16px',
            background: '#10b981',
            color: '#052e1c',
            border: 'none',
            borderRadius: 6,
            cursor: 'pointer',
            fontWeight: 600,
          }}>
            Recarregar
          </button>
        </div>
      )
    }
    return this.props.children
  }
}
