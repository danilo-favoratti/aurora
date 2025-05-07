# 🎮 Aurora's Pixel RPG Storyteller

A real-time RPG storytelling application using AI to generate narratives and pixel-art scenes.

## ✨ Features

- 📖 Interactive storytelling with AI narration
- 🔄 Real-time text streaming (token-by-token)
- 🎨 8-bit pixel-art image generation per scene
- 🎮 User choice-based gameplay (no text input)
- ⚡ Fast, seamless WebSocket communication

## 🛠️ Tech Stack

- **Backend**: Python, FastAPI, WebSockets, asyncio
- **Frontend**: HTML/CSS/JavaScript
- **AI**: OpenAI GPT-4 (text generation) and DALL·E (image generation)

## 🚀 Setup & Running

### Prerequisites

- Python 3.11+
- OpenAI API key

### Installation

1. Clone the repository
```bash
git clone <repository-url>
cd pixel-rpg-storyteller
```

2. Create a virtual environment and activate it
```bash
python3 -m venv venv
source venv/bin/activate  # On Windows: venv\Scripts\activate
```

3. Install dependencies
```bash
pip install -r requirements.txt
```

4. Create a `.env` file with your OpenAI API key
```
OPENAI_API_KEY=your_openai_api_key_here
```

### Running the Application

Start the FastAPI server:
```bash
python3 app.py
```

Access the application in your browser at: `http://localhost:8000`

## 🎮 How to Play

1. Wait for the initial scene to load
2. Read the narration as it streams in
3. Choose from 2-4 options to continue the story
4. Enjoy the pixel-art images generated for each scene

## 🧩 Project Structure

- `app.py` - FastAPI server and WebSocket implementation
- `static/` - Frontend files
  - `index.html` - Main HTML structure
  - `styles.css` - NES-inspired styling
  - `script.js` - WebSocket client and UI handling

## 📝 Notes

- Uses OpenAI's streaming API for real-time text generation
- Triggers image generation during text streaming for parallelism
- Enforces JSON format for AI responses to maintain structure
- All images are in 8-bit pixel-art style

## 🔒 Future Enhancements

- Audio narration with text-to-speech
- Save/load game progress
- Enhanced game mechanics with tool calling
- Multiplayer capabilities 