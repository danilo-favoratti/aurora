import os
from dotenv import load_dotenv
from openai import OpenAI

# Load environment variables
load_dotenv()

# OpenAI Client
client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))

# System Prompt for Storyteller Agent
def load_text_file(file_path: str, fallback_text: str) -> str:
    try:
        with open(file_path, 'r', encoding='utf-8') as f:
            return f.read().strip()
    except FileNotFoundError:
        print(f"Error: File '{file_path}' not found.")
        return fallback_text
    except Exception as e:
        print(f"Error reading file '{file_path}': {e}")
        return fallback_text

SYSTEM_PROMPT = load_text_file("instructions.md", "You are a helpful assistant. Respond in JSON.")

# Common Instructions for Image Generation
IMAGE_STYLE_GUIDE = load_text_file("image-instructions.md", "Generate a pixel art image.")

# Character Image Paths
CHARACTER_IMAGE_PATHS = {
    "aurora": "images/aurora.png",
    "barbara": "images/barbara.png",
    "davi": "images/davi.png",
    "lari": "images/lari.png",
    "danilo": "images/danilo.png",
}

# Detailed Character Descriptions for Image Generation Prompts
DETAILED_CHARACTER_DESCRIPTIONS = {
    "aurora": "Aurora, a one-year-old girl with bright blonde hair in a blue top-knot chuquinha, clear blue eyes, and a consistently cheerful expression, wearing her usual blue dress",
    "davi": "Davi, a tall 6.7 feet man with a slightly cynical but loving smile, Fitted black crew-neck tee, straight-leg mid-wash jeans cuffed at the ankle, and dark-brown leather lace-up boots with a low heel.",
    "barbara": "Barbara, an approachable 5.9 feet woman, brown-eyed woman with a warm smile, hair in a loose bun, wearing pointed-top round glasses, a stud-patched wine-red leather jacket over a white tee, slim black jeans, and knee-high boots, standing tall.",
    "lari": "Lari, a cheerful blonde-tousled 5.6 feet woman woman of about thirty-five beams through turquoise round glasses, her bright blue eyes and wide, toothy smile, wearing a cuffed denim jacket and jeans, a patterned blue scarf, navy sneakers, and a moss-green crossbody pouch.",
    "danilo": "Dandan, a 6.2 feet tall man, dark hair tied in a top-knot, neatly squared jaw softened by a full chin-covering beard with no skin showing, warm complexion, flat-top round-bottom brown sunglasses, earthy-green “MARS” tee, cuffed indigo jeans and rugged olive combat boots."
}

# Game Settings
MAX_GAME_TURNS = 30

# Initial Game State (loaded from .env, with fallbacks)
INTRO_PROMPT = os.getenv(
    "INTRO_PROMPT",
    "Escolha seu Tema"
)
INITIAL_CHOICES = os.getenv(
    "INITIAL_CHOICES",
    '["Roda Gigante", "Algodão Doce", "Fantasia de Borboleta", "Gatinhos Fofos"]'
)
INITIAL_IMAGE_PROMPT = os.getenv(
    "INITIAL_IMAGE_PROMPT",
    "Uma garotinha de 1 ano com chuquinha na cabeça, loira dos olhos claros e cara alegre"
)
USE_PLACEHOLDER_INITIAL_IMAGE = os.getenv("USE_PLACEHOLDER_INITIAL_IMAGE", "false").lower() == "true"

# Debug flag to repeat the first image instead of generating new ones
DEBUG_IMAGE_REPEAT = False

def set_debug_image_repeat(value: bool):
    global DEBUG_IMAGE_REPEAT
    DEBUG_IMAGE_REPEAT = value
    print(f"[Config] DEBUG_IMAGE_REPEAT set to: {DEBUG_IMAGE_REPEAT}")

def get_debug_image_repeat_status() -> bool:
    # print(f"[Config] get_debug_image_repeat_status() returning: {DEBUG_IMAGE_REPEAT}")
    return DEBUG_IMAGE_REPEAT 