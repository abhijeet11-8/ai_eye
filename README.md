# AI Eye

AI Eye is a macOS menu-bar AI overlay that lets you chat, code, and send screenshots to AI models from one floating panel. Completely Free!, Helpful for studies and coding tasks.
Can integrate with edge AI's but make sPC slow.

## What it does

- Screenshot questions route to `Groq` vision
- Text chat routes to `Groq` llama-3.3-70b-versatile by default
- Coding mode supports `OpenRouter` models and `Groq` for llama-3.3
- Direct `DeepSeek` support and local `Ollama` support
- Streaming responses, Markdown rendering, and a draggable bubble UI

## AI models used

- **Groq text**: `llama-3.3-70b-versatile`
- **Groq vision**: `llama-3.2-11b-vision-instruct`
- **Gemini**: `gemini-2.0-flash-exp` and optional `gemini-1.5-flash`
- **OpenRouter coding**: `deepseek/deepseek-chat`, `amazon/nova-lite-v1`, `mistralai/mistral-7b-instruct:free`, `meta-llama/llama-3.3-70b-instruct:free`
- **DeepSeek direct**: `deepseek/deepseek-chat`
- **Ollama local**: `llama3.2-vision`

## Setup

1.  **Install dependencies:**
    `pip install -r requirements.txt`
2.  **Create the config file:**
    ```bash
    cat <<EOF > ~/.ai_eye.json
    {
      "provider": "groq",
      "groq_key": "YOUR_GROQ_API_KEY",
      "groq_model": "llama-3.3-70b-versatile",
      "groq_vision_model": "llama-3.2-11b-vision-instruct",
      "gemini_key": "YOUR_GEMINI_KEY",
      "gemini_model": "gemini-2.0-flash-exp",
      "ollama_host": "http://localhost:11434",
      "ollama_model": "llama3.2-vision",
      "openrouter_key": "YOUR_OPENROUTER_KEY",
      "openrouter_model": "deepseek/deepseek-chat",
      "deepseek_key": "YOUR_DEEPSEEK_KEY",
      "deepseek_model": "deepseek/deepseek-chat"
    }
    EOF
    ```
3.  **Secure and Edit:**
    `chmod 600 ~/.ai_eye.json && nano ~/.ai_eye.json`

## Running on macOS

If `Launch_AI_Eye.command` does not open directly, use Terminal:

```bash
chmod +x ~/Desktop/ai_eye/Launch_AI_Eye.command
cd ~/Desktop/ai_eye
./Launch_AI_Eye.command
```

If macOS blocks the command file:

1. Click `Done` when prompted (not `Move to Trash`).
2. Open **System Settings → Privacy & Security**.
3. Scroll down until you see:
   - `"Launch_AI_Eye.command" was blocked from use because it is not from an identified developer.`
4. Click **Open Anyway**.

This is the only way past the dialog without a paid Apple Developer certificate.

## How to use `.ai_eye.json`

- `~/.ai_eye.json` stores your keys and default models.
- Keep it private and do not commit it to git.
- The repo includes `.gitignore` to exclude `.ai_eye.json` and local artifacts.

## Future updates planned

- PDF access
- Multiple images / multi-image support
- MCQ generator
- Questions generator
- Windows support
- ChatGPT and Gemini (currently not working) integration with free limitation handling

## Files in this repo

- `ai_eye.py` — main macOS overlay app
- `Launch_AI_Eye.command` — launcher script for macOS
- `install.sh` — installer helper
- `requirements.txt` — Python dependencies
- `README.md` — this file
- `.gitignore` — ignored files and secrets
