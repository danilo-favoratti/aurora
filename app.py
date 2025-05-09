import asyncio
import json
import os
import base64 # Import base64 for data URL
import io
from PIL import Image

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

CHARACTER_IMAGE_PATHS = {
    "aurora": "images/aurora.png",
    "barbara": "images/barbara.png",
    "davi": "images/davi.png",
    "lari": "images/lari.png",
    "danilo": "images/danilo.png",
}

MAX_GAME_TURNS = 5 # Define the maximum number of turns

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
        self.current_characters_in_scene = []
        self.complete_response = ""
        self.background_tasks = set()  # Set to store active background tasks
        self.reference_image_bytes: bytes | None = None
        self.reference_image_mime: str | None = None
        self.turn_number = 0  # Initialize turn number
        self.game_concluded = False # Flag to indicate if the game has ended

    def _create_background_task(self, coro):
        """Helper to create, store, and manage cleanup of background tasks."""
        task = asyncio.create_task(coro)
        self.background_tasks.add(task)
        # Add a callback to remove the task from the set upon completion
        task.add_done_callback(self.background_tasks.discard)
        return task

    async def process_user_choice(self, choice: str, turn_id: int, websocket: WebSocket):
        """Process a user's choice and generate the next story segment or conclude the game."""
        if self.game_concluded:
            # Optionally send a message to client if they try to interact after game over
            print(f"[Session {self.session_id}] Game already concluded. Ignoring choice: {choice}")
            # await websocket.send_text(json.dumps({"type": "info", "content": "The story has already concluded."}))
            return

        self.turn_number += 1
        print(f"[Session {self.session_id}] Processing Turn Number: {self.turn_number}")

        # Add the user's choice to the message history
        # For concluding turn, the choice isn't strictly part of a new interaction, but we might log it.
        # If it's the concluding turn, the user message might be more of a trigger.
        user_content_for_api = choice 

        # Check if this is the turn to conclude the game
        if self.turn_number >= MAX_GAME_TURNS:
            self.game_concluded = True
            print(f"[Session {self.session_id}] Max turns reached. Concluding game.")
            # Modify user content to instruct AI to conclude
            user_content_for_api = "The story is now coming to an end. Please provide a final concluding narration. Do not offer any choices."
            # We expect a response with "narration" and possibly "image_prompt", but no "choices".
            # The system prompt already guides for JSON, so AI should still respond in JSON.

        self.messages.append({"role": "user", "content": user_content_for_api})

        # Reset the current state parts that are response-dependent
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

        # Process the complete response
        try:
            # Clean potential markdown code block markers
            cleaned_response = self.complete_response.strip()
            if cleaned_response.startswith("```json"):
                cleaned_response = cleaned_response[7:] # Remove ```json\n
            if cleaned_response.endswith("```"):
                cleaned_response = cleaned_response[:-3]
            cleaned_response = cleaned_response.strip() # Remove any leading/trailing whitespace again

            # Attempt to fix known LLM-induced escape errors before parsing
            cleaned_response = cleaned_response.replace("\\\\'.", "'.") # Typo from previous, should be \\'. not \\\\'.
            
            response_data = json.loads(cleaned_response)
            self.current_narration = response_data.get("narration", "")
            self.current_choices = response_data.get("choices", [])
            self.current_image_prompt = response_data.get("image_prompt", "")
            self.current_characters_in_scene = response_data.get("characters_in_scene", [])

            print(f"[Session {self.session_id}] Parsed characters in scene: {self.current_characters_in_scene}") 

            # Trigger image generation AFTER full response is parsed
            if self.current_image_prompt and websocket.client_state == WebSocketState.CONNECTED:
                print(f"[Session {self.session_id}] Triggering image generation for prompt: '{self.current_image_prompt}' with characters: {self.current_characters_in_scene}")
                self._create_background_task(self.generate_scene(self.current_image_prompt, turn_id, websocket))
            elif not self.current_image_prompt:
                 print(f"[Session {self.session_id}] No image prompt found in the response. Skipping image generation.")

            # Send the choices to the client, or game_end message
            if websocket.client_state == WebSocketState.CONNECTED:
                # if self.game_concluded: # Game end message is now sent from websocket_endpoint
                #     await websocket.send_text(json.dumps({
                #         "type": "game_end",
                #         "message": "The story has concluded after this turn."
                #     }))
                #     print(f"[Session {self.session_id}] Sent game_end message from process_user_choice - (now handled by endpoint)")
                if not self.game_concluded and self.current_choices: # Only send choices if game not concluded and choices exist
                    await websocket.send_text(json.dumps({
                        "type": "choices",
                        "content": self.current_choices
                    }))
                self.messages.append({"role": "assistant", "content": cleaned_response})
            else:
                print(f"[Session {self.session_id}] Skipping sending choices: WebSocket disconnected.")

        except json.JSONDecodeError as json_err:
            print(
                f"[Session {self.session_id}] !!! JSON Parsing Error: {json_err} on response: {self.complete_response}")  # Log the failed response
            error_msg = "Error: Storyteller response format incorrect."
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
        self.turn_number = 0 # Reset turn number for a new game
        self.game_concluded = False # Reset game concluded flag
        initial_turn_id = 0 # Assign ID 0 to the initial turn
        use_placeholder = os.getenv("USE_PLACEHOLDER_INITIAL_IMAGE", "false").lower() == "true"

        # Define the initial game state
        initial_narration = INTRO_PROMPT
        initial_choices = json.loads(os.getenv(
            "INITIAL_CHOICES",
            '["Roda Gigante", "Algodão Doce", "Fantasia de Borboleta", "Gatinhos Fofos"]'
        ))
        initial_image_prompt = os.getenv(
            "INITIAL_IMAGE_PROMPT",
            "Uma garotinha de 1 ano com chuquinha na cabeça, loira dos olhos claros e cara alegre"
        )

        # Start image generation or use placeholder
        if websocket.client_state == WebSocketState.CONNECTED:
            if use_placeholder:
                print(f"[Session {self.session_id}] Using placeholder for initial image.")
                try:
                    placeholder_path = "images/aurora.png"
                    if not os.path.isfile(placeholder_path):
                        raise FileNotFoundError(f"Placeholder image not found at {placeholder_path}")
                    
                    with open(placeholder_path, "rb") as f:
                        raw_bytes = f.read()
                    
                    # Process placeholder to RGBA PNG and store as reference
                    pil_img = Image.open(io.BytesIO(raw_bytes))
                    pil_img = pil_img.convert("RGBA")
                    png_buffer = io.BytesIO()
                    pil_img.save(png_buffer, format="PNG")
                    pil_img.close()
                    png_buffer.seek(0)

                    self.reference_image_bytes = png_buffer.getvalue()
                    self.reference_image_mime = "image/png"
                    
                    # Base64 encode for sending to client
                    base64_placeholder = base64.b64encode(self.reference_image_bytes).decode("utf-8")

                    await websocket.send_text(json.dumps({
                        "type": "image",
                        "content": base64_placeholder,
                        "turn_id": initial_turn_id
                    }))
                except Exception as e:
                    error_msg = f"Error loading/sending placeholder image: {e}"
                    print(f"[Session {self.session_id}] {error_msg}")
                    # Optionally send an error to the client if placeholder loading fails
                    await websocket.send_text(json.dumps({
                        "type": "error", "content": error_msg, "turn_id": initial_turn_id
                    })) 
                    # Decide if we should return or let the game start without an image
                    return # For now, if placeholder fails, stop game start for this client
            else:
                # Use API to generate initial image (existing logic)
                self._create_background_task(self.generate_image(initial_image_prompt, "auto", initial_turn_id, websocket, base64_image="images/aurora.png"))
        else:
            print(f"Skipping initial image generation/placeholder for {self.session_id}: WebSocket disconnected.")
            return

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

    async def generate_image(
        self,
        prompt: str,
        background: str,
        turn_id: int,
        websocket: WebSocket,
        base64_image: str = ""
    ):
        """Generate a pixel-art image by editing the PNG reference and send it to the client."""
        try:
            # 1) Build your styled prompt
            styled_prompt = f"Uma imagem no estilo *modern pixel-art* de: {prompt}"
            print(f"[Image Prompt] {styled_prompt}")

            if not base64_image:
                raise ValueError("No base64_image provided to generate_image()")

            # 2) If it's a file path, load and encode it
            if os.path.isfile(base64_image):
                print(f"[Session {self.session_id}] Loading image from disk at '{base64_image}'")
                with open(base64_image, "rb") as f:
                    raw_bytes = f.read()
                # inline-encode for record (not strictly needed if we skip decoding step)
                base64_image_str = base64.b64encode(raw_bytes).decode("utf-8")
            else:
                # 3) Otherwise assume it's already Base64 or data URL
                base64_image_str = base64_image
                if base64_image_str.startswith("data:"):
                    _, base64_image_str = base64_image_str.split(",", 1)
                raw_bytes = base64.b64decode(base64_image_str)

            # 4) Normalize to an RGBA PNG so the API accepts it
            pil_img = Image.open(io.BytesIO(raw_bytes))
            pil_img = pil_img.convert("RGBA")
            png_buffer = io.BytesIO()
            pil_img.save(png_buffer, format="PNG")
            pil_img.close()
            png_buffer.seek(0)

            # Store the processed image for reference in generate_scene
            self.reference_image_bytes = png_buffer.getvalue()
            self.reference_image_mime = "image/png"
            # Rewind buffer again for sending to this edit API call
            png_buffer.seek(0)

            # 5) Wrap buffer as file-like
            png_buffer.name = "reference.png"

            # 6) Prepare the API args
            api_args = {
                "model": "gpt-image-1",
                "image": ("reference.png", png_buffer, "image/png"),
                "prompt": styled_prompt,
                "n": 1,
                "size": "1024x1024",
                "quality": "high"
            }

            # 7) Run the blocking call in a thread
            response = await asyncio.to_thread(client.images.edit, **api_args)

            # 8) Extract & send back the result
            image_b64 = response.data[0].b64_json
            
            if websocket.client_state == WebSocketState.CONNECTED: 
                try:
                    await websocket.send_text(json.dumps({
                        "type": "image",
                        "content": image_b64,
                        "turn_id": turn_id
                    }))
                except RuntimeError as e:
                    if "after sending \'websocket.close\'." in str(e):
                        print(f"[Session {getattr(self, 'session_id', 'unknown')}] Failed to send image for turn {turn_id}: WebSocket closed before send.")
                    else:
                        raise # Re-raise other RuntimeErrors
            else:
                print(f"[Session {getattr(self, 'session_id', 'unknown')}] WebSocket no longer connected. Skipping sending generated initial image for turn {turn_id}.")

        except asyncio.CancelledError:
            print(f"[Session {getattr(self, 'session_id', 'unknown')}] generate_image task was cancelled for turn {turn_id}.")
            # Optionally re-raise if the cancellation needs to propagate further, but often for background tasks, just logging is fine.
        except Exception as e:
            error_msg = f"Error generating image: {e}"
            print(f"[Session {getattr(self, 'session_id', 'unknown')}] {error_msg}")
            if websocket.client_state == WebSocketState.CONNECTED: 
                try:
                    await websocket.send_text(json.dumps({
                        "type": "error",
                        "content": error_msg,
                        "turn_id": turn_id
                    }))
                except RuntimeError as e_send:
                    if "after sending \'websocket.close\'." in str(e_send):
                        print(f"[Session {getattr(self, 'session_id', 'unknown')}] Failed to send error for turn {turn_id} (initial image): WebSocket closed before send.")
                    else:
                        raise # Re-raise other RuntimeErrors
            else:
                print(f"[Session {getattr(self, 'session_id', 'unknown')}] WebSocket no longer connected. Skipping sending error for initial image for turn {turn_id}.")

    async def generate_scene(self, prompt: str, turn_id: int, websocket: WebSocket):
        """Generate a new scene image using Aurora's reference, a style image, and potentially other character images."""
        try:
            if not self.reference_image_bytes or not self.reference_image_mime:
                error_msg = "Cannot generate scene: Aurora's reference image not available. Initial image generation might have failed."
                print(f"[Session {self.session_id}] {error_msg}")
                if websocket.client_state == WebSocketState.CONNECTED:
                    await websocket.send_text(json.dumps({
                        "type": "error", "content": error_msg, "turn_id": turn_id
                    }))
                return

            # Prepare image inputs
            image_files_for_api = [
                ("aurora.png", io.BytesIO(self.reference_image_bytes), self.reference_image_mime)
            ]

            # Attempt to load and add scene_style.png
            scene_style_image_loaded = False
            scene_style_path = "images/scene_style.png" 
            if os.path.isfile(scene_style_path):
                try:
                    with open(scene_style_path, "rb") as f:
                        raw_bytes = f.read()
                    pil_img = Image.open(io.BytesIO(raw_bytes)).convert("RGBA")
                    png_buffer = io.BytesIO()
                    pil_img.save(png_buffer, format="PNG")
                    pil_img.close()
                    png_buffer.seek(0)
                    image_files_for_api.append(
                        ("scene_style.png", png_buffer, "image/png")
                    )
                    scene_style_image_loaded = True
                except Exception as e:
                    print(f"[Session {self.session_id}] Error loading/processing scene_style.png at {scene_style_path}: {e}")
            else:
                print(f"[Session {self.session_id}] scene_style.png not found at {scene_style_path}. Proceeding without it.")

            # Add other characters if present
            other_characters_to_load = [char_name for char_name in self.current_characters_in_scene if char_name != "aurora"]
            for char_name in other_characters_to_load:
                char_image_path = CHARACTER_IMAGE_PATHS.get(char_name)
                if char_image_path and os.path.isfile(char_image_path):
                    try:
                        with open(char_image_path, "rb") as f:
                            raw_bytes = f.read()
                        pil_img = Image.open(io.BytesIO(raw_bytes)).convert("RGBA")
                        png_buffer = io.BytesIO()
                        pil_img.save(png_buffer, format="PNG")
                        pil_img.close()
                        png_buffer.seek(0)
                        image_files_for_api.append(
                            (f"{char_name}.png", png_buffer, "image/png")
                        )
                    except Exception as e:
                        print(f"[Session {self.session_id}] Error loading/processing image for {char_name} at {char_image_path}: {e}")
                elif char_image_path:
                    print(f"[Session {self.session_id}] Image file not found for {char_name} at {char_image_path}. Skipping.")
                else:
                    print(f"[Session {self.session_id}] No image path defined for character: {char_name}. Skipping.")

            # Construct the prompt safely
            other_character_references_segment = ""
            if other_characters_to_load:
                # Create a string like ", 'barbara.png', 'davi.png'"
                examples = [f"'{name}.png'" for name in other_characters_to_load[:2]] # Show up to 2 examples
                other_character_references_segment = f", {', '.join(examples)}"
                if len(other_characters_to_load) > 2:
                    other_character_references_segment += ", etc."
            
            if scene_style_image_loaded:
                styled_prompt = (
                    f"Create a detailed pixel art scene (use 'scene_style.png' for style). "
                    f"Other reference images show character appearances (e.g., 'aurora.png'{other_character_references_segment}). "
                    f"The scene content to depict is: {prompt}"
                )
            else: # No scene_style_image
                styled_prompt = (
                    f"Create a detailed pixel art scene. "
                    f"Reference images show character appearances (e.g., 'aurora.png'{other_character_references_segment}). "
                    f"The scene content to depict is: {prompt}"
                )
            
            print(f"[Scene Prompt Used] {styled_prompt}")
            print(f"[Session {self.session_id}] Characters in scene for image prompt: {self.current_characters_in_scene}")

            image_input_for_api = image_files_for_api[0] 
            if len(image_files_for_api) > 1:
                #print(f"[Session {self.session_id}] EXPERIMENTAL: Attempting to send {len(image_files_for_api)} images to client.images.edit.")
                image_input_for_api = image_files_for_api
            else:
                print(f"[Session {self.session_id}] Sending only Aurora's base image to client.images.edit.")

            api_args = {
                "model": "gpt-image-1",
                "image": image_input_for_api, 
                "prompt": styled_prompt,
                "n": 1,
                "size": "1024x1024",
                "quality": "high"
            }

            response = await asyncio.to_thread(client.images.edit, **api_args)
            image_b64 = response.data[0].b64_json

            if websocket.client_state == WebSocketState.CONNECTED:
                try:
                    await websocket.send_text(json.dumps({
                        "type": "image",
                        "content": image_b64,
                        "turn_id": turn_id
                    }))
                except RuntimeError as e:
                    if "after sending \'websocket.close\'." in str(e):
                        print(f"[Session {getattr(self, 'session_id', 'unknown')}] Failed to send scene image for turn {turn_id}: WebSocket closed before send.")
                    else:
                        raise # Re-raise other RuntimeErrors
            else:
                print(f"[Session {getattr(self, 'session_id', 'unknown')}] WebSocket no longer connected. Skipping sending generated scene image for turn {turn_id}.")

        except asyncio.CancelledError:
            print(f"[Session {getattr(self, 'session_id', 'unknown')}] generate_scene task was cancelled for turn {turn_id}.")
        except Exception as e:
            error_msg = f"Error generating scene image: {e}"
            print(f"[Session {getattr(self, 'session_id', 'unknown')}] {error_msg}")
            if websocket.client_state == WebSocketState.CONNECTED:
                try:
                    await websocket.send_text(json.dumps({
                        "type": "error",
                        "content": error_msg,
                        "turn_id": turn_id
                    }))
                except RuntimeError as e_send:
                    if "after sending \'websocket.close\'." in str(e_send):
                        print(f"[Session {getattr(self, 'session_id', 'unknown')}] Failed to send error for turn {turn_id} (scene image): WebSocket closed before send.")
                    else:
                        raise # Re-raise other RuntimeErrors 
            else:
                print(f"[Session {getattr(self, 'session_id', 'unknown')}] WebSocket no longer connected. Skipping sending error for scene image for turn {turn_id}.")

