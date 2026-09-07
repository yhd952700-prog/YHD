interface EventState {
  connected: boolean;
  lastEvent: any | null;
  eventCount: number;
}

let state: EventState = { connected: false, lastEvent: null, eventCount: 0 };
let listeners: Set<(state: EventState) => void> = new Set();

class EventClient {
  private ws: WebSocket | null = null;

  connect() {
    this.ws = new WebSocket('ws://localhost:8000');
    this.ws.onopen = () => { 
      state.connected = true; 
      listeners.forEach(fn => fn(state)); 
    };
    this.ws.onmessage = (event) => {
      state.lastEvent = JSON.parse(event.data);
      state.eventCount++;
      listeners.forEach(fn => fn(state));
    };
    this.ws.onclose = () => { 
      state.connected = false; 
      listeners.forEach(fn => fn(state)); 
    };
    this.ws.onerror = (err) => console.error('WebSocket error:', err);
  }

  subscribe(fn: (state: EventState) => void) {
    listeners.add(fn);
    fn(state);
    return () => listeners.delete(fn);
  }

  disconnect() { this.ws?.close(); }
}

export const eventClient = new EventClient();
