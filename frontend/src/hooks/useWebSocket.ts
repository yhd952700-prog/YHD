import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import type { WsConnectionState, WsMessage, WsSendOptions } from '../types';

export interface UseWebSocketOptions {
  /** WebSocket 地址；为 null 时不连接 */
  url: string | null;
  /** 子协议 */
  protocols?: string | string[];
  /** 是否自动重连，默认 true */
  reconnect?: boolean;
  /** 最大重连次数，默认 5 */
  maxReconnectAttempts?: number;
  /** 基础重连间隔（指数退避），默认 1000ms */
  reconnectIntervalMs?: number;
  /** 心跳间隔（毫秒），0 关闭 */
  heartbeatIntervalMs?: number;
  /** 心跳消息 */
  heartbeatMessage?: () => unknown;
  /** 消息处理器（按 type 注册） */
  onMessage?: (message: WsMessage) => void;
  onOpen?: () => void;
  onClose?: (event: CloseEvent) => void;
  onError?: (event: Event) => void;
  /** 是否立即连接，默认 true */
  enabled?: boolean;
}

export interface UseWebSocketResult {
  state: WsConnectionState;
  lastMessage: WsMessage | null;
  reconnectAttempt: number;
  send: <T = unknown>(type: string, payload: T, options?: WsSendOptions) => boolean;
  connect: () => void;
  disconnect: () => void;
  isConnected: boolean;
}

function serialize(data: unknown): string {
  return JSON.stringify(data);
}

function tryParse(raw: string): WsMessage | null {
  try {
    const parsed = JSON.parse(raw) as Partial<WsMessage>;
    if (parsed && typeof parsed === 'object' && typeof parsed.type === 'string') {
      return {
        type: parsed.type,
        payload: parsed.payload,
        timestamp: parsed.timestamp ?? new Date().toISOString(),
        id: parsed.id,
      };
    }
    return { type: 'raw', payload: parsed, timestamp: new Date().toISOString() };
  } catch {
    return { type: 'raw', payload: raw, timestamp: new Date().toISOString() };
  }
}

/**
 * useWebSocket - 带自动重连、心跳与指数退避的 WebSocket Hook。
 */
export function useWebSocket(options: UseWebSocketOptions): UseWebSocketResult {
  const {
    url,
    protocols,
    reconnect = true,
    maxReconnectAttempts = 5,
    reconnectIntervalMs = 1000,
    heartbeatIntervalMs = 0,
    heartbeatMessage,
    onMessage,
    onOpen,
    onClose,
    onError,
    enabled = true,
  } = options;

  const [state, setState] = useState<WsConnectionState>('closed');
  const [lastMessage, setLastMessage] = useState<WsMessage | null>(null);
  const [reconnectAttempt, setReconnectAttempt] = useState(0);

  const wsRef = useRef<WebSocket | null>(null);
  const reconnectTimerRef = useRef<number | null>(null);
  const heartbeatTimerRef = useRef<number | null>(null);
  const manualCloseRef = useRef(false);
  const attemptRef = useRef(0);
  const queueRef = useRef<string[]>([]);

  // 保持回调最新，避免重连闭包过期
  const handlersRef = useRef({ onMessage, onOpen, onClose, onError });
  handlersRef.current = { onMessage, onOpen, onClose, onError };

  const clearTimers = useCallback(() => {
    if (reconnectTimerRef.current !== null) {
      window.clearTimeout(reconnectTimerRef.current);
      reconnectTimerRef.current = null;
    }
    if (heartbeatTimerRef.current !== null) {
      window.clearInterval(heartbeatTimerRef.current);
      heartbeatTimerRef.current = null;
    }
  }, []);

  const startHeartbeat = useCallback(() => {
    if (heartbeatIntervalMs <= 0) return;
    heartbeatTimerRef.current = window.setInterval(() => {
      const ws = wsRef.current;
      if (ws?.readyState === WebSocket.OPEN) {
        const payload = heartbeatMessage?.() ?? { type: 'ping', timestamp: Date.now() };
        ws.send(typeof payload === 'string' ? payload : serialize(payload));
      }
    }, heartbeatIntervalMs);
  }, [heartbeatIntervalMs, heartbeatMessage]);

  const flushQueue = useCallback(() => {
    const ws = wsRef.current;
    if (!ws || ws.readyState !== WebSocket.OPEN) return;
    while (queueRef.current.length > 0) {
      const data = queueRef.current.shift();
      if (data !== undefined) ws.send(data);
    }
  }, []);

  const connect = useCallback(() => {
    if (!url) return;
    if (wsRef.current && wsRef.current.readyState <= WebSocket.OPEN) return;

    clearTimers();
    manualCloseRef.current = false;
    setState('connecting');

    let ws: WebSocket;
    try {
      ws = new WebSocket(url, protocols);
    } catch {
      setState('error');
      return;
    }
    wsRef.current = ws;

    ws.onopen = () => {
      attemptRef.current = 0;
      setReconnectAttempt(0);
      setState('open');
      flushQueue();
      startHeartbeat();
      handlersRef.current.onOpen?.();
    };

    ws.onmessage = (event) => {
      const message = tryParse(String(event.data));
      if (message) {
        setLastMessage(message);
        handlersRef.current.onMessage?.(message);
      }
    };

    ws.onerror = (event) => {
      setState('error');
      handlersRef.current.onError?.(event);
    };

    ws.onclose = (event) => {
      clearTimers();
      setState('closed');
      handlersRef.current.onClose?.(event);

      if (!manualCloseRef.current && reconnect && attemptRef.current < maxReconnectAttempts) {
        attemptRef.current += 1;
        setReconnectAttempt(attemptRef.current);
        const delay = Math.min(
          reconnectIntervalMs * 2 ** (attemptRef.current - 1),
          30_000,
        );
        reconnectTimerRef.current = window.setTimeout(() => {
          connect();
        }, delay);
      }
    };
  }, [
    url,
    protocols,
    reconnect,
    maxReconnectAttempts,
    reconnectIntervalMs,
    clearTimers,
    flushQueue,
    startHeartbeat,
  ]);

  const disconnect = useCallback(() => {
    manualCloseRef.current = true;
    clearTimers();
    const ws = wsRef.current;
    if (ws) {
      wsRef.current = null;
      if (ws.readyState === WebSocket.OPEN || ws.readyState === WebSocket.CONNECTING) {
        ws.close(1000, 'client disconnect');
      }
    }
    setState('closed');
  }, [clearTimers]);

  const send = useCallback(
    <T,>(type: string, payload: T, options?: WsSendOptions): boolean => {
      const message: WsMessage<T> = {
        type,
        payload,
        timestamp: new Date().toISOString(),
        id: `${Date.now()}-${Math.random().toString(36).slice(2, 8)}`,
      };
      const data = serialize(message);
      const ws = wsRef.current;
      if (ws && ws.readyState === WebSocket.OPEN) {
        ws.send(data);
        return true;
      }
      if (options?.retry) {
        const maxRetries = options.maxRetries ?? 1;
        if (queueRef.current.length < maxRetries * 100) {
          queueRef.current.push(data);
        }
      }
      return false;
    },
    [],
  );

  useEffect(() => {
    if (enabled && url) {
      connect();
    }
    return () => {
      disconnect();
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [url, enabled]);

  const isConnected = useMemo(() => state === 'open', [state]);

  return {
    state,
    lastMessage,
    reconnectAttempt,
    send,
    connect,
    disconnect,
    isConnected,
  };
}
