from fastapi import APIRouter, WebSocket

from app.graph.state import IntentConstraints
from app.graph.workflow import iter_trip_workflow

router = APIRouter(tags=["websocket"])


@router.websocket("/ws/solve")
async def solve_socket(websocket: WebSocket) -> None:
    """Stream workflow events followed by the final route solution."""
    await websocket.accept()
    try:
        payload = await websocket.receive_json()
        intent = IntentConstraints.model_validate(payload)
        state = None
        seen_events = 0
        for stage, state in iter_trip_workflow(intent):
            await websocket.send_json(
                {
                    "event": "stage_complete",
                    "payload": {
                        "stage": stage,
                        "status": state.graph_controls.current_status.value,
                    },
                }
            )
            for event in state.graph_controls.events[seen_events:]:
                await websocket.send_json(event)
            seen_events = len(state.graph_controls.events)
        if state is None:
            await websocket.send_json({"event": "failed", "payload": {"reason": "no workflow state produced"}})
            await websocket.close()
            return
        await websocket.send_json({"event": "complete", "payload": state.model_dump(mode="json")})
    except Exception as exc:
        await websocket.send_json({"event": "failed", "payload": {"reason": str(exc)}})
    finally:
        await websocket.close()
