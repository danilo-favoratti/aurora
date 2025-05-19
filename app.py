import asyncio
import json
import os
# base64, io, PIL.Image are no longer directly used in app.py

from fastapi import FastAPI, WebSocket, WebSocketDisconnect, Request # WebSocketDisconnect needed for endpoint
from fastapi.staticfiles import StaticFiles
from starlette.websockets import WebSocketState # WebSocketState needed for endpoint

# Config imports are no longer directly needed in app.py if RPGSession handles them all
# from config import ... (can be removed if not used directly here)

# Image utils are no longer directly needed in app.py
# from image_utils import ...

# Import RPGSession from its new file
from rpg_session import RPGSession
import config # Import the config module directly

app = FastAPI()

connected_clients = {}

# RPGSession class definition is now removed from here

@app.websocket("/ws/{session_id}")
async def websocket_endpoint(websocket: WebSocket, session_id: str):
    await websocket.accept()
    print(f"[App] WebSocket {session_id} accepted.")

    if session_id not in connected_clients:
        print(f"[App] New session: {session_id}. Creating RPGSession.")
        connected_clients[session_id] = RPGSession(session_id)
    else:
        print(f"[App] Reconnecting or existing session: {session_id}.")
    
    session = connected_clients[session_id]
    print(f"[App] Session {session_id} obtained. Game concluded: {session.game_concluded}")

    try:
        if not session.game_concluded: 
            print(f"[App Session {session_id}] Calling start_game...")
            await session.start_game(websocket)
            print(f"[App Session {session_id}] start_game completed.")
        else:
            print(f"[App Session {session_id}] Game already concluded. Sending final state.")
            if websocket.client_state == WebSocketState.CONNECTED:
                # Prepare objectives data safely
                objectives_data = []
                if hasattr(session, 'game_context') and session.game_context and session.game_context.objectives:
                    objectives_data = [
                        {"id": obj.id, "objective": obj.objective, "finished": obj.finished, 
                         "target_count": obj.target_count, "current_count": obj.current_count,
                         "partially_complete": obj.partially_complete}
                        for obj in session.game_context.objectives
                    ]
                else:
                    print(f"[App Session {session_id}] No game_context.objectives to send for concluded game.")
                
                await websocket.send_text(json.dumps({
                    "type": "narration_block", 
                    "content": session.current_narration or "The story had already concluded.", 
                    "turn_id": session.turn_number
                }))
                await websocket.send_text(json.dumps({
                    "type": "objectives", 
                    "content": objectives_data, 
                    "turn_id": session.turn_number
                }))
                await websocket.send_text(json.dumps({"type": "game_end", "message": "This story has already concluded."}))
            print(f"[App Session {session_id}] Final state sent for concluded game.")

        while True:
            if session.game_concluded:
                print(f"[App Session {session_id}] Game is concluded. Breaking WebSocket receive loop.")
                break 

            print(f"[App Session {session_id}] Waiting for client message...")
            data = await websocket.receive_text()
            print(f"[App Session {session_id}] Received data: {data[:100]}...")
            
            try:
                user_data = json.loads(data)
            except json.JSONDecodeError:
                print(f"[App Session {session_id}] Invalid JSON received from client. Message: {data}")
                if websocket.client_state == WebSocketState.CONNECTED:
                    await websocket.send_text(json.dumps({"type": "error", "content": "Invalid JSON input from client."}))
                continue # Wait for next message

            choice = user_data.get("choice") 
            turn_id_from_client = user_data.get("turn_id")
            
            if session.game_concluded: 
                print(f"[App Session {session_id}] Game concluded (checked after receive). Ignoring choice: {choice}")
                if websocket.client_state == WebSocketState.CONNECTED:
                     await websocket.send_text(json.dumps({"type": "game_end", "message": "The story has concluded."}))
                break 
            
            if choice is not None and turn_id_from_client is not None: 
                print(f"[App Session {session_id}] Processing choice: '{choice}' for new turn_id: {turn_id_from_client}")
                await session.process_user_choice(choice, turn_id_from_client, websocket)
                print(f"[App Session {session_id}] process_user_choice completed for turn_id: {turn_id_from_client}")
            elif choice is not None: 
                print(f"[App Session {session_id}] Received choice '{choice}' without a turn_id. Ignoring.")
                if websocket.client_state == WebSocketState.CONNECTED:
                    await websocket.send_text(json.dumps({"type": "error", "content": "Client choice message missing 'turn_id' from client."}))
            else: 
                print(f"[App Session {session_id}] Malformed choice message. Data: {user_data}")
                if websocket.client_state == WebSocketState.CONNECTED:
                    await websocket.send_text(json.dumps({"type": "error", "content": "Malformed choice message from client."}))

    except WebSocketDisconnect:
        print(f"[App Session {session_id}] WebSocket disconnected by client or network issue.")
    except Exception as e:
        print(f"[App Session {session_id}] Unexpected error in WebSocket handler for {session_id}: {type(e).__name__} - {e}")
        import traceback
        traceback.print_exc()
        if websocket.client_state == WebSocketState.CONNECTED:
            try:
                await websocket.send_text(json.dumps({"type": "error", "content": "Unexpected server error. Please check logs."}))
            except Exception as send_err:
                print(f"[App Session {session_id}] Failed to send error to client after main exception: {send_err}")
    finally:
        print(f"[App Session {session_id}] WebSocket endpoint 'finally' block. Game concluded: {session.game_concluded}")
        
        if hasattr(session, 'background_tasks') and session.background_tasks:
            print(f"[App Session {session_id}] Waiting for {len(session.background_tasks)} background tasks...")
            results = await asyncio.gather(*list(session.background_tasks), return_exceptions=True)
            for i, result in enumerate(results):
                if isinstance(result, Exception):
                    print(f"[App Session {session_id}] Background task {i} failed: {result}")
            print(f"[App Session {session_id}] Background tasks finalized.")

        if session.game_concluded and websocket.client_state == WebSocketState.CONNECTED:
            try:
                print(f"[App Session {session_id}] Sending final game_end message from endpoint's finally block (if not already sent).")
                await websocket.send_text(json.dumps({"type": "game_end", "message": "The story has concluded."}))
            except Exception as e_final_send:
                print(f"[App Session {session_id}] Exception sending final game_end from finally: {e_final_send}")

        if session_id in connected_clients:
            print(f"[App Session {session_id}] Cleaning up RPGSession object from connected_clients.")
            del connected_clients[session_id]
            print(f"[App Session {session_id}] RPGSession object cleaned up.")
        
        if websocket.client_state != WebSocketState.DISCONNECTED:
            print(f"[App Session {session_id}] Server is NOT explicitly closing WebSocket per design. Current state: {websocket.client_state}")
        else:
            print(f"[App Session {session_id}] WebSocket was already disconnected.")

        print(f"[App Session {session_id}] WebSocket connection handler ({websocket_endpoint.__name__}) fully exiting.")

app.mount("/", StaticFiles(directory="static", html=True), name="static")

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("app:app", host="0.0.0.0", port=8020, reload=True)
