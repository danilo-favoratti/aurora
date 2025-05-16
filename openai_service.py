import asyncio
from typing import List, Dict, Any, AsyncGenerator, Tuple, Union

from config import client # OpenAI client
import io # For image file handling

async def edit_image_with_openai(
    image_bytes: bytes,
    image_mime: str,
    image_filename: str, # e.g., "reference.png"
    prompt: str,
    session_id: str # For logging context
) -> str | None: # Returns base64 JSON string of the image or None
    """Generates an image by editing a base image using OpenAI API."""
    try:
        png_buffer = io.BytesIO(image_bytes)
        png_buffer.name = image_filename

        api_args = {
            "model": "gpt-image-1",
            "image": (png_buffer.name, png_buffer, image_mime),
            "prompt": prompt,
            "n": 1,
            "size": "1024x1024",
            "quality": "high"
        }
        response = await asyncio.to_thread(client.images.edit, **api_args)
        return response.data[0].b64_json
    except Exception as e:
        print(f"[OpenAI Service][Session {session_id}] !!! OpenAI API Call Error (Image Edit): {e}")
        return None

async def edit_image_with_multiple_inputs_openai(
    image_files_for_api: List[Tuple[str, io.BytesIO, str]],
    prompt: str,
    session_id: str
) -> str | None:
    """Generates an image by editing, potentially using multiple input images if the API/library supports it."""
    try:
        if not image_files_for_api:
            print(f"[OpenAI Service][Session {session_id}] No image files provided for editing.")
            return None

        image_input_param: Union[Tuple[str, io.BytesIO, str], List[Tuple[str, io.BytesIO, str]]]

        if len(image_files_for_api) == 1:
            image_input_param = image_files_for_api[0]
        else: # More than one image
            image_input_param = image_files_for_api # Pass the list of tuples directly

        api_args = {
            "model": "gpt-image-1",
            "image": image_input_param, # This will now be the list if multiple images are present
            "prompt": prompt,
            "n": 1,
            "size": "1024x1024",
            "quality": "high"
        }
        response = await asyncio.to_thread(client.images.edit, **api_args)
        return response.data[0].b64_json
    except Exception as e:
        print(f"[OpenAI Service][Session {session_id}] !!! OpenAI API Call Error (Multi-Input Image Edit Attempt): {e}")
        return None 