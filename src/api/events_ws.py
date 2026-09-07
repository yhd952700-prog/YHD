"""WebSocket Event Stream API

Real-time event streaming endpoint for L-Core UI.
Exposes the EventBus over WebSocket for frontend integration.

依据 Definition Lock v3.0:
- 必须使用真实后端 Event (禁止项 #2: Mock ≠ Implementation)
- 必须提供 Evidence + Verification + Traceability
"""
from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from src.kernels.event import get_event_bus, Event
import json
import asyncio
import logging
from typing import Set

logger = logging.getLogger(__name__)
router = APIRouter()

# Active WebSocket connections
_active_connections: Set[WebSocket] = set()


@router.websocket("/ws/events")
async def websocket_endpoint(websocket: WebSocket):
    """
    WebSocket endpoint for real-time event streaming.

    Protocol:
    - Client connects to ws://host:port/ws/events
    - Server sends events as JSON: {"type", "source", "data", "correlation_id", "timestamp"}
    - Client can send {"ping": true} for keepalive
    """
    await websocket.accept()
    _active_connections.add(websocket)
    logger.info(f"WebSocket client connected. Total: {len(_active_connections)}")

    event_bus = get_event_bus()

    # Event handler that sends events to this WebSocket
    async def event_handler(event: Event):
        """Send event to WebSocket client."""
        try:
            payload = {
                "type": event.type,
                "source": event.source,
                "data": event.data,
                "correlation_id": event.correlation_id,
                "causation_id": event.causation_id,
                "scope": event.scope.value,
                "priority": event.priority.value,
                "timestamp": event.timestamp.isoformat(),
                "metadata": event.metadata,
                "tags": list(event.tags),
            }
            await websocket.send_text(json.dumps(payload))
        except Exception as e:
            logger.error(f"Failed to send event to WebSocket: {e}")

    # Subscribe to all events
    subscription_id = event_bus.subscribe("*", lambda e: asyncio.create_task(event_handler(e)))
    logger.info(f"Subscription created: {subscription_id}")

    try:
        # Keep connection alive and handle client messages
        while True:
            try:
                data = await websocket.receive_text()
                # Handle keepalive pings
                msg = json.loads(data)
                if msg.get("ping"):
                    await websocket.send_text(json.dumps({"pong": True}))
            except json.JSONDecodeError:
                logger.warning("Invalid JSON received from WebSocket client")
            except Exception as e:
                logger.error(f"Error receiving WebSocket message: {e}")
                break

    except WebSocketDisconnect:
        logger.info("WebSocket client disconnected normally")
    finally:
        # Cleanup
        event_bus.unsubscribe(subscription_id)
        _active_connections.discard(websocket)
        logger.info(f"WebSocket cleanup complete. Remaining: {len(_active_connections)}")


@router.get("/ws/status")
async def websocket_status():
    """Get WebSocket connection status."""
    return {
        "active_connections": len(_active_connections),
        "endpoint": "/ws/events",
        "protocol": "WebSocket",
    }
