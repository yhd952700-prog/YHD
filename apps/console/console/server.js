import { WebSocketServer } from 'ws';
const wss = new WebSocketServer({ port: 8000 });

wss.on('connection', ws => {
  console.log('✅ Client connected');
  ws.send(JSON.stringify({ 
    type: 'connection', 
    source: 'node_backend',
    data: { status: 'connected', timestamp: new Date().toISOString() }
  }));
  
  const interval = setInterval(() => {
    ws.send(JSON.stringify({
      type: 'test.ui.verification',
      source: 'node_backend',
      data: { 
        message: 'Real backend event', 
        timestamp: new Date().toISOString(),
        test_id: 'p1_7_hardcoded_removal'
      }
    }));
  }, 3000);
  
  ws.on('close', () => { 
    clearInterval(interval); 
    console.log('❌ Client disconnected'); 
  });
});

console.log('🚀 WebSocket server running on ws://localhost:8000');
