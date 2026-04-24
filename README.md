# AI Eye

AI Eye is a macOS AI overlay that lets you chat, code, and give access to whatever's on screen to personal assistant AI models from one floating panel. Completely Free! and Unlimited! token limit. Helpful for studies and coding tasks.
Can also use Ollama AI models locally but makes PC slow, suggested to use the API keys.

On other screens, press `z` to toggle audio to ask a question, and press `x` to add a screenshot.

## What it does

- Screenshot questions route to `Groq` vision (`llama-4-scout`)
- Text chat routes to `Groq` llama-4-scout by default
- Voice input via `z` key — records speech, transcribes with **Groq Whisper-large-v3**, and sends to the selected model automatically
- Voice works with screenshot mode (attaches the screenshot + your transcribed question)
- Coding mode supports `OpenRouter` models and `Groq` for llama-3.3
- Direct `DeepSeek` support and local `Ollama` support
- Streaming responses, Markdown rendering, and a draggable bubble UI


| Preview Models | Coding Models |
| :---: | :---: |
| <img src="https://github.com/user-attachments/assets/1cd3e4fe-d481-474a-a3d2-175d8e0930e2" width="550"> | <img src="https://github.com/user-attachments/assets/868c9025-17c6-4cb5-971c-4ea73cee5080" width="550"> |
<img src="https://github.com/user-attachments/assets/5997e146-d79a-4d61-b826-2d54c025b5bc" />

<img src="https://github.com/user-attachments/assets/11ecaa98-1067-44ee-ab5e-2dfc32aecb4e" width="400"> <img src="https://github.com/user-attachments/assets/4cd72260-e741-4ee7-92c0-ccaeee66e20d" width="400">


## AI models used

- **Groq text**: `meta-llama/llama-4-scout-17b-16e-instruct`
- **Groq vision**: `meta-llama/llama-4-scout-17b-16e-instruct`
- **Groq STT**: `whisper-large-v3`
- **Gemini**: `gemini-2.0-flash-exp` and optional `gemini-1.5-flash` (yet not working due to billing issues)
- **OpenRouter coding**: `deepseek/deepseek-chat`, `amazon/nova-lite-v1`, `mistralai/mistral-7b-instruct:free`, `meta-llama/llama-3.3-70b-instruct:free`
- **DeepSeek direct**: `deepseek/deepseek-chat`
- **Ollama local**: `llama3.2-vision`

## Setup
Open terminal and run it:
```bash
cd Desktop
git pull https://github.com/abhijeet11-8/ai_eye.git
cd ai_eye
```

To install automatically (recommended), run the installer script:
if brew not installed:
```bash
/bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"
```
then,
```bash
brew install portaudio
```

you need to make install.sh executable first:
```bash
chmod +x ./install.sh
```
then,
```bash
./install.sh
```
Here you can download Ollama and any of its local models to run locally, or just type y/n to skip.

For model & app setup:

1.  **Install dependencies:**
    ```bash
    pip install -r requirements.txt
    ```
3.  **Create the config file:**
    ```bash
    cat <<EOF > ~/.ai_eye.json
    {
      "provider": "groq",
      "groq_key": "groq-key",
      "groq_model": "meta-llama/llama-4-scout-17b-16e-instruct",
      "groq_vision_model": "meta-llama/llama-4-scout-17b-16e-instruct",
      "groq_whisper_model": "whisper-large-v3",
      "gemini_key": "gemini-key",
      "gemini_model": "gemini-2.0-flash-exp",
      "ollama_host": "http://localhost:11434",
      "ollama_model": "llama3.2-vision",
      "openrouter_key": "openrouter-key",
      "openrouter_model": "deepseek/deepseek-chat",
      "deepseek_key": "deepseek-key",
      "deepseek_model": "deepseek/deepseek-chat"
    }
    EOF
    ```
4.  **Secure and Edit:**
    ```bash
    chmod 600 ~/.ai_eye.json && nano ~/.ai_eye.json
    ```
    Get your API keys from teh links down below and paste them into:
    ```bash
    .ai_eye.json
    ```
    - [Groq](https://console.groq.com/keys)
    - [OpenRouter](https://openrouter.ai/settings/management-keys)
    - [DeepSeek](https://platform.deepseek.com/api_keys)

    To edit later, run in terminal:
    `nano ~/.ai_eye.json`

## Running on macOS

If `Launch_AI_Eye.command` does not run directly, use Terminal:

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
5. Terminal will run the file and the "eye_emoji" chat opens up.
6. Terminate the terminal window and it will run in the background.
7. To quit permanently: Press `Quit AI Eye` in the top bar. To show/hide the panel: Press `Show / Hide Panel`. To minimize to bubble: click the close or minimize button on the chat.

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
- Teaching, Personal Assistant, and coding modes
- Agent-like Gemini for Mac
- Normal chatting mode

## Files in this repo

- `ai_eye.py` — main macOS overlay app (v4)
- `Launch_AI_Eye.command` — launcher script for macOS
- `install.sh` — installer helper
- `requirements.txt` — Python dependencies
- `README.md` — this file
- `.gitignore` — ignored files and secrets
