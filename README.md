<img width="402" height="200" alt="Screenshot 2026-04-20 at 12 46 24 PM" src="https://github.com/user-attachments/assets/4cd72260-e741-4ee7-92c0-ccaeee66e20d" />
<img width="401" height="898" alt="Screenshot 2026-04-20 at 12 43 11 PM" src="https://github.com/user-attachments/assets/a26218d9-9cf7-4fca-9a57-8b3f5097757f" />
<img width="521" height="134" alt="Screenshot 2026-04-20 at 12 41 07 PM" src="https://github.com/user-attachments/assets/11ecaa98-1067-44ee-ab5e-2dfc32aecb4e" />
# AI Eye

AI Eye is a macOS AI overlay that lets you chat, code, and give acess to whatever's on screen to personal assistant AI models from one floating panel. Completely Free! and Unlimited! tokan limit, Helpful for studies and coding tasks.
Can also use ollama AI modals locally but makes PC slow, suggested to use the api-keys.

On other screens, press `z` to toggle audio to ask question. and press `x` to add screenshot.

## What it does

- Screenshot questions route to `Groq` vision
- Text chat routes to `Groq` llama-3.3-70b-versatile by default
- Coding mode supports `OpenRouter` models and `Groq` for llama-3.3
- Direct `DeepSeek` support and local `Ollama` support
- Streaming responses, Markdown rendering, and a draggable bubble UI


| Preview Models | Coding Models |
| :---: | :---: |
| <img src="https://github.com/user-attachments/assets/a26218d9-9cf7-4fca-9a57-8b3f5097757f" width="550"> | <img src="https://github.com/user-attachments/assets/868c9025-17c6-4cb5-971c-4ea73cee5080" width="550"> |
<img src="https://github.com/user-attachments/assets/5997e146-d79a-4d61-b826-2d54c025b5bc" />
<img src="https://github.com/user-attachments/assets/11ecaa98-1067-44ee-ab5e-2dfc32aecb4e" width="550"> <img src="https://github.com/user-attachments/assets/4cd72260-e741-4ee7-92c0-ccaeee66e20d" width="550">


## AI models used

- **Groq text**: `llama-3.3-70b-versatile`
- **Groq vision**: `llama-3.2-11b-vision-instruct`
- **Gemini**: `gemini-2.0-flash-exp` and optional `gemini-1.5-flash` (yet not working due to billing issues)
- **OpenRouter coding**: `deepseek/deepseek-chat`, `amazon/nova-lite-v1`, `mistralai/mistral-7b-instruct:free`, `meta-llama/llama-3.3-70b-instruct:free`
- **DeepSeek direct**: `deepseek/deepseek-chat`
- **Ollama local**: `llama3.2-vision`

## Setup
Open terminal and run it:
```bash
cd Desktop
git pull https://github.com/abhijeet11-8/ai_eye.git
```

To install automatically (recommended), run the installer script:
```bash
./install.sh
```
or,
```bash
chmod +x ./install.sh
```
here you can download Ollama and any of its local models to run it locally or just type y/n.

For model & app setup:

1.  **Install dependencies:**
    `pip install -r requirements.txt`
    then,
    `brew install portaudio`
    if brew not installed,
    `/bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"`
2.  **Create the config file:**
    ```bash
    cd ai_eye
    cat <<EOF > ~/.ai_eye.json
    {
    "provider": "groq",
    "groq_key": "groq-key",
    "groq_model": "meta-llama/llama-4-scout-17b-16e-instruct",
    "groq_vision_model": "meta-llama/llama-4-scout-17b-16e-instruct",
    "gemini_key": "gemini-key",
    "gemini_model": "gemini-2.0-flash-exp",
    "ollama_host": "http://localhost:11434",
    "ollama_model": "llama3.2-vision",
    "openrouter_key": "openrouter-key",
    "openrouter_model": "mistralai/mistral-7b-instruct:free",
    "deepseek_key": "deepseek-key",
    "deepseek_model": "deepseek/deepseek-chat",
    "llama3_model": "meta-llama/llama-3.3-70b-versatile"
    }
    EOF
    ```
3.  **Secure and Edit:**
    `chmod 600 ~/.ai_eye.json && nano ~/.ai_eye.json`
    Get your api keys from here edit the .ai_eye.json file:
    [groq](https://console.groq.com/keys),
    [openrouter](https://openrouter.ai/settings/management-keys),
    [deepseek](https://platform.deepseek.com/api_keys)

    To edit run in terminal:
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
5. Terminal will run the file and "eye_emoji" chat opens up.
6. Terminate the terminal and it will run in background.
7. To quit permanently or maximize: Press `Quit Ai Eye` and `Show / Hide Panel` in  the top bar to minimize click the cross or minimize button on chat.

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
- Teaching, Personal Assistant, coding modes
- Agent like gemini for MAC
- normal chatting mode

## Files in this repo

- `ai_eye.py` — main macOS overlay app
- `Launch_AI_Eye.command` — launcher script for macOS
- `install.sh` — installer helper
- `requirements.txt` — Python dependencies
- `README.md` — this file
- `.gitignore` — ignored files and secrets
