import os
import json
import asyncio
from typing import List, Dict, Any
from dotenv import load_dotenv
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from openai import OpenAI

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
        return "You are a helpful assistant. Respond in JSON." # Basic fallback

SYSTEM_PROMPT = load_system_prompt()

app = FastAPI()

# Store active connections
connected_clients = {}

# Initial game introduction (Load from .env, with fallback)
INTRO_PROMPT = os.getenv(
    "INTRO_PROMPT",
    "You stand at a crossroads. Adventure awaits. (Default intro)"
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

    async def process_user_choice(self, choice: str, websocket: WebSocket):
        """Process a user's choice and generate the next story segment."""
        # Add the user's choice to the message history
        self.messages.append({"role": "user", "content": choice})
        
        # Reset the current state
        self.current_narration = ""
        self.current_choices = []
        self.current_image_prompt = ""
        self.complete_response = ""
        
        # Call OpenAI API with streaming
        stream = await asyncio.to_thread(
            client.chat.completions.create,
            model="gpt-4",
            messages=self.messages,
            stream=True
        )
        
        # Variables to track if image generation has been triggered
        image_generation_triggered = False
        token_count = 0
        first_sentence_ended = False
        in_narration_field = False
        narration_started = False
        possible_narration = ""
        
        # Buffer for JSON parsing attempts
        json_buffer = ""
        
        async for chunk in self._async_generator(stream):
            if hasattr(chunk.choices[0].delta, 'content') and chunk.choices[0].delta.content is not None:
                token = chunk.choices[0].delta.content
                token_count += 1
                self.complete_response += token
                json_buffer += token
                
                # Try to detect when we're in the narration field of the JSON
                if '"narration"' in json_buffer and not narration_started:
                    parts = json_buffer.split('"narration"')
                    if len(parts) > 1 and ':' in parts[1]:
                        narration_started = True
                        possible_narration = parts[1].split(':', 1)[1].lstrip().lstrip('"')
                elif narration_started:
                    # Look for end of narration field (either end quotes or comma after quotes)
                    if possible_narration.endswith('",'):
                        # End of narration detected with comma
                        in_narration_field = False
                        narration_text = possible_narration[:-2]  # Remove trailing ",
                        # Send only the completed narration text
                        await websocket.send_text(json.dumps({"type": "text", "content": narration_text}))
                        possible_narration = ""
                    elif possible_narration.endswith('"') and '":"' not in possible_narration[-4:]:
                        # Check if this is actually the end quote (not part of another field)
                        following = json_buffer[len(json_buffer) - len(possible_narration) - 12:]
                        if '","' in following or '"}' in following:
                            in_narration_field = False
                            narration_text = possible_narration[:-1]  # Remove trailing "
                            # Send only the completed narration text
                            await websocket.send_text(json.dumps({"type": "text", "content": narration_text}))
                            possible_narration = ""
                    else:
                        # Still in narration field
                        possible_narration += token
                        
                        # Check if this is a good place to stream a chunk of text
                        if len(possible_narration) > 5 and (
                            possible_narration.endswith('. ') or 
                            possible_narration.endswith('! ') or
                            possible_narration.endswith('? ') or
                            possible_narration.endswith(',')
                        ):
                            # Stream this chunk
                            await websocket.send_text(json.dumps({"type": "text", "content": token}))
                            # Reset for next chunk of text
                            if not first_sentence_ended and (
                                possible_narration.endswith('. ') or 
                                possible_narration.endswith('! ') or
                                possible_narration.endswith('? ')
                            ):
                                first_sentence_ended = True
                
                # Check if we've reached a good point to start image generation
                if not image_generation_triggered and first_sentence_ended:
                    # Try to extract partial image prompt or generate based on narration so far
                    if "image_prompt" in json_buffer:
                        try:
                            # Try to extract the image_prompt from the partial JSON
                            if '"image_prompt":' in json_buffer:
                                img_prompt_part = json_buffer.split('"image_prompt":', 1)[1].strip()
                                if img_prompt_part.startswith('"'):
                                    # Extract the image prompt from quotes
                                    img_prompt = img_prompt_part.split('"', 2)[1]
                                    asyncio.create_task(self.generate_image(img_prompt, websocket))
                                    image_generation_triggered = True
                        except:
                            # If we can't extract, use the narration itself
                            image_prompt = f"8-bit pixel art of a {possible_narration[:100]}"
                            asyncio.create_task(self.generate_image(image_prompt, websocket))
                            image_generation_triggered = True
                    elif first_sentence_ended and token_count > 30:
                        # Use the narration as a fallback
                        image_prompt = f"8-bit pixel art of a {possible_narration[:100]}"
                        asyncio.create_task(self.generate_image(image_prompt, websocket))
                        image_generation_triggered = True
        
        # Process the complete response
        try:
            response_data = json.loads(self.complete_response)
            self.current_narration = response_data.get("narration", "")
            self.current_choices = response_data.get("choices", [])
            self.current_image_prompt = response_data.get("image_prompt", "")
            
            # If image generation wasn't triggered earlier, do it now
            if not image_generation_triggered:
                asyncio.create_task(self.generate_image(self.current_image_prompt, websocket))
            
            # Send the choices to the client
            await websocket.send_text(json.dumps({
                "type": "choices",
                "content": self.current_choices
            }))
            
            # Add the assistant's response to the message history
            self.messages.append({"role": "assistant", "content": self.complete_response})
        except json.JSONDecodeError:
            # Handle the case where the response isn't valid JSON
            error_msg = "Error: The storyteller's response was not in the expected format."
            await websocket.send_text(json.dumps({"type": "error", "content": error_msg}))

    async def _async_generator(self, stream):
        """Convert sync generator to async generator"""
        for chunk in stream:
            yield chunk
            await asyncio.sleep(0)

    async def generate_image(self, prompt: str, websocket: WebSocket):
        """Generate an image based on the prompt and send it to the client."""
        try:
            # Ensure the prompt is for 8-bit pixel art
            if "8-bit pixel art" not in prompt.lower():
                prompt = f"8-bit pixel art of {prompt}"
            
            # Call the OpenAI Image API
            response = await asyncio.to_thread(
                client.images.generate,
                prompt=prompt,
                n=1,
                size="256x256",
                response_format="b64_json"
            )
            
            # Extract the base64 image
            image_b64 = response.data[0].b64_json
            
            # Send the image to the client
            await websocket.send_text(json.dumps({
                "type": "image",
                "content": image_b64
            }))
        except Exception as e:
            # Handle any errors
            error_msg = f"Error generating image: {str(e)}"
            await websocket.send_text(json.dumps({"type": "error", "content": error_msg}))

    async def start_game(self, websocket: WebSocket):
        """Start a new game session with the introduction."""
        # Define the initial game state
        initial_narration = INTRO_PROMPT
        initial_choices = [
            "Explore deeper into the forest",
            "Climb a tree to get a better view",
            "Call out to see if anyone is nearby",
            "Search your pockets for any useful items"
        ]
        initial_image_prompt = "8-bit pixel art of a mysterious foggy forest with filtered sunlight"

        # Start image generation in the background
        asyncio.create_task(self.generate_image(initial_image_prompt, websocket))

        # Stream the narration token by token (simulated)
        narration_words = initial_narration.split()
        for word in narration_words:
            token = word + " "
            # Send just the text token, not wrapped in any JSON structure
            await websocket.send_text(json.dumps({"type": "text", "content": token}))
            await asyncio.sleep(0.05) # Simulate word-by-word streaming speed

        # Send the initial choices after narration is complete
        await websocket.send_text(json.dumps({
            "type": "choices",
            "content": initial_choices
        }))

        # Prepare the initial assistant message for history (using the full intended JSON structure)
        initial_assistant_message = json.dumps({
            "narration": initial_narration,
            "choices": initial_choices,
            "image_prompt": initial_image_prompt
        })
        self.messages.append({"role": "assistant", "content": initial_assistant_message})


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
                
                if choice:
                    # Process the user's choice
                    await session.process_user_choice(choice, websocket)
            except json.JSONDecodeError:
                # Handle invalid JSON
                await websocket.send_text(json.dumps({
                    "type": "error",
                    "content": "Invalid input format. Expected JSON with 'choice' field."
                }))
    except WebSocketDisconnect:
        # Clean up the session when the user disconnects
        if session_id in connected_clients:
            del connected_clients[session_id]


# Mount static files
app.mount("/", StaticFiles(directory="static", html=True), name="static")


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("app:app", host="0.0.0.0", port=8000, reload=True) 