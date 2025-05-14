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
    CHARACTER_IMAGE_PATHS,
    MAX_GAME_TURNS,
    INTRO_PROMPT,
    INITIAL_CHOICES, 
    INITIAL_IMAGE_PROMPT,
    USE_PLACEHOLDER_INITIAL_IMAGE,
    DETAILED_CHARACTER_DESCRIPTIONS,
    IMAGE_STYLE_GUIDE, # Import the new style guide
    get_debug_image_repeat_status, # Import the getter
)

# Image utilities import
from image_utils import (
    load_image_from_path,
    process_base64_image,
    get_placeholder_image_data
)

# Import for OpenAI Agents SDK
from agents import Agent, Runner

# Import the OpenAI service and the custom exception
from openai_service import (
    edit_image_with_openai,
    edit_image_with_multiple_inputs_openai
)

# Import from the new agent_service
from openai_agent_service import (
    initialize_storyteller_agent,
    get_agent_story_response,
    StoryResponse,
    GameContext,
    Character,
    QuestState
)

class RPGSession:
    def __init__(self, session_id: str):
        self.session_id = session_id

        self.messages = []
        
        self.current_narration = ""
        self.current_choices = []
        self.current_image_prompt = ""
        self.current_characters_in_scene = []
        self.background_tasks = set()
        self.reference_image_bytes: bytes | None = None # This will be updated after each image generation
        self.reference_image_mime: str | None = None    # Should typically remain 'image/png'
        self.turn_number = 0
        self.game_concluded = False
        self.storyteller_agent = initialize_storyteller_agent()
        self.theme_selected = False
        self.objectives_explained = False
        self.game_objectives_narration: str | None = None
        self.last_assistant_response_json: str | None = None
        
        # Initialize game context
        self.game_context = GameContext()
        # Add Aurora as the initial character
        self.game_context.characters.append(Character(
            name="aurora",
            description=DETAILED_CHARACTER_DESCRIPTIONS.get("aurora", "Aurora, the main character"),
            in_scene=True
        ))
        
        self.runner = Runner()
        self.runner.agent = self.storyteller_agent
        self.runner.context = self.game_context
        
        self.first_image_b64 = None  # Store the first image's base64 for debug repeat
        
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

        raw_user_choice = choice # Keep the original choice for logging if needed
        current_input_for_agent = ""

        if not self.theme_selected:
            self.theme_selected = True
            self.turn_number = 1 # This is the first gameplay turn number
            self.game_context.current_turn = 1
            self.game_context.theme = raw_user_choice
            print(f"[Session {self.session_id}] Theme selected: {raw_user_choice}. Processing as Turn Number: {self.turn_number}. Requesting objective explanation.")
            current_input_for_agent = f"O tema do jogo foi escolhido: '{raw_user_choice}'. Com base neste tema, configure o jogo (ambiente, entidades, quest) conforme suas instruções. IMPORTANTE: Em sua narração para ESTA PRIMEIRA RODADA DE JOGO APÓS A ESCOLHA DO TEMA, você DEVE começar explicando os objetivos gerais do jogo. Após explicar os objetivos, descreva o cenário inicial e forneça as primeiras opções de jogo."
        else:
            self.turn_number += 1
            self.game_context.current_turn = self.turn_number
            print(f"[Session {self.session_id}] Processing Turn Number: {self.turn_number}")
            if self.turn_number >= MAX_GAME_TURNS:
                self.game_concluded = True
                print(f"[Session {self.session_id}] Max turns reached ({MAX_GAME_TURNS}). Concluding game.")
                current_input_for_agent = "A história está chegando ao fim. Forneça uma narração final conclusiva. Não ofereça escolhas. Diga que apesar dos objetivos iniciais não terem sido alcançados, o objetivo de se divertir é o principal e esse foi atingido!"
            else:
                # Construct a more focused objective reminder
                pending_objectives_texts = []
                if self.game_context.objectives_initialized and self.game_context.objectives:
                    for obj in self.game_context.objectives:
                        if not obj.finished:
                            pending_objectives_texts.append(f"ID {obj.id}: {obj.objective}")
                
                if pending_objectives_texts:
                    objective_reminder = "Lembrete dos objetivos PENDENTES: " + "; ".join(pending_objectives_texts) + "."
                elif self.game_context.objectives_initialized:
                    objective_reminder = "Todos os objetivos iniciais parecem estar concluídos! Verifique se a quest deve terminar ou se há algo mais a fazer."
                else:
                    objective_reminder = "Objetivos ainda não foram definidos."

                previous_narration_summary = "Resumo da cena anterior: Nenhum ainda." # Default if no last response
                if self.last_assistant_response_json:
                    try:
                        last_response_data = json.loads(self.last_assistant_response_json)
                        previous_narration_summary = "Resumo da cena anterior: " + last_response_data.get("narration", "Não foi possível obter a narração anterior.")[:150] # Summary
                    except json.JSONDecodeError:
                        previous_narration_summary = "Resumo da cena anterior: (erro ao processar narração anterior)"
                
                current_input_for_agent = (
                    f"{objective_reminder}\n\n"
                    f"{previous_narration_summary}\n\n"
                    f"A escolha do jogador para esta rodada foi: '{raw_user_choice}'.\n"
                    f"Continue a história a partir daqui, descrevendo o resultado desta escolha e o novo estado da cena. Forneça novas opções. Não explique os objetivos do jogo novamente."
                )
        
        self.current_narration = ""
        self.current_choices = []
        self.current_image_prompt = ""
        
        agent_response_object: StoryResponse | None = None
        try:
            # openai_agent_service currently uses input=current_input_for_agent, 
            # and conversation_history is just for logging in openai_agent_service.
            # The Agent SDK is expected to make the self.storyteller_agent stateful.
            agent_response_object = await get_agent_story_response(
                self.runner,
                self.game_context,
                current_input_for_agent, 
                list(self.messages), # Pass current history for context (openai_agent_service currently only logs its length)
                self.session_id
            )
            if agent_response_object is None:
                raise Exception("Agent service returned no response or an error occurred in service.")

            # Log the actual input sent to the agent for this turn, then the agent's response.
            self.messages.append({"role": "user", "content": current_input_for_agent}) 
            self.last_assistant_response_json = agent_response_object.model_dump_json()
            self.messages.append({"role": "assistant", "content": self.last_assistant_response_json})

            if self.turn_number == 1 and not self.objectives_explained:
                self.objectives_explained = True
                self.game_objectives_narration = agent_response_object.narration

            # Process Pydantic response and send to client
            self.current_narration = agent_response_object.narration
            self.current_choices = agent_response_object.choices
            self.current_image_prompt = agent_response_object.image_prompt
            self.current_characters_in_scene = agent_response_object.characters_in_scene
            
            # Send objectives to client FIRST, so it's up-to-date before narration of potential final turn
            if websocket.client_state == WebSocketState.CONNECTED:
                objectives_data = []
                for obj in self.game_context.objectives: 
                    obj_data = {"id": obj.id, "objective": obj.objective, "finished": obj.finished}
                    if obj.target_count is not None:
                        obj_data["target_count"] = obj.target_count
                        obj_data["current_count"] = obj.current_count
                    if obj.partially_complete: 
                        obj_data["partially_complete"] = obj.partially_complete
                    objectives_data.append(obj_data)
                
                print(f"[Session {self.session_id}] DEBUG: Sending objectives_data to client (Turn {turn_id}): {objectives_data}")
                await websocket.send_text(json.dumps({
                    "type": "objectives",
                    "content": objectives_data,
                    "turn_id": turn_id 
                }))

            # Now check for game conclusion based on objectives *after* agent might have updated them
            if self.game_context.quest_state == QuestState.COMPLETED and not self.game_concluded:
                self.game_concluded = True # Set game_concluded here
                print(f"[Session {self.session_id}] All objectives completed! Game concluding this turn (Turn {turn_id}).")
                # Agent should have provided a final narration. Choices should be empty.
                # If not, the agent didn't follow instructions for final turn properly.
                if self.current_choices and len(self.current_choices) > 0:
                    print(f"[Session {self.session_id}] WARNING: Game is concluding, but agent provided choices: {self.current_choices}. Clearing them.")
                    self.current_choices = [] # Ensure no choices on game end
            
            print(f"[Session {self.session_id}] Parsed characters in scene: {self.current_characters_in_scene}")

            if websocket.client_state == WebSocketState.CONNECTED and self.current_narration:
                await websocket.send_text(json.dumps({"type": "narration_block", "content": self.current_narration, "turn_id": turn_id }))

            if self.current_image_prompt and websocket.client_state == WebSocketState.CONNECTED:
                print(f"[Session {self.session_id}] Triggering image generation for prompt: '{self.current_image_prompt}' with characters: {self.current_characters_in_scene}")
                self._create_background_task(self.generate_scene(self.current_image_prompt, turn_id, websocket))
            elif not self.current_image_prompt:
                 print(f"[Session {self.session_id}] No image prompt. Skipping image generation.")

            if websocket.client_state == WebSocketState.CONNECTED:
                # Only send choices if the game is NOT concluded in this very turn
                if not self.game_concluded and self.current_choices:
                    await websocket.send_text(json.dumps({"type": "choices", "content": self.current_choices, "turn_id": turn_id}))
                elif self.game_concluded:
                    print(f"[Session {self.session_id}] Game concluded this turn. No choices will be sent.")
            else:
                print(f"[Session {self.session_id}] Skipping sending choices/narration: WebSocket disconnected.")
        except Exception as e: 
            error_msg = f"Error processing agent Pydantic response: {str(e)}"
            print(f"[Session {self.session_id}] !!! {error_msg} (Response object was: {str(agent_response_object)[:500]})")
            if websocket.client_state == WebSocketState.CONNECTED:
                try: await websocket.send_text(json.dumps({"type": "error", "content": "Server error processing agent response.", "turn_id": turn_id}))
                except Exception as send_e: print(f"[Session {self.session_id}] Error sending generic processing error: {send_e}")

    async def start_game(self, websocket: WebSocket):
        self.turn_number = 0 # Initial state before any theme choice is processed by agent
        self.game_concluded = False
        self.theme_selected = False # Reset flag
        self.objectives_explained = False # Reset this flag too
        self.messages = [] # Clear message history for a new game
        self.game_objectives_narration = None
        self.last_assistant_response_json = None
        self.game_context = GameContext()  # Reset game context
        self.game_context.characters.append(Character(
            name="aurora",
            description=DETAILED_CHARACTER_DESCRIPTIONS.get("aurora", "Aurora, the main character"),
            in_scene=True
        ))
        initial_turn_id_for_theme_selection = 0 # This is for the theme selection UI turn
        
        initial_narration = INTRO_PROMPT # e.g., "Escolha seu Tema"
        initial_choices_list = []
        try: 
            initial_choices_list = json.loads(INITIAL_CHOICES) # Theme options
        except json.JSONDecodeError:
            print(f"[Session {self.session_id}] Error decoding INITIAL_CHOICES. Using default. Value: {INITIAL_CHOICES}")
            initial_choices_list = ["Fallback Theme 1", "Fallback Theme 2"]
        
        # This is for the image accompanying the theme selection, not from agent yet.
        initial_image_prompt_text = INITIAL_IMAGE_PROMPT 

        # Send initial narration (theme prompt)
        if websocket.client_state == WebSocketState.CONNECTED:
            await websocket.send_text(json.dumps({"type": "narration_block", "content": initial_narration, "turn_id": initial_turn_id_for_theme_selection}))

        # Image for theme selection screen
        if websocket.client_state == WebSocketState.CONNECTED:
            if USE_PLACEHOLDER_INITIAL_IMAGE or not initial_image_prompt_text: 
                print(f"[Session {self.session_id}] Using placeholder for initial theme selection image.")
                img_bytes, img_mime, b64_placeholder = get_placeholder_image_data("images/aurora_first_image.png")
                if img_bytes and img_mime and b64_placeholder:
                    self.reference_image_bytes = img_bytes
                    self.reference_image_mime = img_mime
                    # Store the placeholder as the first image if debug repeat might be used
                    if self.first_image_b64 is None:
                        self.first_image_b64 = b64_placeholder 
                        print(f"[Session {self.session_id}] Stored placeholder as first_image_b64 for debug repeat.")
                    try: await websocket.send_text(json.dumps({"type": "image", "content": b64_placeholder, "turn_id": initial_turn_id_for_theme_selection}))
                    except Exception as e: 
                        error_msg = f"Error sending placeholder: {e}"
                        print(f"[Session {self.session_id}] {error_msg}")
                        await websocket.send_text(json.dumps({"type": "error", "content": error_msg, "turn_id": initial_turn_id_for_theme_selection}))
                        return
                else: 
                    error_msg = "Error loading placeholder image for theme selection."
                    print(f"[Session {self.session_id}] {error_msg}")
                    await websocket.send_text(json.dumps({"type": "error", "content": error_msg, "turn_id": initial_turn_id_for_theme_selection}))
                    return
            else:
                print(f"[Session {self.session_id}] Generating initial image for theme selection from prompt: '{initial_image_prompt_text[:50]}...'")
                # This generate_image call sets self.reference_image_bytes to Aurora's initial edited image
                self._create_background_task(self.generate_image(initial_image_prompt_text, "auto", initial_turn_id_for_theme_selection, websocket, base64_image="images/aurora.png"))
        else:
            print(f"Skipping initial image/placeholder for theme selection: WebSocket disconnected.")
            return 
        
        # Send initial choices (theme options)
        if websocket.client_state == WebSocketState.CONNECTED:
            await websocket.send_text(json.dumps({"type": "choices", "content": initial_choices_list, "turn_id": initial_turn_id_for_theme_selection}))
        
        # Log the setup for theme selection (not an agent response)
        initial_setup_log = json.dumps({
            "narration": initial_narration,
            "choices": initial_choices_list,
            "image_prompt": "Theme selection screen - initial image prompt used: " + initial_image_prompt_text
        })
        self.messages.append({"role": "assistant", "content": initial_setup_log})

    async def generate_image(self, prompt: str, background: str, turn_id: int, websocket: WebSocket, base64_image: str = ""):
        try:
            # Prepend common style guide to the specific prompt for this image
            final_prompt = f"{IMAGE_STYLE_GUIDE}\n\nScene details: {prompt}"
            print(f"[Image Prompt with Style Guide] {final_prompt}")
            if not base64_image: raise ValueError("No base64_image provided to generate_image()")

            processed_image_bytes, processed_image_mime = None, None
            if os.path.exists(base64_image):
                 processed_image_bytes, processed_image_mime = load_image_from_path(base64_image)
            else:
                 processed_image_bytes, processed_image_mime = process_base64_image(base64_image)

            if not processed_image_bytes or not processed_image_mime: raise ValueError("Failed to load/process base image.")
            
            # This method generates the INITIAL reference image (e.g. Aurora)
            # Its output becomes the first self.reference_image_bytes
            self.reference_image_bytes = processed_image_bytes 
            self.reference_image_mime = processed_image_mime
            
            image_b64 = await edit_image_with_openai(
                image_bytes=self.reference_image_bytes,
                image_mime=self.reference_image_mime,
                image_filename="reference.png",
                prompt=final_prompt, # Use the combined prompt
                session_id=self.session_id
            )
            if image_b64 is None: 
                raise Exception("OpenAI image editing failed in service for generate_image.")

            # Store the first image for debug repeat
            if self.first_image_b64 is None:
                self.first_image_b64 = image_b64

            # After successful generation of the *initial* edited image (e.g., Aurora based on default prompt),
            # update self.reference_image_bytes with this new image so generate_scene can use it.
            new_image_bytes = base64.b64decode(image_b64)
            # Further process it to ensure it's a valid PNG for the next edit round, if necessary,
            # though edit_image_with_openai should return b64_json of a PNG.
            # For simplicity, let's assume b64_json is a direct PNG bytes source after decode.
            self.reference_image_bytes = new_image_bytes 
            # self.reference_image_mime typically remains "image/png"
            print(f"[Session {self.session_id}] self.reference_image_bytes updated by generate_image output.")

            if websocket.client_state == WebSocketState.CONNECTED:
                try: await websocket.send_text(json.dumps({"type": "image", "content": image_b64, "turn_id": turn_id}))
                except RuntimeError as e: 
                    if "after sending 'websocket.close'." in str(e): print(f"[S {self.session_id}] Failed to send image for T{turn_id}: WS closed.")
                    else: raise
            else: print(f"[S {self.session_id}] WS no longer connected. Skipping send generated initial image for T{turn_id}.")
        except asyncio.CancelledError: print(f"[S {self.session_id}] generate_image task cancelled for T{turn_id}.")
        except Exception as e:
            error_msg = f"Error generating image: {e}"
            print(f"[S {self.session_id}] {error_msg}")
            if websocket.client_state == WebSocketState.CONNECTED:
                try: await websocket.send_text(json.dumps({"type": "error", "content": error_msg, "turn_id": turn_id}))
                except RuntimeError as e_send:
                    if "after sending 'websocket.close'." in str(e_send): print(f"[S {self.session_id}] Failed to send error for T{turn_id} (initial image): WS closed.")
                    else: raise
            else: print(f"[S {self.session_id}] WS no longer connected. Skipping send error for initial image for T{turn_id}.")

    async def generate_scene(self, prompt: str, turn_id: int, websocket: WebSocket):
        try:
            # Debug: Repeat the first image if flag is enabled by calling the getter
            if get_debug_image_repeat_status() and self.first_image_b64 is not None:
                print(f"[Session {self.session_id}] DEBUG_IMAGE_REPEAT via getter is ON. Reusing first image for turn {turn_id}.")
                if websocket.client_state == WebSocketState.CONNECTED:
                    await websocket.send_text(json.dumps({"type": "image", "content": self.first_image_b64, "turn_id": turn_id}))
                return

            if not self.reference_image_bytes or not self.reference_image_mime:
                error_msg = "Cannot generate scene: Previous image (self.reference_image_bytes) is not available."
                print(f"[Session {self.session_id}] {error_msg}")
                if websocket.client_state == WebSocketState.CONNECTED:
                    await websocket.send_text(json.dumps({"type": "error", "content": error_msg, "turn_id": turn_id}))
                return

            # Start with the output of the previous turn as the base image to edit.
            # The service function `edit_image_with_multiple_inputs_openai` expects a list of tuples:
            # (filename, BytesIO_object, mime_type)
            api_image_inputs = [
                ("previous_scene_output.png", io.BytesIO(self.reference_image_bytes), self.reference_image_mime)
            ]
            print(f"[S {self.session_id}] Using previous scene output as the first image for editing.")

            # Add original reference images for other characters in the scene for context.
            # These are passed in the list, but the API will likely only use the first as the primary editable image.
            # Their main influence will be through the detailed text prompt.
            characters_to_add_references_for = [cn for cn in self.current_characters_in_scene]
            # If Aurora is in current_characters_in_scene, her original reference could be added too,
            # in addition to her being part of previous_scene_output.png.
            # Or, we rely on previous_scene_output.png to carry her visual forward.
            # Let's add original references for all characters listed, including Aurora if present.
            # The service will decide if it sends one or many (currently configured to send many if >1).

            temp_filenames_for_logging = ["previous_scene_output.png"] 

            for char_name in characters_to_add_references_for: # Iterate through all chars in scene
                # Skip adding previous_scene_output again if char_name is aurora and we consider previous_scene_output as her latest version.
                # However, adding original references might help guide the prompt interpretation.
                # For now, let's add original references for all specified characters if not already the base.
                
                is_base_image_aurora = (char_name == "aurora") # Assuming Aurora is inherently in reference_image_bytes
                
                # If we want to send original character files as additional context:
                char_image_path = CHARACTER_IMAGE_PATHS.get(char_name)
                if char_image_path:
                    # Only add if not Aurora AND Aurora is the base (to avoid sending aurora.png if previous_scene is already her)
                    # This logic gets complex. Simpler: always send previous scene + original refs of chars in scene.
                    # The `edit_image_with_multiple_inputs_openai` handles the list.
                    
                    # Let's ensure we don't add the *exact same BytesIO object* if Aurora is the base
                    # and also in current_characters_in_scene.
                    # The primary image for editing is already api_image_inputs[0]
                    # Add other character *original* reference images.
                    if char_name == "aurora" and api_image_inputs[0][0] == "previous_scene_output.png":
                        # We are already sending previous scene which contains Aurora.
                        # Optionally, we could add the *original* images/aurora.png for strong reference.
                        # For now, let's assume previous_scene_output carries Aurora forward from the edit.
                        # If other characters are present, their original files are added.
                        print(f"[S {self.session_id}] Aurora is part of the base image to be edited. Her original ref won't be added separately unless logic changes.")
                    elif char_name != "aurora": # Add original reference for other characters
                        char_bytes, char_mime = load_image_from_path(char_image_path)
                        if char_bytes and char_mime:
                            api_image_inputs.append((f"{char_name}_original_ref.png", io.BytesIO(char_bytes), char_mime))
                            temp_filenames_for_logging.append(f"{char_name}_original_ref.png")
                            print(f"[S {self.session_id}] Added {char_name}_original_ref.png for context.")
                        else: 
                            print(f"[S {self.session_id}] Original image for {char_name} not found/loaded.")
                else: 
                    print(f"[S {self.session_id}] No image path for char: {char_name}.")
            
            # Construct the text prompt
            prompt_character_descriptions = []
            for char_name in self.current_characters_in_scene:
                if char_name in DETAILED_CHARACTER_DESCRIPTIONS:
                    prompt_character_descriptions.append(DETAILED_CHARACTER_DESCRIPTIONS[char_name])
                else:
                    prompt_character_descriptions.append(char_name)
            characters_for_prompt_string = ". ".join(prompt_character_descriptions)
            
            final_scene_prompt_text = f"{IMAGE_STYLE_GUIDE}\n\nCharacters to include: {characters_for_prompt_string}.\nScene details based on story: {prompt}"
                                   
            # print(f"[Scene Prompt Used] {final_scene_prompt_text}")
            print(f"[S {self.session_id}] Images sent to service: {temp_filenames_for_logging}")

            # Call the service function that can handle a list of images
            image_b64 = await edit_image_with_multiple_inputs_openai(
                image_files_for_api=api_image_inputs, 
                prompt=final_scene_prompt_text,
                session_id=self.session_id
            )

            if image_b64 is None: 
                raise Exception("OpenAI multi-image editing failed in service for generate_scene.")

            # CRUCIAL: Update self.reference_image_bytes with the NEWLY generated scene for the NEXT turn.
            self.reference_image_bytes = base64.b64decode(image_b64)
            self.reference_image_mime = "image/png" # Assuming service returns PNG
            print(f"[Session {self.session_id}] self.reference_image_bytes updated by generate_scene output for turn {turn_id}.")

            if websocket.client_state == WebSocketState.CONNECTED:
                try: await websocket.send_text(json.dumps({"type": "image", "content": image_b64, "turn_id": turn_id}))
                except RuntimeError as e:
                    if "after sending 'websocket.close'." in str(e): print(f"[S {self.session_id}] Failed to send scene image for T{turn_id}: WS closed.")
                    else: raise
            else: print(f"[S {self.session_id}] WS no longer connected. Skipping send generated scene image for T{turn_id}.")
        except asyncio.CancelledError: print(f"[S {self.session_id}] generate_scene task cancelled for T{turn_id}.")
        except Exception as e:
            error_msg = f"Error generating scene image: {e}"
            print(f"[S {self.session_id}] {error_msg}")
            if websocket.client_state == WebSocketState.CONNECTED:
                try: await websocket.send_text(json.dumps({"type": "error", "content": error_msg, "turn_id": turn_id}))
                except RuntimeError as e_send:
                    if "after sending 'websocket.close'." in str(e_send): print(f"[S {self.session_id}] Failed to send error for T{turn_id} (scene image): WS closed.")
                    else: raise 
            else: print(f"[S {self.session_id}] WS no longer connected. Skipping send error for scene image for T{turn_id}.") 