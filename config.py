import os
from dotenv import load_dotenv
from openai import OpenAI

# Load environment variables
load_dotenv()

# OpenAI Client
client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))

# System Prompt
def load_system_prompt(file_path: str = "instructions.md") -> str:
    try:
        with open(file_path, 'r') as f:
            return f.read().strip()
    except FileNotFoundError:
        print(f"Error: System prompt file '{file_path}' not found.")
        return "You are a helpful assistant. Respond in JSON."  # Basic fallback

SYSTEM_PROMPT = load_system_prompt()

# Character Image Paths
CHARACTER_IMAGE_PATHS = {
    "aurora": "images/aurora.png",
    "barbara": "images/barbara.png",
    "davi": "images/davi.png",
    "lari": "images/lari.png",
    "danilo": "images/danilo.png",
}

# Game Settings
MAX_GAME_TURNS = 2  # Define the maximum number of turns

# Initial Game State (loaded from .env, with fallbacks)
INTRO_PROMPT = os.getenv(
    "INTRO_PROMPT",
    "Tema: Roda Gigante"
)
INITIAL_CHOICES = os.getenv(
    "INITIAL_CHOICES",
    '["Roda Gigante", "Algodão Doce", "Fantasia de Borboleta", "Gatinhos Fofos"]'
) # This will be a string, needs json.loads in usage
INITIAL_IMAGE_PROMPT = os.getenv(
    "INITIAL_IMAGE_PROMPT",
    "Uma garotinha de 1 ano com chuquinha na cabeça, loira dos olhos claros e cara alegre"
)
USE_PLACEHOLDER_INITIAL_IMAGE = os.getenv("USE_PLACEHOLDER_INITIAL_IMAGE", "false").lower() == "true" 