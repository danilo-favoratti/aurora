import asyncio
import json
import os
import base64
import io

from fastapi import WebSocket # Only WebSocket is strictly needed by RPGSession methods
from starlette.websockets import WebSocketState # For checking client_state

# Config imports
from config import (
    # client, # No longer directly used by RPGSession for OpenAI calls
    SYSTEM_PROMPT,
    CHARACTER_IMAGE_PATHS,
    MAX_GAME_TURNS,
    INTRO_PROMPT,
    INITIAL_CHOICES, 
    INITIAL_IMAGE_PROMPT,
    USE_PLACEHOLDER_INITIAL_IMAGE,
    # Potentially add DETAILED_CHARACTER_DESCRIPTIONS here if you move them to config
)

# Image utilities import
from image_utils import (
    load_image_from_path,
    process_base64_image,
    get_placeholder_image_data
)

# Import the OpenAI service and the custom exception
from openai_service import (
    get_chat_completion_stream,
    edit_image_with_openai,
    edit_image_with_multiple_inputs_openai,
    OpenAIServiceError # Import the custom exception
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
        self.background_tasks = set()
        self.reference_image_bytes: bytes | None = None
        self.reference_image_mime: str | None = None
        self.turn_number = 0
        self.game_concluded = False

    def _create_background_task(self, coro):
        """Helper to create, store, and manage cleanup of background tasks."""
        task = asyncio.create_task(coro)
        self.background_tasks.add(task)
        task.add_done_callback(self.background_tasks.discard)
        return task

    async def process_user_choice(self, choice: str, turn_id: int, websocket: WebSocket):
        """Process a user's choice and generate the next story segment or conclude the game."""
        if self.game_concluded:
            print(f"[Session {self.session_id}] Game already concluded. Ignoring choice: {choice}")
            return

        self.turn_number += 1
        print(f"[Session {self.session_id}] Processing Turn Number: {self.turn_number}")

        user_content_for_api = choice
        if self.turn_number >= MAX_GAME_TURNS:
            self.game_concluded = True
            print(f"[Session {self.session_id}] Max turns reached. Concluding game.")
            user_content_for_api = "The story is now coming to an end. Please provide a final concluding narration. Do not offer any choices."

        self.messages.append({"role": "user", "content": user_content_for_api})
        self.current_narration = ""
        self.current_choices = []
        self.current_image_prompt = ""
        self.complete_response = ""

        chat_stream = None # Initialize chat_stream
        try:
            # Get the async generator object by calling the service function (NO await here)
            chat_stream = get_chat_completion_stream(self.messages, self.session_id)
        except OpenAIServiceError as e: # Should not be raised by just calling, but during iteration.
                                      # However, if get_chat_completion_stream had some immediate error before yielding.
            print(f"[Session {self.session_id}] Error obtaining chat stream: {e}")
            if websocket.client_state == WebSocketState.CONNECTED:
                try:
                    await websocket.send_text(json.dumps(
                        {"type": "error", "content": "OpenAI API Error (Chat Setup): Check server logs."}))
                except Exception as send_e:
                    print(f"[Session {self.session_id}] Error sending API error message to client: {send_e}")
            return
        
        token_count = 0
        try:
            # Iterate directly over the async generator returned by the service
            async for chunk_obj in chat_stream:
                if hasattr(chunk_obj.choices[0].delta, 'content') and chunk_obj.choices[0].delta.content is not None:
                    token = chunk_obj.choices[0].delta.content
                    token_count += 1
                    self.complete_response += token
                    if websocket.client_state == WebSocketState.CONNECTED:
                        try:
                            await websocket.send_text(json.dumps({"type": "text", "content": token}))
                        except Exception as send_e:
                            print(f"[Session {self.session_id}] Error sending stream token: {send_e}. Aborting.")
                            for task in list(self.background_tasks): task.cancel()
                            return
                    else:
                        print(f"[Session {self.session_id}] WebSocket disconnected during stream. Aborting.")
                        for task in list(self.background_tasks): task.cancel()
                        return
        except OpenAIServiceError as e: # Catch errors raised during stream generation
            print(f"[Session {self.session_id}] Error during OpenAI stream processing: {e}")
            if websocket.client_state == WebSocketState.CONNECTED:
                try:
                    await websocket.send_text(json.dumps({"type": "error", "content": "Error processing story stream."}))
                except Exception as send_e:
                    print(f"[Session {self.session_id}] Error sending stream processing error to client: {send_e}")
            return

        # Process the complete response
        try:
            cleaned_response = self.complete_response.strip()
            if cleaned_response.startswith("```json"):
                cleaned_response = cleaned_response[7:]
            if cleaned_response.endswith("```"):
                cleaned_response = cleaned_response[:-3]
            cleaned_response = cleaned_response.strip()
            cleaned_response = cleaned_response.replace("\\\\'.", "'.")
            
            response_data = json.loads(cleaned_response)
            self.current_narration = response_data.get("narration", "")
            self.current_choices = response_data.get("choices", [])
            self.current_image_prompt = response_data.get("image_prompt", "")
            self.current_characters_in_scene = response_data.get("characters_in_scene", [])
            print(f"[Session {self.session_id}] Parsed characters in scene: {self.current_characters_in_scene}")

            if self.current_image_prompt and websocket.client_state == WebSocketState.CONNECTED:
                print(f"[Session {self.session_id}] Triggering image generation for prompt: '{self.current_image_prompt}' with characters: {self.current_characters_in_scene}")
                self._create_background_task(self.generate_scene(self.current_image_prompt, turn_id, websocket))
            elif not self.current_image_prompt:
                 print(f"[Session {self.session_id}] No image prompt found. Skipping image generation.")

            if websocket.client_state == WebSocketState.CONNECTED:
                if not self.game_concluded and self.current_choices:
                    await websocket.send_text(json.dumps({"type": "choices", "content": self.current_choices}))
                self.messages.append({"role": "assistant", "content": cleaned_response})
            else:
                print(f"[Session {self.session_id}] Skipping sending choices: WebSocket disconnected.")
        except json.JSONDecodeError as json_err:
            print(f"[Session {self.session_id}] !!! JSON Parsing Error: {json_err} on response: {self.complete_response}")
            error_msg = "Error: Storyteller response format incorrect."
            if websocket.client_state == WebSocketState.CONNECTED:
                try: await websocket.send_text(json.dumps({"type": "error", "content": error_msg, "turn_id": turn_id}))
                except Exception as send_e: print(f"[Session {self.session_id}] Error sending JSON parse error: {send_e}")
        except Exception as e:
            error_msg = f"Error processing response: {str(e)}"
            print(f"[Session {self.session_id}] !!! Error processing response: {e}")
            if websocket.client_state == WebSocketState.CONNECTED:
                try: await websocket.send_text(json.dumps({"type": "error", "content": "Server error processing response.", "turn_id": turn_id}))
                except Exception as send_e: print(f"[Session {self.session_id}] Error sending generic processing error: {send_e}")

    async def start_game(self, websocket: WebSocket):
        self.turn_number = 0
        self.game_concluded = False
        initial_turn_id = 0
        use_placeholder = USE_PLACEHOLDER_INITIAL_IMAGE

        initial_narration = INTRO_PROMPT
        initial_choices_str = INITIAL_CHOICES
        try:
            initial_choices = json.loads(initial_choices_str)
        except json.JSONDecodeError:
            print(f"[Session {self.session_id}] Error decoding INITIAL_CHOICES. Using default. Value: {initial_choices_str}")
            initial_choices = ["Roda Gigante", "Algodão Doce", "Fantasia de Borboleta", "Gatinhos Fofos"]
        initial_image_prompt = INITIAL_IMAGE_PROMPT

        if websocket.client_state == WebSocketState.CONNECTED:
            if use_placeholder:
                print(f"[Session {self.session_id}] Using placeholder for initial image.")
                img_bytes, img_mime, b64_placeholder = get_placeholder_image_data("images/aurora.png")
                if img_bytes and img_mime and b64_placeholder:
                    self.reference_image_bytes = img_bytes
                    self.reference_image_mime = img_mime
                    try: await websocket.send_text(json.dumps({"type": "image", "content": b64_placeholder, "turn_id": initial_turn_id}))
                    except Exception as e: 
                        error_msg = f"Error sending placeholder: {e}"
                        print(f"[Session {self.session_id}] {error_msg}")
                        await websocket.send_text(json.dumps({"type": "error", "content": error_msg, "turn_id": initial_turn_id}))
                        return
                else:
                    error_msg = "Error loading placeholder image via image_utils."
                    print(f"[Session {self.session_id}] {error_msg}")
                    await websocket.send_text(json.dumps({"type": "error", "content": error_msg, "turn_id": initial_turn_id}))
                    return
            else:
                # Call generate_image which now uses the openai_service
                self._create_background_task(self.generate_image(initial_image_prompt, "auto", initial_turn_id, websocket, base64_image="images/aurora.png"))
        else:
            print(f"Skipping initial image/placeholder for {self.session_id}: WebSocket disconnected.")
            return
        initial_assistant_message = json.dumps({"narration": initial_narration, "choices": initial_choices, "image_prompt": initial_image_prompt})
        for char in initial_assistant_message:
            if websocket.client_state != WebSocketState.CONNECTED: print(f"Initial stream interrupted for {self.session_id}: WebSocket disconnected."); return
            await websocket.send_text(json.dumps({"type": "text", "content": char}))
            await asyncio.sleep(0.01)
        if websocket.client_state == WebSocketState.CONNECTED:
            await websocket.send_text(json.dumps({"type": "choices", "content": initial_choices}))
            self.messages.append({"role": "assistant", "content": initial_assistant_message})
        else: print(f"Skipping initial choices for {self.session_id}: WebSocket disconnected.")

    async def generate_image(self, prompt: str, background: str, turn_id: int, websocket: WebSocket, base64_image: str = ""):
        try:
            styled_prompt = f"Uma imagem no estilo *modern pixel-art* de: {prompt}"
            print(f"[Image Prompt] {styled_prompt}")
            if not base64_image: raise ValueError("No base64_image provided to generate_image()")

            processed_image_bytes, processed_image_mime = None, None
            if os.path.exists(base64_image):
                 processed_image_bytes, processed_image_mime = load_image_from_path(base64_image)
            else:
                 processed_image_bytes, processed_image_mime = process_base64_image(base64_image)

            if not processed_image_bytes or not processed_image_mime: raise ValueError("Failed to load/process base image.")
            self.reference_image_bytes = processed_image_bytes
            self.reference_image_mime = processed_image_mime
            
            # Use OpenAI Service for image editing
            image_b64 = await edit_image_with_openai(
                image_bytes=self.reference_image_bytes,
                image_mime=self.reference_image_mime,
                image_filename="reference.png",
                prompt=styled_prompt,
                session_id=self.session_id
            )
            if image_b64 is None: # API call failed in service
                raise Exception("OpenAI image editing failed in service.")
            
            if websocket.client_state == WebSocketState.CONNECTED:
                try: await websocket.send_text(json.dumps({"type": "image", "content": image_b64, "turn_id": turn_id}))
                except RuntimeError as e: 
                    if "after sending \'websocket.close\'." in str(e): print(f"[S {self.session_id}] Failed to send image for T{turn_id}: WS closed.")
                    else: raise
            else: print(f"[S {self.session_id}] WS no longer connected. Skipping send generated initial image for T{turn_id}.")
        except asyncio.CancelledError: print(f"[S {self.session_id}] generate_image task cancelled for T{turn_id}.")
        except Exception as e:
            error_msg = f"Error generating image: {e}"
            print(f"[S {self.session_id}] {error_msg}")
            if websocket.client_state == WebSocketState.CONNECTED:
                try: await websocket.send_text(json.dumps({"type": "error", "content": error_msg, "turn_id": turn_id}))
                except RuntimeError as e_send:
                    if "after sending \'websocket.close\'." in str(e_send): print(f"[S {self.session_id}] Failed to send error for T{turn_id} (initial image): WS closed.")
                    else: raise
            else: print(f"[S {self.session_id}] WS no longer connected. Skipping send error for initial image for T{turn_id}.")

    async def generate_scene(self, prompt: str, turn_id: int, websocket: WebSocket):
        try:
            if not self.reference_image_bytes or not self.reference_image_mime:
                error_msg = "Cannot generate scene: Aurora's reference image not available."
                print(f"[Session {self.session_id}] {error_msg}")
                if websocket.client_state == WebSocketState.CONNECTED:
                    await websocket.send_text(json.dumps({"type": "error", "content": error_msg, "turn_id": turn_id}))
                return

            # Initialize api_image_inputs with Aurora (the base image for editing)
            api_image_inputs = [
                ("aurora.png", io.BytesIO(self.reference_image_bytes), self.reference_image_mime)
            ]
            print(f"[S {self.session_id}] Added aurora.png (base) to API image inputs.")

            scene_style_image_loaded = False
            scene_style_path = "images/scene_style.png"
            style_img_bytes, style_img_mime = load_image_from_path(scene_style_path)
            if style_img_bytes and style_img_mime:
                api_image_inputs.append(("scene_style.png", io.BytesIO(style_img_bytes), style_img_mime))
                scene_style_image_loaded = True
                print(f"[S {self.session_id}] Added scene_style.png to API image inputs.")
            else:
                print(f"[S {self.session_id}] scene_style.png not found/failed to load.")

            # Add images for all other characters currently in the scene
            # self.current_characters_in_scene is set by process_user_choice from LLM response
            other_characters_in_scene = [cn for cn in self.current_characters_in_scene if cn != "aurora"]

            for char_name in other_characters_in_scene:
                char_image_path = CHARACTER_IMAGE_PATHS.get(char_name)
                if char_image_path:
                    char_bytes, char_mime = load_image_from_path(char_image_path)
                    if char_bytes and char_mime:
                        api_image_inputs.append((f"{char_name}.png", io.BytesIO(char_bytes), char_mime))
                        print(f"[S {self.session_id}] Added {char_name}.png to API image inputs.")
                    else: 
                        print(f"[S {self.session_id}] Image file not found or failed to load for {char_name} at {char_image_path}. Skipping this character image.")
                else: 
                    print(f"[S {self.session_id}] No image path defined in CHARACTER_IMAGE_PATHS for character: {char_name}. Skipping this character image.")
            
            # Detailed character descriptions for the prompt (as implemented before)
            detailed_character_desc = {
                "aurora": "Aurora, a one-year-old girl with bright blonde hair in a blue top-knot chuquinha, clear blue eyes, and a consistently cheerful expression, wearing her usual blue dress",
                "davi": "Davi, a tall man with a slightly cynical but loving smile, dark short-cropped hair, often seen wearing a simple dark t-shirt and jeans",
                "barbara": "Barbara, a woman with brown eyes, wise and loving eyes, wearing red t-shirt and blue jeans",
                "lari": "Lari, blonde short hair, blue eyes, wearing a red jacket and dark blue jeans and brown boots",
                "danilo": "Danilo, black hair, brown eyes. wearing a green Mars t-shirt, blue jeans and brown boots"
            }
            prompt_character_descriptions = []
            for char_name in self.current_characters_in_scene:
                if char_name in detailed_character_desc:
                    prompt_character_descriptions.append(detailed_character_desc[char_name])
                else:
                    prompt_character_descriptions.append(char_name)
            characters_for_prompt_string = ". ".join(prompt_character_descriptions)
            base_scene_description = prompt 
            final_scene_prompt_text = f"{characters_for_prompt_string}. Scene: {base_scene_description}"
            style_guidance = "Create a detailed pixel art scene. "
            if scene_style_image_loaded:
                style_guidance = "Create a detailed pixel art scene (use the style of the reference 'scene_style.png', which is pixel art). "
            styled_prompt_scene = style_guidance + final_scene_prompt_text
                                   
            print(f"[Scene Prompt Used] {styled_prompt_scene}")
            print(f"[S {self.session_id}] Characters for DALL-E prompt text: {self.current_characters_in_scene}. Number of images passed to service: {len(api_image_inputs)}")

            image_b64 = await edit_image_with_multiple_inputs_openai(
                image_files_for_api=api_image_inputs, 
                prompt=styled_prompt_scene,
                session_id=self.session_id
            )
            if image_b64 is None: 
                raise Exception("OpenAI multi-image editing failed in service.")

            if websocket.client_state == WebSocketState.CONNECTED:
                try: await websocket.send_text(json.dumps({"type": "image", "content": image_b64, "turn_id": turn_id}))
                except RuntimeError as e:
                    if "after sending \'websocket.close\'." in str(e): print(f"[S {self.session_id}] Failed to send scene image for T{turn_id}: WS closed.")
                    else: raise
            else: print(f"[S {self.session_id}] WS no longer connected. Skipping send generated scene image for T{turn_id}.")
        except asyncio.CancelledError: print(f"[S {self.session_id}] generate_scene task cancelled for T{turn_id}.")
        except Exception as e:
            error_msg = f"Error generating scene image: {e}"
            print(f"[S {self.session_id}] {error_msg}")
            if websocket.client_state == WebSocketState.CONNECTED:
                try: await websocket.send_text(json.dumps({"type": "error", "content": error_msg, "turn_id": turn_id}))
                except RuntimeError as e_send:
                    if "after sending \'websocket.close\'." in str(e_send): print(f"[S {self.session_id}] Failed to send error for T{turn_id} (scene image): WS closed.")
                    else: raise 
            else: print(f"[S {self.session_id}] WS no longer connected. Skipping send error for scene image for T{turn_id}.") 