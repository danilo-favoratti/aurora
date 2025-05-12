import asyncio
import json
import os
# base64, io, PIL.Image are no longer directly used in app.py

from fastapi import FastAPI, WebSocket, WebSocketDisconnect # WebSocketDisconnect needed for endpoint
from fastapi.staticfiles import StaticFiles
from starlette.websockets import WebSocketState # WebSocketState needed for endpoint

# Config imports are no longer directly needed in app.py if RPGSession handles them all
# from config import ... (can be removed if not used directly here)

# Image utils are no longer directly needed in app.py
# from image_utils import ...

# Import RPGSession from its new file
from rpg_session import RPGSession

app = FastAPI()

connected_clients = {}

# RPGSession class definition is now removed from here

@app.websocket("/ws/{session_id}")
async def websocket_endpoint(websocket: WebSocket, session_id: str):
    await websocket.accept()

    if session_id not in connected_clients:
        # Create RPGSession using the class imported from rpg_session.py
        connected_clients[session_id] = RPGSession(session_id)

    session = connected_clients[session_id]

    try:
        if not session.game_concluded: 
            await session.start_game(websocket)
        else:
            if websocket.client_state == WebSocketState.CONNECTED:
                await websocket.send_text(json.dumps({"type": "text", "content": session.current_narration or "The story had already concluded."}))
                await websocket.send_text(json.dumps({"type": "game_end", "message": "This story has already concluded."}))
            # If game already concluded and we don't close, client might be confused if it tries to reconnect to this state.
            # However, per request, we are not closing. Client should handle UI based on game_end message.
            # return # Exiting here would prevent the receive loop for already concluded games if we want to keep socket open.

        while True:
            if session.game_concluded:
                print(f"[Session {session_id}] Game is concluded. WebSocket receive loop will idle if connection kept open.")
                # Instead of breaking, we let it idle. Or, we could break and rely on finally not closing.
                # For now, let it break to prevent further processing if client sends unexpected data post-game_end.
                break 

            data = await websocket.receive_text()
            try:
                user_data = json.loads(data)
                choice = user_data.get("choice", "")
                turn_id = user_data.get("turn_id") 
                
                if session.game_concluded: # Double check before processing
                    print(f"[Session {session_id}] Game concluded. Ignoring choice: {choice}")
                    continue # Ignore choices if game ended during this loop iteration
                
                if choice and turn_id is not None:
                    await session.process_user_choice(choice, turn_id, websocket)
                elif choice:
                    print(f"[Session {session_id}] Received choice '{choice}' without a turn_id. Ignoring.")
            except json.JSONDecodeError:
                if websocket.client_state == WebSocketState.CONNECTED:
                    await websocket.send_text(json.dumps({"type": "error", "content": "Invalid input format."}))
            except WebSocketDisconnect: 
                print(f"WebSocket disconnected during receive for session: {session_id}")
                raise # Re-raise to be caught by the outer try/except WebSocketDisconnect
    
    except WebSocketDisconnect:
        print(f"WebSocket disconnected for session: {session_id} (either client closed or network issue).")
    except Exception as e:
        print(f"Unexpected error in WebSocket handler for {session_id}: {e}")
    finally:
        print(f"[Session {session_id}] WebSocket endpoint finishing. Game concluded: {session.game_concluded}")
        
        if session.background_tasks:
            print(f"[Session {session_id}] Waiting for {len(session.background_tasks)} background tasks...")
            # Gather with return_exceptions=True to ensure all tasks are awaited even if some fail
            results = await asyncio.gather(*list(session.background_tasks), return_exceptions=True)
            for i, result in enumerate(results):
                if isinstance(result, Exception):
                    print(f"[Session {session_id}] Background task {i} failed: {result}")
            print(f"[Session {session_id}] Background tasks finalized.")

        if session.game_concluded and websocket.client_state == WebSocketState.CONNECTED:
            try:
                print(f"[Session {session_id}] Sending final game_end message from endpoint (connection will remain open).")
                await websocket.send_text(json.dumps({"type": "game_end", "message": "The story has concluded."}))
            except Exception as e_final_send:
                print(f"[Session {session_id}] Exception sending final game_end: {e_final_send}")

        # Session cleanup from connected_clients still happens
        if session_id in connected_clients:
            print(f"[Session {session_id}] Cleaning up session object from connected_clients dictionary.")
            # Active background tasks should have been gathered. If any were added very late and not caught, they might be orphaned.
            # However, _create_background_task adds them to the set, so gather should catch them.
            del connected_clients[session_id]
            print(f"[Session {session_id}] Session object cleaned up.")
        
        # MODIFICATION: Do not explicitly close the WebSocket from the server side.
        # The connection will remain open until the client closes it or a network error occurs.
        if websocket.client_state != WebSocketState.DISCONNECTED:
            print(f"[Session {session_id}] Server will NOT explicitly close WebSocket. Current state: {websocket.client_state}")
            # try:
            #     await websocket.close() # REMOVED THIS LINE
            #     print(f"[Session {session_id}] WebSocket closed from finally block.")
            # except Exception as e_close:
            #     print(f"[Session {session_id}] Exception closing WebSocket: {e_close}")
        else:
            print(f"[Session {session_id}] WebSocket already disconnected by client or network issue.")

        print(f"[Session {session_id}] WebSocket connection handler fully exiting (connection may remain open if client keeps it).")

app.mount("/", StaticFiles(directory="static", html=True), name="static")

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("app:app", host="0.0.0.0", port=8000, reload=True)
