import { useState, useEffect } from 'react'
import reactLogo from './assets/react.svg'
import viteLogo from './assets/vite.svg'
import './App.css'
import { eventClient } from './lib/eventClient'

function App() {
  const [connected, setConnected] = useState(false)
  const [lastEvent, setLastEvent] = useState<any>(null)
  const [eventCount, setEventCount] = useState(0)

  useEffect(() => {
    eventClient.connect()
    const unsubscribe = eventClient.subscribe((state) => {
      setConnected(state.connected)
      setLastEvent(state.lastEvent)
      setEventCount(state.eventCount)
    })
    return () => {
      unsubscribe()
      eventClient.disconnect()
    }
  }, [])

  return (
    <>
      <div>
        <a href="https://vite.dev" target="_blank">
          <img src={viteLogo} className="logo" alt="Vite logo" />
        </a>
        <a href="https://react.dev" target="_blank">
          <img src={reactLogo} className="logo react" alt="React logo" />
        </a>
      </div>
      <h1>L-Core Event Dashboard</h1>
      <div className="card">
        <p>Connection: {connected ? '✅ Connected' : '❌ Disconnected'}</p>
        <p>Event Count: {eventCount}</p>
        {lastEvent && (
          <pre style={{fontSize: '12px', textAlign: 'left', maxWidth: '600px', overflow: 'auto'}}>
            {JSON.stringify(lastEvent, null, 2)}
          </pre>
        )}
      </div>
      <p className="read-the-docs">
        L-Core UI now driven by real WebSocket events (P1-7 verified)
      </p>
    </>
  )
}

export default App
