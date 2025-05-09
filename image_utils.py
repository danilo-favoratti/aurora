import base64
import io
import os
from PIL import Image

def load_image_from_path(file_path: str) -> tuple[bytes | None, str | None]:
    """Loads an image from a file path, converts to RGBA PNG, and returns bytes and MIME type."""
    try:
        if not os.path.isfile(file_path):
            print(f"[Image Utils] File not found at path: {file_path}")
            return None, None
        
        with open(file_path, "rb") as f:
            raw_bytes = f.read()
        
        pil_img = Image.open(io.BytesIO(raw_bytes))
        pil_img = pil_img.convert("RGBA")
        png_buffer = io.BytesIO()
        pil_img.save(png_buffer, format="PNG")
        pil_img.close()
        return png_buffer.getvalue(), "image/png"
    except Exception as e:
        print(f"[Image Utils] Error loading image from path '{file_path}': {e}")
        return None, None

def process_base64_image(base64_image_str: str) -> tuple[bytes | None, str | None]:
    """Processes a Base64 image string (or data URL) to RGBA PNG bytes and MIME type."""
    try:
        if base64_image_str.startswith("data:"):
            # Format: data:image/png;base64,iVBORw0KGgoAAAANS...
            meta, base64_image_str = base64_image_str.split(",", 1)
            # Potentially use meta to get original mime type if needed, though we convert to PNG.
        
        raw_bytes = base64.b64decode(base64_image_str)
        pil_img = Image.open(io.BytesIO(raw_bytes))
        pil_img = pil_img.convert("RGBA")
        png_buffer = io.BytesIO()
        pil_img.save(png_buffer, format="PNG")
        pil_img.close()
        return png_buffer.getvalue(), "image/png"
    except Exception as e:
        print(f"[Image Utils] Error processing base64 image: {e}")
        return None, None

def get_placeholder_image_data(placeholder_path: str = "images/aurora.png") -> tuple[bytes | None, str | None, str | None]:
    """Loads, processes, and base64 encodes a placeholder image."""
    img_bytes, img_mime = load_image_from_path(placeholder_path)
    if img_bytes and img_mime:
        base64_encoded_img = base64.b64encode(img_bytes).decode("utf-8")
        return img_bytes, img_mime, base64_encoded_img
    return None, None, None 