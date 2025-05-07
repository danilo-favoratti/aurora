import asyncio
import json
import os

from dotenv import load_dotenv
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.staticfiles import StaticFiles
from openai import OpenAI, moderations
from starlette.websockets import WebSocketState

# Load environment variables
load_dotenv()
client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))


# Load system prompt from file
def load_system_prompt(file_path: str = "instructions.md") -> str:
    try:
        with open(file_path, 'r') as f:
            return f.read().strip()
    except FileNotFoundError:
        print(f"Error: System prompt file '{file_path}' not found.")
        # Fallback or raise error
        return "You are a helpful assistant. Respond in JSON."  # Basic fallback


SYSTEM_PROMPT = load_system_prompt()

app = FastAPI()

# Store active connections
connected_clients = {}

# Initial game introduction (Load from .env, with fallback)
INTRO_PROMPT = os.getenv(
    "INTRO_PROMPT",
    "Tema: Roda Gigante"
)


class RPGSession:
    def __init__(self, session_id: str):
        self.session_id = session_id
        self.messages = [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": "Start a new fantasy adventure."},
        ]
        self.current_narration = ""
        self.current_choices = []
        self.current_image_prompt = ""
        self.complete_response = ""
        self.background_tasks = set()  # Set to store active background tasks

    def _create_background_task(self, coro):
        """Helper to create, store, and manage cleanup of background tasks."""
        task = asyncio.create_task(coro)
        self.background_tasks.add(task)
        # Add a callback to remove the task from the set upon completion
        task.add_done_callback(self.background_tasks.discard)
        return task

    async def process_user_choice(self, choice: str, turn_id: int, websocket: WebSocket):
        """Process a user's choice and generate the next story segment."""
        # Add the user's choice to the message history
        self.messages.append({"role": "user", "content": choice})

        # Reset the current state
        self.current_narration = ""
        self.current_choices = []
        self.current_image_prompt = ""
        self.complete_response = ""

        # Call OpenAI API with streaming
        try:
            stream = await asyncio.to_thread(
                client.chat.completions.create,
                model="gpt-4o",
                messages=self.messages,
                stream=True
            )
        except Exception as e:
            # Log the error server-side
            print(f"[Session {self.session_id}] !!! OpenAI API Call Error: {e}")
            # Attempt to send error to client if connected
            if websocket.client_state == WebSocketState.CONNECTED:
                try:
                    await websocket.send_text(json.dumps(
                        {"type": "error", "content": f"OpenAI API Error: Check server logs."}))  # Send generic error
                except Exception as send_e:
                    print(f"[Session {self.session_id}] Error sending API error message to client: {send_e}")
            return  # Stop processing if API call failed

        # Variables to track state during stream processing
        image_prompt_found_and_triggered = False
        token_count = 0

        async for chunk in self._async_generator(stream):
            if hasattr(chunk.choices[0].delta, 'content') and chunk.choices[0].delta.content is not None:
                token = chunk.choices[0].delta.content
                token_count += 1
                self.complete_response += token

                # Stream the token to the client
                # Check connection before sending stream token
                if websocket.client_state == WebSocketState.CONNECTED:
                    try:
                        await websocket.send_text(json.dumps({"type": "text", "content": token}))
                    except Exception as send_e:
                        print(f"[Session {self.session_id}] Error sending stream token: {send_e}. Aborting stream.")
                        # Cancel background tasks if stream fails?
                        for task in list(self.background_tasks):
                            task.cancel()
                        return  # Stop processing this choice
                else:
                    print(f"[Session {self.session_id}] WebSocket disconnected during stream. Aborting.")
                    # Cancel background tasks
                    for task in list(self.background_tasks):
                        task.cancel()
                    return  # Stop processing

            # --- Early Image Prompt Extraction & Trigger --- 
            if not image_prompt_found_and_triggered and '"image_prompt": "' in self.complete_response:
                try:
                    # Attempt to parse the start of the JSON to find the prompt
                    partial_json_text = self.complete_response
                    prompt_key_index = partial_json_text.find('"image_prompt": "')
                    if prompt_key_index != -1:
                        value_start_index = prompt_key_index + len('"image_prompt": "')
                        # Find the *next* quote after the value starts
                        value_end_index = partial_json_text.find('"', value_start_index)
                        background = None

                        if value_end_index != -1:
                            extracted_prompt = partial_json_text[value_start_index:value_end_index]
                            # Basic check if prompt seems valid (not empty)
                            if extracted_prompt:
                                # print(f"[Session {self.session_id}] Early image prompt extracted: '{extracted_prompt}...'")
                                if websocket.client_state == WebSocketState.CONNECTED:
                                    self._create_background_task(self.generate_image(extracted_prompt, background, turn_id, websocket))
                                    image_prompt_found_and_triggered = True
                                else:
                                    print(f"[Session {self.session_id}] Skipping early image trigger: WebSocket disconnected.")

                except Exception as e:
                    # This might fail if the JSON is still very incomplete, that's okay
                    print(f"[Session {self.session_id}] Minor error attempting early prompt extraction: {e}")
            # --- End Early Image Trigger --- 

        # Process the complete response
        try:
            response_data = json.loads(self.complete_response)
            self.current_narration = response_data.get("narration", "")
            self.current_choices = response_data.get("choices", [])
            self.current_image_prompt = response_data.get("image_prompt", "")

            # Now image generation should have already been triggered if a prompt was found.
            # No need for a fallback trigger here anymore.

            # Send the choices to the client, checking connection first
            if websocket.client_state == WebSocketState.CONNECTED:
                await websocket.send_text(json.dumps({
                    "type": "choices",
                    "content": self.current_choices
                }))
                # Add the assistant's response to the message history only if sent
                self.messages.append({"role": "assistant", "content": self.complete_response})
            else:
                print(f"[Session {self.session_id}] Skipping sending choices: WebSocket disconnected.")

        except json.JSONDecodeError as json_err:
            print(
                f"[Session {self.session_id}] !!! JSON Parsing Error: {json_err} on response: {self.complete_response}")  # Log the failed response
            error_msg = "Error: Storyteller response format incorrect. Check server logs."
            if websocket.client_state == WebSocketState.CONNECTED:
                try:
                    # Include turn_id in error message
                    await websocket.send_text(json.dumps({"type": "error", "content": error_msg, "turn_id": turn_id}))
                except Exception as send_e:
                    print(f"[Session {self.session_id}] Error sending JSON parse error message: {send_e}")
        except Exception as e:  # Catch other potential errors during processing
            error_msg = f"Error processing response: {str(e)}"
            print(f"[Session {self.session_id}] !!! Error processing response: {e}")
            if websocket.client_state == WebSocketState.CONNECTED:
                try:
                    # Include turn_id in error message
                    await websocket.send_text(json.dumps({"type": "error", "content": "Server error processing response.", "turn_id": turn_id}))
                except Exception as send_e:
                    print(f"[Session {self.session_id}] Error sending generic processing error message: {send_e}")

    async def _async_generator(self, stream):
        """Convert sync generator to async generator"""
        for chunk in stream:
            yield chunk
            await asyncio.sleep(0)

    async def start_game(self, websocket: WebSocket):
        """Start a new game session with the introduction."""
        initial_turn_id = 0 # Assign ID 0 to the initial turn
        # Define the initial game state
        initial_narration = INTRO_PROMPT
        initial_choices = json.loads(os.getenv(
            "INITIAL_CHOICES",
            '["Roda Gigante", "Algodão Doce", "Fantasia de Borboleta", "Gatinhos Fofos"]'
        ))
        initial_image_prompt = os.getenv(
            "INITIAL_IMAGE_PROMPT",
            "Uma garotinha de 1 ano com chuquinha na cabeça e cara alegre"
        )
        background = "opaque"

        # Start image generation in the background
        # Check connection before starting background task
        if websocket.client_state == WebSocketState.CONNECTED:
            # Use helper to create and track task
            self._create_background_task(self.generate_image(initial_image_prompt, background, initial_turn_id, websocket))
        else:
            print(f"Skipping initial image generation for {self.session_id}: WebSocket disconnected.")
            return  # Don't proceed if disconnected at the start

        # Create the full initial JSON response
        initial_assistant_message = json.dumps({
            "narration": initial_narration,
            "choices": initial_choices,  # Include choices here for history, though sent separately later
            "image_prompt": initial_image_prompt
        })

        # --- Stream the full JSON response character by character ---
        for char in initial_assistant_message:
            # Check connection before each send during initial stream
            if websocket.client_state != WebSocketState.CONNECTED:
                print(f"Initial stream interrupted for {self.session_id}: WebSocket disconnected.")
                return  # Exit start_game if disconnected during stream
            await websocket.send_text(json.dumps({"type": "text", "content": char}))
            await asyncio.sleep(0.01)  # Keep speed fast for JSON structure parts
        # --- End JSON streaming ---

        # Send the initial choices separately after the stream, check connection
        if websocket.client_state == WebSocketState.CONNECTED:
            await websocket.send_text(json.dumps({
                "type": "choices",
                "content": initial_choices
            }))
            # Store the initial message structure in history
            self.messages.append({"role": "assistant", "content": initial_assistant_message})
        else:
            print(f"Skipping initial choices for {self.session_id}: WebSocket disconnected.")

    async def generate_image(self, prompt: str, background: str, turn_id: int, websocket: WebSocket):
        """Generate an image based on the prompt and send it to the client."""
        try:
            # Ensure the prompt is for 8-bit pixel art
            prompt = f"Uma image no estilo *64-bit pixel-art* de: {prompt}"
            print(f"Image Prompt: {prompt}")

            # Call the OpenAI Image API
            response = await asyncio.to_thread(
                client.images.generate,
                prompt=prompt,
                background=background or "auto",
                model="gpt-image-1",
                moderation="low",
                n=1,
                output_compression=100,
                output_format="png",
                quality="high",
                size="1024x1024"
            )

            # Extract the base64 image
            image_b64 = response.data[0].b64_json

            # Send the image to the client
            await websocket.send_text(json.dumps({
                "type": "image",
                "content": image_b64,
                "turn_id": turn_id
            }))
        except Exception as e:
            # Handle any errors during the API call itself
            error_msg = f"Error generating image: {str(e)}"
            print(f"Image generation error for session {self.session_id}: {error_msg}")
            # Send error only if connected
            if websocket.client_state == WebSocketState.CONNECTED:
                # Include turn_id in error message
                await websocket.send_text(json.dumps({"type": "error", "content": error_msg, "turn_id": turn_id}))