@app.websocket("/ws/{session_id}")
async def websocket_endpoint(websocket: WebSocket, session_id: str):
    await websocket.accept()

    # Create or retrieve a session
    if session_id not in connected_clients:
        connected_clients[session_id] = RPGSession(session_id)
    # else: # If session exists, re-initialize key game state if needed, or rely on __init__ if always new
        # pass # For now, we assume new session_id means new RPGSession instance

    session = connected_clients[session_id]

    try:
        if not session.game_concluded: 
            await session.start_game(websocket)
        else:
            if websocket.client_state == WebSocketState.CONNECTED:
                await websocket.send_text(json.dumps({"type": "text", "content": session.current_narration or "The story had already concluded."}))
                await websocket.send_text(json.dumps({"type": "game_end", "message": "This story has already concluded."}))
            return 

        # Main game loop
        while True:
            if session.game_concluded:
                print(f"[Session {session_id}] Game is concluded based on session flag, preparing to break WebSocket receive loop.")
                break 

            data = await websocket.receive_text()
            try:
                user_data = json.loads(data)
                choice = user_data.get("choice", "")
                turn_id = user_data.get("turn_id") 
                
                if choice and turn_id is not None:
                    await session.process_user_choice(choice, turn_id, websocket)
                elif choice:
                    print(f"[Session {session_id}] Received choice '{choice}' without a turn_id. Ignoring.")
            except json.JSONDecodeError:
                if websocket.client_state == WebSocketState.CONNECTED:
                    await websocket.send_text(json.dumps({
                        "type": "error",
                        "content": "Invalid input format. Expected JSON with 'choice' field."
                    }))
    
    finally:
        # This block will execute whether the try block completes normally or an exception occurs (including WebSocketDisconnect)
        print(f"[Session {session_id}] WebSocket endpoint finishing. Game concluded: {session.game_concluded}")
        
        # Wait for any remaining background tasks to finalize
        # This is crucial to allow final image generation to attempt sending
        if session.background_tasks:
            print(f"[Session {session_id}] Waiting for {len(session.background_tasks)} background tasks to finalize before closing...")
            await asyncio.gather(*list(session.background_tasks), return_exceptions=True) # Ensure it's a list if modifying elsewhere
            print(f"[Session {session_id}] Background tasks finalized.")

        # Send game_end message if the game concluded and connection is still up
        if session.game_concluded and websocket.client_state == WebSocketState.CONNECTED:
            try:
                print(f"[Session {session_id}] Sending final game_end message from endpoint.")
                await websocket.send_text(json.dumps({"type": "game_end", "message": "The story has concluded."}))
            except RuntimeError as e_send:
                if "after sending 'websocket.close'." in str(e_send):
                    print(f"[Session {session_id}] Failed to send final game_end message: WebSocket closed before send.")
                else:
                    print(f"[Session {session_id}] RuntimeError sending final game_end: {e_send}") # Other runtime error
            except Exception as e_final_send:
                print(f"[Session {session_id}] Exception sending final game_end: {e_final_send}")

        # Clean up the session from connected_clients if it exists
        # This might be redundant if WebSocketDisconnect already handled it, but good for general exceptions.
        if session_id in connected_clients:
            print(f"[Session {session_id}] Cleaning up session from finally block.")
            # Cancel tasks again just in case some were added very late, though unlikely with current flow
            for task in list(session.background_tasks):
                task.cancel()
            # No need to gather again if we gathered above, unless new tasks could be added.
            # For simplicity, we assume tasks gathered above are sufficient or cancellation is idempotent.
            del connected_clients[session_id]
            print(f"[Session {session_id}] Session cleaned up from finally block.")
        
        print(f"[Session {session_id}] WebSocket connection handler fully exiting.")

    # except WebSocketDisconnect: # This specific exception is handled by the finally block too now
    #     print(f"WebSocket disconnected for session: {session_id}")
    #     # Cleanup is now centralized in the finally block
    # except Exception as e: # General exceptions also lean on the finally block
    #     print(f"Unexpected error in WebSocket handler for {session_id}: {e}")
    #     # Cleanup is now centralized in the finally block

# Mount static files
app.mount("/", StaticFiles(directory="static", html=True), name="static")

if __name__ == "__main__":
    import uvicorn

    uvicorn.run("app:app", host="0.0.0.0", port=8000, reload=True)