@app.websocket("/ws/{session_id}")
async def websocket_endpoint(websocket: WebSocket, session_id: str):
    await websocket.accept()

    # Create or retrieve a session
    if session_id not in connected_clients:
        connected_clients[session_id] = RPGSession(session_id)

    session = connected_clients[session_id]

    try:
        # Start the game
        await session.start_game(websocket)

        # Main game loop
        while True:
            # Wait for user input
            data = await websocket.receive_text()
            try:
                # Parse the user's choice
                user_data = json.loads(data)
                choice = user_data.get("choice", "")
                turn_id = user_data.get("turn_id") # Get the turn_id sent by client
                
                if choice and turn_id is not None:
                    # Process the user's choice
                    await session.process_user_choice(choice, turn_id, websocket)
                elif choice:
                    print(f"[Session {session_id}] Received choice '{choice}' without a turn_id. Ignoring.")
                    # Optionally send an error back to client?
            except json.JSONDecodeError:
                # Handle invalid JSON
                await websocket.send_text(json.dumps({
                    "type": "error",
                    "content": "Invalid input format. Expected JSON with 'choice' field."
                }))
    except WebSocketDisconnect:
        print(f"WebSocket disconnected for session: {session_id}")
        # Clean up the session when the user disconnects
        if session_id in connected_clients:
            session = connected_clients[session_id]
            # Cancel any running background tasks for this session
            print(f"Cancelling {len(session.background_tasks)} background tasks for session {session_id}...")
            for task in list(session.background_tasks):  # Iterate over a copy
                task.cancel()
            # Optionally wait for tasks to cancel (can prevent errors if cancellation takes time)
            # await asyncio.gather(*session.background_tasks, return_exceptions=True)
            del connected_clients[session_id]
            print(f"Session {session_id} cleaned up.")
    except Exception as e:
        # Catch other potential exceptions during the websocket connection
        print(f"Unexpected error in WebSocket handler for {session_id}: {e}")
        # Ensure cleanup happens even on unexpected errors
        if session_id in connected_clients:
            session = connected_clients[session_id]
            for task in list(session.background_tasks):
                task.cancel()
            del connected_clients[session_id]
            print(f"Session {session_id} cleaned up after error.")


# Mount static files
app.mount("/", StaticFiles(directory="static", html=True), name="static")

if __name__ == "__main__":
    import uvicorn

    uvicorn.run("app:app", host="0.0.0.0", port=8000, reload=True)
