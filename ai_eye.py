#!/usr/bin/env python3
"""
AI Eye v4 — macOS menu-bar AI overlay

Smart routing:
  📷 Screenshot  → Groq vision model   (images via Groq)
  💬 Text only   → Groq llama-4-scout  (fast)
  💻 Coding      → OpenRouter  (DeepSeek · Amazon Nova 2 · Mistral · llama-3.3)
  🔵 DeepSeek    → OpenRouter routed
  🦙 Local       → Ollama
  🎤 Voice       → Groq Whisper-large-v3 STT → any selected model

New in v4:
  • Fixed bubble minimization (drag vs click separated cleanly)
  • Whisper-large-v3 via Groq for speech-to-text
  • Voice toggle button (🎤) — records while held / VAD auto-send on silence
  • Live voice chat: speak → transcribe → send to selected model
  • Voice works with screenshot mode (attaches image + transcribed text)
  • PyAudio-based recording with silence detection (500ms threshold)
  • Global Z key shortcut to toggle voice recording (works from any app)
"""

import sys, os, json, base64, io, threading, requests, subprocess, wave, tempfile, struct, math, time
import objc
from Foundation import NSObject, NSMakeRect, NSTimer, NSPoint, NSMakePoint
from AppKit import (
    NSApplication, NSApp,
    NSMenu, NSMenuItem,
    NSStatusBar,
    NSPanel, NSWindow, NSBackingStoreBuffered,
    NSColor, NSScreen,
    NSVisualEffectView,
    NSApplicationActivationPolicyAccessory,
    NSVariableStatusItemLength,
    NSButton, NSView,
    NSFont, NSEvent,
    NSWindowCollectionBehaviorCanJoinAllSpaces,
    NSStatusWindowLevel,
    NSWindowCollectionBehaviorFullScreenAuxiliary,
    NSEventMaskKeyDown,
    NSScreenSaverWindowLevel
)
try:
    from WebKit import WKWebView, WKWebViewConfiguration, WKUserContentController
except ImportError:
    print("❌  pip install pyobjc-framework-WebKit"); sys.exit(1)

# ── macOS window constants ────────────────────────────────────────
_STYLE_PANEL  = 1 | 2 | 8 | 32768 | 128
_STYLE_BUBBLE = 0                            # borderless
_LEVEL        = 25                           # NSStatusWindowLevel
_BEHAV_PANEL  = 1
from AppKit import (
    NSWindowCollectionBehaviorCanJoinAllSpaces,
    NSWindowCollectionBehaviorFullScreenAuxiliary,
    NSWindowCollectionBehaviorStationary,
    NSWindowCollectionBehaviorTransient,
    NSWindowCollectionBehaviorIgnoresCycle,
)

_BEHAV_BUBBLE = (
    NSWindowCollectionBehaviorCanJoinAllSpaces |
    NSWindowCollectionBehaviorFullScreenAuxiliary |
    NSWindowCollectionBehaviorStationary |
    NSWindowCollectionBehaviorTransient |
    NSWindowCollectionBehaviorIgnoresCycle
)
_VE_MAT       = 7
_VE_BLD       = 0
_VE_STA       = 1
_AUTORESZ     = 18
_BUBBLE_W     = 60
_BUBBLE_H     = 60

# ── Config ────────────────────────────────────────────────────────
CFG_PATH = os.path.expanduser("~/.ai_eye.json")
DEFAULTS = {
    "provider":          "groq",
    "groq_key":          "",
    "groq_model":        "meta-llama/llama-4-scout-17b-16e-instruct",
    "groq_vision_model": "meta-llama/llama-4-scout-17b-16e-instruct",
    "groq_whisper_model":"whisper-large-v3",
    "gemini_key":        "",
    "gemini_model":      "gemini-2.0-flash-exp",
    "ollama_host":       "http://localhost:11434",
    "ollama_model":      "llama3.2-vision",
    "openrouter_key":    "",
    "openrouter_model":  "deepseek/deepseek-chat",
    "deepseek_key":      "",
    "deepseek_model":    "deepseek/deepseek-chat",
}

def load_cfg():
    try:
        with open(CFG_PATH) as f:
            return {**DEFAULTS, **json.load(f)}
    except Exception:
        return DEFAULTS.copy()

def save_cfg(c):
    with open(CFG_PATH, "w") as f:
        json.dump(c, f, indent=2)

# ── Terminal helpers ──────────────────────────────────────────────
def _run_apple(script):
    try:
        subprocess.Popen(["osascript", "-e", script],
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except Exception:
        pass

def hide_terminal():
    _run_apple('''
    tell application "System Events"
        set termApps to {"Terminal","iTerm2","iTerm","Hyper","Warp","Alacritty"}
        repeat with appName in termApps
            if exists process appName then set visible of process appName to false
        end repeat
    end tell
    ''')

def kill_terminal():
    _run_apple('''
    tell application "System Events"
        set termApps to {"Terminal","iTerm2","iTerm","Hyper","Warp","Alacritty"}
        repeat with appName in termApps
            if exists process appName then tell application appName to quit
        end repeat
    end tell
    ''')

# ── Screen capture ────────────────────────────────────────────────
def capture_b64():
    try:
        import mss
        from PIL import Image
        with mss.mss() as s:
            shot = s.grab(s.monitors[0])
            img  = Image.frombytes("RGB", shot.size, shot.bgra, "raw", "BGRX")
            img.thumbnail((1600, 900))
            buf  = io.BytesIO()
            img.save(buf, "JPEG", quality=65)
            return base64.b64encode(buf.getvalue()).decode()
    except Exception as e:
        print(f"Capture error: {e}")
        return None

# ── Whisper STT via Groq ──────────────────────────────────────────
WHISPER_SAMPLE_RATE  = 16000
WHISPER_CHANNELS     = 1
WHISPER_CHUNK        = 1024
WHISPER_FORMAT_INT16 = 8   # pyaudio.paInt16
SILENCE_THRESHOLD    = 500   # RMS below this = silence
SILENCE_DURATION     = 0.5   # seconds of silence before auto-send
MAX_RECORD_SECS      = 30    # hard cap

def _rms(data: bytes) -> float:
    if len(data) < 2:
        return 0.0
    samples = struct.unpack(f"{len(data)//2}h", data)
    return math.sqrt(sum(s*s for s in samples) / len(samples))

def record_until_silence(on_status=None):
    """
    Records from default mic until silence is detected.
    Returns raw PCM bytes (16-bit mono 16 kHz) or None on error.
    """
    try:
        import pyaudio
    except ImportError:
        return None, "pyaudio not installed — run: pip install pyaudio"

    pa = pyaudio.PyAudio()
    stream = pa.open(
        format=pyaudio.paInt16,
        channels=WHISPER_CHANNELS,
        rate=WHISPER_SAMPLE_RATE,
        input=True,
        frames_per_buffer=WHISPER_CHUNK,
    )
    if on_status:
        on_status("🎤 Listening…")

    frames = []
    silence_frames = 0
    silence_limit  = int(SILENCE_DURATION * WHISPER_SAMPLE_RATE / WHISPER_CHUNK)
    max_frames     = int(MAX_RECORD_SECS * WHISPER_SAMPLE_RATE / WHISPER_CHUNK)
    started        = False   # wait for first non-silence

    for _ in range(max_frames):
        data = stream.read(WHISPER_CHUNK, exception_on_overflow=False)
        rms  = _rms(data)
        if rms > SILENCE_THRESHOLD:
            started = True
            silence_frames = 0
        elif started:
            silence_frames += 1

        if started:
            frames.append(data)

        if started and silence_frames >= silence_limit:
            break

    stream.stop_stream()
    stream.close()
    pa.terminate()

    if not frames:
        return None, "No speech detected"

    return b"".join(frames), None

def pcm_to_wav_bytes(pcm: bytes) -> bytes:
    buf = io.BytesIO()
    with wave.open(buf, "wb") as wf:
        wf.setnchannels(WHISPER_CHANNELS)
        wf.setsampwidth(2)   # 16-bit
        wf.setframerate(WHISPER_SAMPLE_RATE)
        wf.writeframes(pcm)
    return buf.getvalue()

def transcribe_groq(wav_bytes: bytes, cfg: dict) -> tuple[str, str]:
    """
    Sends WAV bytes to Groq Whisper-large-v3.
    Returns (transcript, error_or_None).
    """
    key = cfg.get("groq_key", "")
    if not key:
        return "", "No groq_key in config"

    model = cfg.get("groq_whisper_model", "whisper-large-v3")
    headers = {"Authorization": f"Bearer {key}"}
    files   = {"file": ("audio.wav", wav_bytes, "audio/wav")}
    data    = {"model": model, "response_format": "json", "language": "en"}

    try:
        r = requests.post(
            "https://api.groq.com/openai/v1/audio/transcriptions",
            headers=headers, files=files, data=data, timeout=30
        )
        j = r.json()
        if "error" in j:
            return "", j["error"].get("message", str(j["error"]))
        text = j.get("text", "").strip()
        return text, None
    except Exception as e:
        return "", str(e)

# ── AI providers ──────────────────────────────────────────────────
def _ollama(cfg, messages, image_b64, chunk_cb):
    sys_msg = next((m["content"] for m in messages if m["role"] == "system"), "")
    chat    = [dict(m) for m in messages if m["role"] != "system"]
    if image_b64:
        for m in reversed(chat):
            if m["role"] == "user":
                m["images"] = [image_b64]; break
    payload = {"model": cfg["ollama_model"], "messages": chat,
               "stream": chunk_cb is not None}
    if sys_msg: payload["system"] = sys_msg
    try:
        r = requests.post(f"{cfg['ollama_host']}/api/chat",
                          json=payload, stream=bool(chunk_cb), timeout=180)
        if chunk_cb:
            full = ""
            for line in r.iter_lines():
                if not line: continue
                d = json.loads(line)
                t = d.get("message", {}).get("content", "")
                if t: full += t; chunk_cb(t)
                if d.get("done"): break
            return full
        return r.json()["message"]["content"]
    except Exception as e:
        return f"Ollama error: {e}"


def _gemini(cfg, messages, image_b64, chunk_cb):
    key   = cfg["gemini_key"]
    model = cfg["gemini_model"]
    if not key:
        return "⚠️ Add your gemini_key to ~/.ai_eye.json"

    sys_instr = None
    contents  = []
    non_sys   = [m for m in messages if m["role"] != "system"]
    sys_msgs  = [m for m in messages if m["role"] == "system"]
    if sys_msgs:
        sys_instr = {"parts": [{"text": sys_msgs[-1]["content"]}]}
    for idx, m in enumerate(non_sys):
        parts = [{"text": m["content"]}]
        if image_b64 and idx == len(non_sys) - 1 and m["role"] == "user":
            parts.append({"inline_data": {"mime_type": "image/jpeg", "data": image_b64}})
        contents.append({"role": "user" if m["role"] == "user" else "model", "parts": parts})

    body = {"contents": contents}
    if sys_instr:
        body["system_instruction"] = sys_instr

    try:
        use_url = (f"https://generativelanguage.googleapis.com/v1beta"
                   f"/models/{model}:generateContent?key={key}")
        r    = requests.post(use_url, json=body, timeout=90)
        data = r.json()
        if "error" in data:
            return f"⚠️ Gemini error: {data['error'].get('message', str(data['error']))}"
        cands = data.get("candidates", [])
        if not cands:
            reason = data.get("promptFeedback", {}).get("blockReason", "no candidates")
            return f"⚠️ Gemini: {reason}"
        text = cands[0].get("content", {}).get("parts", [{}])[0].get("text", "")
        if not text:
            return "⚠️ Gemini returned empty text"
        if chunk_cb: chunk_cb(text)
        return text
    except Exception as e:
        return f"⚠️ Gemini error: {e}"


def _groq(cfg, messages, chunk_cb=None, image_b64=None):
    key = cfg.get("groq_key", "")
    if not key:
        return "⚠️ Add your groq_key to ~/.ai_eye.json"

    model = cfg["groq_vision_model"] if image_b64 else cfg["groq_model"]
    headers = {
        "Authorization": f"Bearer {key}",
        "Content-Type":  "application/json",
    }

    msgs = []
    last_user_idx = max((i for i, m in enumerate(messages) if m["role"] == "user"), default=-1)
    for i, m in enumerate(messages):
        if m["role"] == "user" and i == last_user_idx and image_b64:
            msgs.append({
                "role":    "user",
                "content": [
                    {"type": "text",      "text": m["content"]},
                    {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{image_b64}"}},
                ]
            })
        else:
            msgs.append({"role": m["role"], "content": m["content"]})

    payload = {
        "model":       model,
        "messages":    msgs,
        "max_tokens":  2048,
        "temperature": 0.7,
        "stream":      chunk_cb is not None,
    }

    def _extract(choice):
        msg = choice.get("message", {})
        content = msg.get("content", choice.get("content", ""))
        if isinstance(content, str): return content
        if isinstance(content, dict):
            return content.get("text", "") or content.get("content", "") or ""
        if isinstance(content, list):
            out = ""
            for item in content:
                if isinstance(item, dict):
                    out += item.get("text", "") or item.get("content", "") or ""
                elif isinstance(item, str):
                    out += item
            return out
        return ""

    try:
        r = requests.post("https://api.groq.com/openai/v1/chat/completions",
                          headers=headers, json=payload,
                          stream=bool(chunk_cb), timeout=60)
        if chunk_cb:
            full = ""
            for raw in r.iter_lines():
                if not raw or raw == b"data: [DONE]": continue
                if isinstance(raw, bytes) and raw.startswith(b"data: "):
                    try:
                        d = json.loads(raw[6:])
                        choice = d.get("choices", [{}])[0]
                        delta  = choice.get("delta", {})
                        content = delta.get("content") or ""
                        if isinstance(content, str) and content:
                            full += content; chunk_cb(content)
                    except Exception:
                        pass
            return full
        data = r.json()
        if "error" in data:
            return f"Groq error: {data['error'].get('message')}"
        choice = data.get("choices", [{}])[0]
        text = _extract(choice)
        if not text:
            return "⚠️ Groq returned empty response"
        return text
    except Exception as e:
        return f"Groq error: {e}"


def _openrouter(cfg, messages, image_b64, chunk_cb):
    key = cfg.get("openrouter_key", "")
    if not key:
        return "⚠️ Add your openrouter_key to ~/.ai_eye.json"

    headers = {
        "Authorization": f"Bearer {key}",
        "Content-Type":  "application/json",
        "HTTP-Referer":  "https://ai-eye.local",
        "X-Title":       "AI Eye",
    }
    msgs = [{"role": m["role"], "content": m["content"]} for m in messages]
    payload = {
        "model":      cfg.get("openrouter_model", "deepseek/deepseek-chat"),
        "messages":   msgs,
        "max_tokens": 4096,
        "stream":     chunk_cb is not None,
    }
    try:
        r = requests.post("https://openrouter.ai/api/v1/chat/completions",
                          headers=headers, json=payload,
                          stream=bool(chunk_cb), timeout=120)
        if chunk_cb:
            full = ""
            for raw in r.iter_lines():
                if not raw: continue
                line = raw.decode() if isinstance(raw, bytes) else raw
                if line in ("data: [DONE]", "[DONE]"): break
                if not line.startswith("data: "): continue
                try:
                    d = json.loads(line[6:])
                    t = d.get("choices", [{}])[0].get("delta", {}).get("content") or ""
                    if t: full += t; chunk_cb(t)
                except Exception:
                    pass
            if not full:
                try:
                    r2 = requests.post("https://openrouter.ai/api/v1/chat/completions",
                                       headers=headers, json={**payload, "stream": False}, timeout=120)
                    d2 = r2.json()
                    if "error" in d2:
                        return f"⚠️ OpenRouter: {d2['error'].get('message', str(d2['error']))}"
                    full = d2["choices"][0]["message"]["content"] or ""
                    if full: chunk_cb(full)
                except Exception as e2:
                    return f"⚠️ OpenRouter fallback error: {e2}"
            return full
        data = r.json()
        if "error" in data:
            return f"⚠️ OpenRouter error: {data['error'].get('message', str(data['error']))}"
        txt = data.get("choices", [{}])[0].get("message", {}).get("content") or ""
        return txt or f"⚠️ OpenRouter empty — model may be unavailable: {cfg.get('openrouter_model')}"
    except Exception as e:
        return f"⚠️ OpenRouter error: {e}"


def ai_call(cfg, messages, image_b64=None, chunk_cb=None):
    p = cfg.get("provider", "groq")
    if p == "ollama":     return _ollama(cfg, messages, image_b64, chunk_cb)
    if p == "gemini":     return _gemini(cfg, messages, image_b64, chunk_cb)
    if p == "groq":       return _groq(cfg, messages, chunk_cb, image_b64)
    if p == "openrouter": return _openrouter(cfg, messages, image_b64, chunk_cb)
    if p == "deepseek":   return _openrouter(cfg, messages, image_b64, chunk_cb)
    return "Unknown provider"


# ── HTML UI ───────────────────────────────────────────────────────
_HTML = r"""<!DOCTYPE html>
<html>
<head>
<meta name="viewport" content="width=device-width,initial-scale=1">
<style>
*,*::before,*::after{box-sizing:border-box;margin:0;padding:0}
:root{
  --c0:rgba(255,255,255,.90);
  --c1:rgba(255,255,255,.42);
  --c2:rgba(255,255,255,.18);
  --c3:rgba(255,255,255,.06);
  --bd:rgba(255,255,255,.08);
  --bd2:rgba(255,255,255,.13);
  --acc:#6366f1;
  --acc-b:rgba(99,102,241,.18);
  --grn:#34d399;
  --grn-b:rgba(52,211,153,.10);
  --prp:#a78bfa;
  --prp-b:rgba(124,58,237,.11);
  --red:#f87171;
  --red-b:rgba(248,113,113,.15);
  --ubg:rgba(79,70,229,.82);
}
html,body{height:100%;background:transparent!important;overflow:hidden;-webkit-font-smoothing:antialiased}
body{display:flex;flex-direction:column;height:100vh;
  font-family:-apple-system,"SF Pro Text",system-ui,sans-serif;
  font-size:13px;line-height:1.5;color:var(--c0)}

/* Header */
.hdr{display:flex;align-items:center;justify-content:space-between;
  height:42px;padding:0 10px;border-bottom:1px solid var(--bd);
  -webkit-app-region:drag;flex-shrink:0}
.hl{display:flex;align-items:center;gap:7px}
.eye{width:24px;height:24px;opacity:.75;display:flex;align-items:center;justify-content:center}
.badge{font-size:11px;font-family:"SF Mono","Menlo",monospace;color:var(--c1);letter-spacing:.025em}
.hr2{display:flex;align-items:center;gap:1px;-webkit-app-region:no-drag}
.ic{width:32px;height:32px;border:none;background:none;cursor:pointer;border-radius:8px;
  color:var(--c2);display:flex;align-items:center;justify-content:center;
  transition:background .12s,color .12s;outline:none}
.ic svg{stroke-width:1.8}
.ic:hover{background:var(--c3);color:var(--c0)}
.ic.snap{color:var(--grn);background:var(--grn-b)}
.ic.snap:hover{background:rgba(52,211,153,.17)}
.ic.code{color:var(--prp);background:var(--prp-b)}
.ic.code:hover{background:rgba(124,58,237,.19)}
.ic.voice{color:var(--red);background:var(--red-b)}
.ic.voice:hover{background:rgba(248,113,113,.22)}
.ic.voice-pulse{color:var(--red);background:var(--red-b);
  animation:vpulse .7s ease-in-out infinite alternate}
@keyframes vpulse{from{background:var(--red-b)}to{background:rgba(248,113,113,.35)}}

/* Model bars */
.model-bar{display:flex;align-items:center;gap:3px;padding:5px 10px;
  border-bottom:1px solid var(--bd);background:rgba(0,0,0,.12);flex-shrink:0;flex-wrap:wrap}
.chip{padding:2px 9px;border-radius:5px;border:1px solid rgba(255,255,255,.09);
  background:none;color:var(--c2);cursor:pointer;font-size:10.5px;
  font-family:"SF Mono","Menlo",monospace;transition:all .12s;outline:none;letter-spacing:.02em}
.chip:hover{color:var(--c0);border-color:var(--bd2)}
.chip.on{background:rgba(99,102,241,.2);color:#a5b4fc;border-color:rgba(99,102,241,.45)}
.chip.code-on{background:rgba(167,139,250,.15);color:#c4b5fd;border-color:rgba(167,139,250,.4)}
.model-label{font-size:10px;color:var(--c2);opacity:.5;margin-right:2px;
  font-family:"SF Mono","Menlo",monospace;letter-spacing:.03em}

/* Voice status bar */
.voice-bar{display:none;align-items:center;justify-content:space-between;
  padding:4px 12px;font-size:11px;border-bottom:1px solid rgba(248,113,113,.12);flex-shrink:0}
.voice-bar.open{display:flex;background:var(--red-b)}
.voice-bar.transcribing{display:flex;background:rgba(99,102,241,.12)}
#voiceStatus{color:var(--c0)}
.voice-cancel{background:none;border:none;color:var(--c2);cursor:pointer;font-size:12px;padding:0}
.voice-cancel:hover{color:var(--c0)}

/* Snap banner */
.snap-banner{display:none;align-items:center;justify-content:space-between;
  padding:4px 12px;font-size:11px;color:var(--grn);background:var(--grn-b);
  border-bottom:1px solid rgba(52,211,153,.1);flex-shrink:0}
.snap-banner.open{display:flex}
.snap-banner button{background:none;border:none;color:var(--c2);cursor:pointer;font-size:12px;padding:0}
.snap-banner button:hover{color:var(--c0)}

/* Messages */
.msgs{flex:1;overflow-y:auto;padding:12px 10px;display:flex;flex-direction:column;
  gap:5px;scroll-behavior:smooth}
.msgs::-webkit-scrollbar{width:3px}
.msgs::-webkit-scrollbar-thumb{background:var(--bd2);border-radius:2px}
.row{display:flex}
.row.u{justify-content:flex-end}
.row.a{justify-content:flex-start}
.row.s{justify-content:center;margin:2px 0}
.bbl{max-width:84%;border-radius:14px;font-size:13px;line-height:1.55;
  word-break:break-word;padding:8px 11px}
.bbl.u{background:var(--ubg);color:#fff;border-radius:14px 14px 3px 14px;white-space:pre-wrap}
.bbl.a{background:var(--c3);border:1px solid var(--bd2);border-radius:14px 14px 14px 3px}
.bbl.s{font-size:11px;color:var(--c2);padding:2px 6px;background:none;border:none}
.bbl.voice-msg{border-left:2px solid var(--red);opacity:.9}

/* Markdown */
.bbl.a pre{background:rgba(0,0,0,.42);border:1px solid rgba(255,255,255,.06);
  border-radius:7px;padding:9px 11px;margin:6px 0;font-size:11px;
  font-family:"SF Mono","Menlo",monospace;overflow-x:auto;white-space:pre}
.bbl.a code{font-size:11px;font-family:"SF Mono","Menlo",monospace}
.bbl.a :not(pre)>code{background:rgba(255,255,255,.08);padding:1px 5px;border-radius:4px}
.bbl.a strong{color:rgba(255,255,255,.95)}
.bbl.a p{margin-bottom:5px}
.bbl.a p:last-child{margin-bottom:0}

/* Typing dots */
.typing{display:flex;gap:4px;padding:8px 11px}
.d{width:4px;height:4px;border-radius:50%;background:var(--c2);
  animation:pulse 1.3s ease-in-out infinite}
.d:nth-child(2){animation-delay:.15s}
.d:nth-child(3){animation-delay:.3s}
@keyframes pulse{0%,80%,100%{opacity:.25;transform:scale(.8)}40%{opacity:1;transform:scale(1)}}

/* Input */
.inp-wrap{padding:9px 10px;border-top:1px solid var(--bd);flex-shrink:0}
.inp-box{display:flex;align-items:flex-end;gap:5px;background:var(--c3);
  border:1px solid var(--bd2);border-radius:12px;padding:4px 4px 4px 11px;
  transition:border-color .15s}
.inp-box:focus-within{border-color:rgba(99,102,241,.4)}
textarea{flex:1;background:transparent;border:none;outline:none;color:var(--c0);
  font-size:13px;font-family:-apple-system,"SF Pro Text",system-ui,sans-serif;
  resize:none;line-height:1.5;padding:5px 0;max-height:96px;min-height:28px}
textarea::placeholder{color:var(--c2)}
.go{width:32px;height:32px;border:none;background:var(--acc);color:#fff;border-radius:9px;
  cursor:pointer;flex-shrink:0;display:flex;align-items:center;justify-content:center;
  margin-bottom:1px;transition:opacity .12s,transform .08s}
.go svg{stroke-width:2.4}
.go:hover{opacity:.85}
.go:active{transform:scale(.91)}
.go:disabled{opacity:.22;cursor:default}

/* Mic button in input */
.mic-btn{width:32px;height:32px;border:none;background:none;cursor:pointer;flex-shrink:0;
  display:flex;align-items:center;justify-content:center;border-radius:9px;margin-bottom:1px;
  color:var(--c2);transition:all .12s;outline:none}
.mic-btn:hover{background:var(--c3);color:var(--c0)}
.mic-btn.active{background:var(--red-b);color:var(--red);animation:vpulse .7s ease-in-out infinite alternate}

/* Z-key shortcut hint badge */
.z-hint{font-size:9.5px;font-family:"SF Mono","Menlo",monospace;
  color:var(--c2);background:rgba(255,255,255,.06);border:1px solid rgba(255,255,255,.09);
  border-radius:4px;padding:1px 5px;letter-spacing:.04em;
  pointer-events:none;user-select:none;line-height:1}

/* Z-key shortcut hint in footer */
.shortcut-hint{font-size:9.5px;color:var(--c2);opacity:.4;text-align:center;
  padding-bottom:3px;font-family:"SF Mono","Menlo",monospace;letter-spacing:.04em}
</style>
</head>
<body>

<div class="hdr">
  <div class="hl">
    <svg class="eye" viewBox="0 0 20 20" fill="none">
      <ellipse cx="10" cy="10" rx="8.5" ry="5" stroke="white" stroke-width="1.8"/>
      <circle cx="10" cy="10" r="2.8" fill="white" opacity=".95"/>
      <circle cx="11" cy="9" r="1.05" fill="rgba(0,0,0,.5)"/>
    </svg>
    <span class="badge" id="badge">groq · ready</span>
  </div>
  <div class="hr2">
    <button class="ic" onclick="clearChat()" title="Clear">
      <svg width="15" height="15" viewBox="0 0 13 13" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round">
        <path d="M1.5 3.5h10M5 3.5V2.5h3v1M4 3.5l.4 7.5h4.2L9 3.5"/>
      </svg>
    </button>
    <button class="ic" id="codeBtn" onclick="toggleCode()" title="Code">
      <svg width="15" height="15" viewBox="0 0 13 13" fill="none" stroke="currentColor" stroke-width="1.9" stroke-linecap="round" stroke-linejoin="round">
        <path d="M4 4L1.5 6.5 4 9M9 4l2.5 2.5L9 9M7.5 2.5l-2 8"/>
      </svg>
    </button>
    <button class="ic" id="snapBtn" onclick="toggleSnap()" title="Screenshot">
      <svg width="15" height="15" viewBox="0 0 13 13" fill="none" stroke="currentColor" stroke-width="1.9" stroke-linecap="round" stroke-linejoin="round">
        <rect x="1" y="3" width="11" height="8" rx="1.2"/>
        <circle cx="6.5" cy="7" r="1.9"/>
        <path d="M4.5 3l.8-1.5h2.4L8.5 3"/>
      </svg>
    </button>
    <button class="ic" id="voiceBtn" onclick="toggleVoice()" title="Voice (Z key)">
      <svg width="15" height="15" viewBox="0 0 13 13" fill="none" stroke="currentColor" stroke-width="1.9" stroke-linecap="round" stroke-linejoin="round">
        <rect x="4.5" y="1" width="4" height="6.5" rx="2"/>
        <path d="M2 7.5a4.5 4.5 0 009 0"/>
        <line x1="6.5" y1="12" x2="6.5" y2="10"/>
      </svg>
    </button>
    <button class="ic" onclick="minimize()" title="Minimize"
      style="font-size:18px;font-weight:500;color:var(--c2);padding-bottom:1px">−</button>
  </div>
</div>

<!-- Provider bar -->
<div class="model-bar" id="modelBar">
  <span class="model-label">provider:</span>
  <button class="chip" id="pv-groq"     onclick="setProvider('groq')">groq</button>
  <button class="chip" id="pv-gemini"   onclick="setProvider('gemini')">gemini</button>
  <button class="chip" id="pv-deepseek" onclick="setProvider('deepseek')">deepseek</button>
</div>
<!-- Coding model bar -->
<div class="model-bar" id="codeBar" style="display:none;background:var(--prp-b);">
  <span class="model-label">code model:</span>
  <button class="chip code-on" id="p0" onclick="pickCode('deepseek/deepseek-chat',0)">deepseek</button>
  <button class="chip"         id="p1" onclick="pickCode('amazon/nova-lite-v1',1)">nova-2</button>
  <button class="chip"         id="p3" onclick="pickCode('meta-llama/llama-3.3-70b-instruct:free',3)">llama-3.3</button>
</div>

<!-- Voice status bar -->
<div class="voice-bar" id="voiceBar">
  <span id="voiceStatus">🎤 Listening…</span>
  <button class="voice-cancel" onclick="cancelVoice()">✕</button>
</div>

<!-- Screenshot banner -->
<div class="snap-banner" id="snapBanner">
  <span>📷 Screenshot attached</span>
  <button onclick="toggleSnap()">✕</button>
</div>

<!-- Messages -->
<div class="msgs" id="msgs">
  <div class="row s"><div class="bbl s">Ready — type, speak 🎤, or press <kbd style="font-family:SF Mono,monospace;background:rgba(255,255,255,.08);border-radius:3px;padding:0 4px">Z</kbd> to toggle voice</div></div>
</div>

<!-- Input -->
<div class="inp-wrap">
  <div class="inp-box">
    <textarea id="inp" rows="1" placeholder="Message… (Z = voice toggle)"
      onkeydown="onKey(event)" oninput="grow(this)"></textarea>
    <button class="mic-btn" id="micInlineBtn" onclick="startVoiceInline()" title="Speak (Z)">
      <svg width="13" height="13" viewBox="0 0 13 13" fill="none" stroke="currentColor" stroke-width="1.9" stroke-linecap="round" stroke-linejoin="round">
        <rect x="4.5" y="1" width="4" height="6.5" rx="2"/>
        <path d="M2 7.5a4.5 4.5 0 009 0"/>
        <line x1="6.5" y1="12" x2="6.5" y2="10"/>
      </svg>
    </button>
    <button class="go" id="sendBtn" onclick="send()">
      <svg width="11" height="11" viewBox="0 0 11 11" fill="none" stroke="white" stroke-width="1.8" stroke-linecap="round">
        <path d="M5.5 9.5V1.5M2 5L5.5 1.5 9 5"/>
      </svg>
    </button>
  </div>
</div>

<script>
var snapOn=false,codeOn=false,voiceOn=false,busy=false,aiEl=null;
var codeModel='deepseek/deepseek-chat';
var codePills=['p0','p1','p2','p3'];
var codeModelMap={
  'deepseek/deepseek-chat':'deepseek',
  'amazon/nova-lite-v1':'nova-2',
  'meta-llama/llama-3.3-70b-instruct:free':'llama-3.3'
};
var currentProvider='groq';
var providerPills=['pv-groq','pv-gemini','pv-deepseek'];

function esc(t){return t.replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;');}

function renderMd(s){
  var out=[];
  var parts=s.split(/(```[\w]*\n?[\s\S]*?```)/g);
  parts.forEach(function(p,i){
    if(i%2===1){
      var m=p.match(/```(\w*)\n?([\s\S]*?)```/);
      if(m){
        var h=m[1]?'<div style="opacity:.3;font-size:9.5px;margin-bottom:4px;letter-spacing:.04em">'+esc(m[1])+'</div>':'';
        out.push('<pre>'+h+esc(m[2].replace(/^\n+|\n+$/g,''))+'</pre>');return;
      }
    }
    p=p.replace(/`([^`\n]+)`/g,function(_,c){return '<code>'+esc(c)+'</code>';});
    p=p.replace(/\*\*([\s\S]*?)\*\*/g,'<strong>$1</strong>');
    p=p.replace(/\n/g,'<br>');
    out.push(p);
  });
  return out.join('');
}

function grow(el){el.style.height='auto';el.style.height=Math.min(el.scrollHeight,96)+'px';}
function scroll(){var m=document.getElementById('msgs');requestAnimationFrame(function(){m.scrollTop=m.scrollHeight;});}
function post(o){window.webkit.messageHandlers.ai.postMessage(JSON.stringify(o));}

function setProvider(p){
  currentProvider=p;
  providerPills.forEach(function(id){
    var el=document.getElementById(id);
    if(el) el.className='chip'+(id==='pv-'+p?' on':'');
  });
  updateBadge();
  post({action:'setProvider',provider:p});
}

function updateBadgeFromNative(p){
  currentProvider=p;
  providerPills.forEach(function(id){
    var el=document.getElementById(id);
    if(el) el.className='chip'+(id==='pv-'+p?' on':'');
  });
  updateBadge();
}

function toggleSnap(){
  snapOn=!snapOn;
  document.getElementById('snapBtn').className='ic'+(snapOn?' snap':'');
  document.getElementById('snapBanner').className='snap-banner'+(snapOn?' open':'');
  updateBadge();
}
function toggleCode(){
  codeOn=!codeOn;
  document.getElementById('codeBtn').className='ic'+(codeOn?' code':'');
  document.getElementById('modelBar').style.display=codeOn?'none':'flex';
  document.getElementById('codeBar').style.display=codeOn?'flex':'none';
  updateBadge();
}
function pickCode(m,i){
  codeModel=m;
  codePills.forEach(function(id,j){
    var el=document.getElementById(id);
    if(el) el.className='chip'+(j===i?' code-on':'');
  });
  updateBadge();
}

// ── Voice ──────────────────────────────────────────────────────
function toggleVoice(){
  if(voiceOn){cancelVoice();return;}
  voiceOn=true;
  document.getElementById('voiceBtn').className='ic voice-pulse';
  document.getElementById('micInlineBtn').className='mic-btn active';
  document.getElementById('voiceBar').className='voice-bar open';
  document.getElementById('voiceStatus').textContent='🎤 Listening…';
  post({action:'startVoice',snap:snapOn,coding_mode:codeOn,coding_model:codeModel});
}

// Called from native Z-key shortcut
function nativeVoiceToggle(){toggleVoice();}

function startVoiceInline(){
  if(voiceOn)return;
  toggleVoice();
}

function cancelVoice(){
  voiceOn=false;
  document.getElementById('voiceBtn').className='ic';
  document.getElementById('micInlineBtn').className='mic-btn';
  document.getElementById('voiceBar').className='voice-bar';
  post({action:'cancelVoice'});
}

function voiceStatus(msg, isTranscribing){
  document.getElementById('voiceStatus').textContent=msg;
  var bar=document.getElementById('voiceBar');
  if(isTranscribing){bar.className='voice-bar transcribing open';}
  else{bar.className='voice-bar open';}
}

function voiceDone(transcript){
  voiceOn=false;
  document.getElementById('voiceBtn').className='ic';
  document.getElementById('micInlineBtn').className='mic-btn';
  document.getElementById('voiceBar').className='voice-bar';
  if(transcript){
    document.getElementById('inp').value=transcript;
    grow(document.getElementById('inp'));
    // auto-send
    send();
  }
}

function voiceErr(msg){
  voiceOn=false;
  document.getElementById('voiceBtn').className='ic';
  document.getElementById('micInlineBtn').className='mic-btn';
  document.getElementById('voiceBar').className='voice-bar';
  addSys('🎤 '+msg);
}

// ─────────────────────────────────────────────────────────────

function clearChat(){
  document.getElementById('msgs').innerHTML='<div class="row s"><div class="bbl s">Cleared</div></div>';
  aiEl=null;post({action:'clearHistory'});
}
function minimize(){post({action:'minimize'});}
function updateBadge(){
  var b=document.getElementById('badge');
  if(codeOn) b.textContent='code · '+(codeModelMap[codeModel]||codeModel);
  else if(snapOn) b.textContent='vision · groq';
  else b.textContent=currentProvider+' · ready';
}
function onKey(e){if(e.key==='Enter'&&!e.shiftKey){e.preventDefault();send();}}

function send(){
  if(busy)return;
  var el=document.getElementById('inp');var txt=el.value.trim();if(!txt)return;
  addRow('u',txt,false);el.value='';el.style.height='auto';setBusy(true);
  post({action:'send',text:txt,snap:snapOn,coding_mode:codeOn,coding_model:codeModel});
}
function addRow(cls,txt,md){
  var row=document.createElement('div');row.className='row '+cls;
  var b=document.createElement('div');b.className='bbl '+cls;
  if(md){b.innerHTML=renderMd(txt);}else{b.textContent=txt;}
  row.appendChild(b);document.getElementById('msgs').appendChild(row);scroll();return b;
}
function addSys(txt){
  var row=document.createElement('div');row.className='row s';
  var b=document.createElement('div');b.className='bbl s';b.textContent=txt;
  row.appendChild(b);document.getElementById('msgs').appendChild(row);scroll();
}
function startThink(){
  var row=document.createElement('div');row.className='row a';row.id='_t';
  var b=document.createElement('div');b.className='bbl a';b.style.padding='0';
  b.innerHTML='<div class="typing"><div class="d"></div><div class="d"></div><div class="d"></div></div>';
  row.appendChild(b);document.getElementById('msgs').appendChild(row);scroll();
}
function stopThink(){var e=document.getElementById('_t');if(e)e.remove();}
var _fullBuf='';
function recvChunk(c){
  stopThink();_fullBuf+=c;
  if(!aiEl){aiEl=addRow('a','',false);}
  aiEl.innerHTML=renderMd(_fullBuf);scroll();
}
function recvEnd(f){
  stopThink();
  var text=f||_fullBuf;_fullBuf='';
  if(!text){addSys('⚠ Empty response');if(aiEl){aiEl.remove();}aiEl=null;setBusy(false);return;}
  if(aiEl){aiEl.innerHTML=renderMd(text);}else{addRow('a',text,true);}
  aiEl=null;setBusy(false);scroll();
}
function recvErr(m){stopThink();_fullBuf='';addSys('⚠ '+m);aiEl=null;setBusy(false);}
function setBusy(b){busy=b;document.getElementById('sendBtn').disabled=b;if(b)startThink();}
</script>
</body></html>"""


# ── WKWebView message handler ─────────────────────────────────────
class _MsgHandler(NSObject):
    _ctrl = None
    def userContentController_didReceiveScriptMessage_(self, ucc, msg):
        try:
            data = json.loads(str(msg.body()))
        except Exception:
            return
        if _MsgHandler._ctrl:
            _MsgHandler._ctrl.on_js(data)


# ── Native drag view for bubble ───────────────────────────────────
class _BubbleDragView(NSView):
    """
    Full-size NSView inside the bubble window that:
      • Draws the 👁 emoji centred
      • Handles native drag (moves the window)
      • Single-click (no drag) calls _on_click()
    """

    @objc.python_method
    def setup(self, on_click):
        self._on_click   = on_click
        self._drag_start = None
        self._did_drag   = False
        self.setWantsLayer_(True)
        return self

    def acceptsFirstMouse_(self, event): return True
    def mouseDownCanMoveWindow(self):    return False

    def mouseDown_(self, event):
        self._did_drag   = False
        loc              = event.locationInWindow()
        win_origin       = self.window().frame().origin
        self._drag_start = NSPoint(
            win_origin.x + loc.x,
            win_origin.y + loc.y,
        )
        self._win_origin_at_down = NSPoint(win_origin.x, win_origin.y)

    def mouseDragged_(self, event):
        if self._drag_start is None:
            return
        loc      = event.locationInWindow()
        win_orig = self.window().frame().origin
        cur      = NSPoint(win_orig.x + loc.x, win_orig.y + loc.y)
        dx       = cur.x - self._drag_start.x
        dy       = cur.y - self._drag_start.y
        if abs(dx) > 2 or abs(dy) > 2:
            self._did_drag = True
        new_origin = NSPoint(
            self._win_origin_at_down.x + dx,
            self._win_origin_at_down.y + dy,
        )
        self.window().setFrameOrigin_(new_origin)

    def mouseUp_(self, event):
        if not self._did_drag and self._on_click:
            self._on_click()
        self._drag_start = None
        self._did_drag   = False

    def drawRect_(self, rect):
        from AppKit import NSBezierPath, NSGraphicsContext
        NSColor.colorWithWhite_alpha_(0.12, 0.85).set()
        path = NSBezierPath.bezierPathWithRoundedRect_xRadius_yRadius_(
            self.bounds(), 30, 30)
        path.fill()
        import AppKit
        attrs = {
            AppKit.NSFontAttributeName:            NSFont.systemFontOfSize_(26),
            AppKit.NSForegroundColorAttributeName: NSColor.whiteColor(),
        }
        s    = "👁"
        size = self.bounds().size
        s_obj = objc.lookUpClass("NSString").stringWithString_(s)
        s_obj.drawAtPoint_withAttributes_(
            NSPoint(size.width / 2 - 13, size.height / 2 - 14), attrs)


# ── Floating bubble panel ─────────────────────────────────────────
class BubblePanel(NSObject):

    @objc.python_method
    def build(self, ctrl, x, y):
        self._ctrl = ctrl

        panel = NSPanel.alloc().initWithContentRect_styleMask_backing_defer_(
            NSMakeRect(x, y, _BUBBLE_W, _BUBBLE_H),
            _STYLE_BUBBLE, NSBackingStoreBuffered, False)
        panel.setLevel_(_LEVEL)
        panel.setCollectionBehavior_(_BEHAV_BUBBLE)
        panel.setBackgroundColor_(NSColor.clearColor())
        panel.setOpaque_(False)
        panel.setHasShadow_(True)
        panel.setIgnoresMouseEvents_(False)
        panel.setAcceptsMouseMovedEvents_(True)

        drag_view = _BubbleDragView.alloc().initWithFrame_(
            panel.contentView().bounds())
        drag_view.setup(ctrl.restoreFromBubble)
        drag_view.setAutoresizingMask_(_AUTORESZ)
        panel.contentView().addSubview_(drag_view)

        self._panel     = panel
        self._drag_view = drag_view

    @objc.python_method
    def show(self):
        self._panel.setLevel_(NSScreenSaverWindowLevel)  # higher than normal windows
        
        self._panel.setCollectionBehavior_(
        NSWindowCollectionBehaviorCanJoinAllSpaces |
        NSWindowCollectionBehaviorFullScreenAuxiliary |
        NSWindowCollectionBehaviorStationary
    )
        self._panel.orderFrontRegardless()
        self._panel.setHidesOnDeactivate_(False)

    @objc.python_method
    def hide(self):
        self._panel.orderOut_(None)

    @objc.python_method
    def move_to(self, x, y):
        self._panel.setFrameOrigin_(NSPoint(x, y))


# ── Main Controller ───────────────────────────────────────────────
class Controller(NSObject):

    def init(self):
        self = objc.super(Controller, self).init()
        if self is None: return None
        self._cfg          = load_cfg()
        self._history      = []
        self._js_q         = []
        self._js_lock      = threading.Lock()
        self._panel        = None
        self._wv           = None
        self._bubble       = None
        self._minimized    = False
        self._voice_cancel = False
        self._key_monitor  = None   # global NSEvent monitor for Z key
        return self

    @objc.python_method
    def setup(self):
        NSApp.setActivationPolicy_(NSApplicationActivationPolicyAccessory)
        self._build_statusbar()
        self._build_panel()

        fr = self._panel.frame()
        screen = NSScreen.mainScreen().visibleFrame()
        margin = 16

        x = screen.origin.x + screen.size.width  - _BUBBLE_W - margin
        y = screen.origin.y + screen.size.height - _BUBBLE_H - margin

        self._bubble = BubblePanel.alloc().init()
        self._bubble.build(self, x, y)

        # ── Global Z-key monitor ──────────────────────────────────
        # NSKeyDownMask = 1 << 10 = 1024
        # The handler fires for every key-down globally (even when another
        # app is in front).  We filter to keyCode 6 (Z) with no modifiers.
        ctrl_ref = self   # capture for the block
        

        def _key_handler(event, self=self):
            try:
                key = event.keyCode()

                # Z key → voice
                if key == 6:
                    if self._wv:
                        self._wv.evaluateJavaScript_completionHandler_(
                            "nativeVoiceToggle()", None
                        )

                # X key → screenshot toggle
                elif key == 7:
                    if self._wv:
                        self._wv.evaluateJavaScript_completionHandler_(
                            "toggleSnap()", None
                        )

            except Exception as e:
                print("Key error:", e)

        self._key_monitor = NSEvent.addGlobalMonitorForEventsMatchingMask_handler_(
            NSEventMaskKeyDown,
            _key_handler
        )
        # ─────────────────────────────────────────────────────────

        NSTimer.scheduledTimerWithTimeInterval_target_selector_userInfo_repeats_(
            0.04, self,
            objc.selector(self.flushJS_, signature=b"v@:@"),
            None, True)

        NSTimer.scheduledTimerWithTimeInterval_target_selector_userInfo_repeats_(
            1.2, self,
            objc.selector(self._doHideTerminal_, signature=b"v@:@"),
            None, False)

    def _doHideTerminal_(self, timer):
        hide_terminal()

    def _initBadge_(self, timer):
        p = self._cfg.get("provider", "groq")
        self._push(f"updateBadgeFromNative({json.dumps(p)})")

    @objc.python_method
    def _build_statusbar(self):
        sb   = NSStatusBar.systemStatusBar()
        item = sb.statusItemWithLength_(NSVariableStatusItemLength)
        item.button().setTitle_("👁")
        item.button().setTarget_(self)
        item.button().setAction_(
            objc.selector(self.statusClick_, signature=b"v@:@"))

        menu = NSMenu.alloc().init()
        menu.setAutoenablesItems_(False)

        def mi(title, sel=None, key=""):
            m = NSMenuItem.alloc().initWithTitle_action_keyEquivalent_(
                title,
                objc.selector(sel, signature=b"v@:@") if sel else None,
                key)
            m.setTarget_(self)
            menu.addItem_(m)
            return m

        mi("Show / Hide Panel", self.statusClick_)
        menu.addItem_(NSMenuItem.separatorItem())
        mi("Quit AI Eye", self.quitApp_, "q")
        item.setMenu_(menu)
        self._sitem = item

    @objc.python_method
    def _build_panel(self):
        fr     = NSScreen.mainScreen().frame()
        sw, sh = fr.size.width, fr.size.height
        w, h   = 360, min(680, int(sh) - 80)
        x      = int(sw) - w - 12
        y      = (int(sh) - h) // 2

        panel = NSPanel.alloc().initWithContentRect_styleMask_backing_defer_(
            NSMakeRect(x, y, w, h), _STYLE_PANEL, NSBackingStoreBuffered, False)
        panel.setTitle_("")
        panel.setTitlebarAppearsTransparent_(True)
        panel.setTitleVisibility_(1)
        panel.setMovableByWindowBackground_(True)
        panel.setLevel_(_LEVEL)
        panel.setCollectionBehavior_(_BEHAV_PANEL)
        panel.setBackgroundColor_(NSColor.clearColor())
        panel.setOpaque_(False)
        panel.setHasShadow_(True)
        panel.setMinSize_((280, 320))
        panel.setDelegate_(self)

        ve = NSVisualEffectView.alloc().initWithFrame_(panel.contentView().bounds())
        ve.setMaterial_(_VE_MAT)
        ve.setBlendingMode_(_VE_BLD)
        ve.setState_(_VE_STA)
        ve.setAutoresizingMask_(_AUTORESZ)
        panel.contentView().addSubview_(ve)

        wk_cfg = WKWebViewConfiguration.alloc().init()
        ucc    = WKUserContentController.alloc().init()
        wk_cfg.setUserContentController_(ucc)
        handler = _MsgHandler.alloc().init()
        _MsgHandler._ctrl = self
        ucc.addScriptMessageHandler_name_(handler, "ai")

        wv = WKWebView.alloc().initWithFrame_configuration_(ve.bounds(), wk_cfg)
        wv.setAutoresizingMask_(_AUTORESZ)
        try: wv.setValue_forKey_(False, "drawsBackground")
        except Exception: pass
        ve.addSubview_(wv)
        wv.loadHTMLString_baseURL_(_HTML, None)

        self._panel = panel
        self._wv    = wv
        panel.orderFront_(None)

        NSTimer.scheduledTimerWithTimeInterval_target_selector_userInfo_repeats_(
            0.8, self,
            objc.selector(self._initBadge_, signature=b"v@:@"),
            None, False)

    def windowShouldClose_(self, sender):
        self._panel.orderOut_(None)
        return False

    @objc.python_method
    def minimizeToBubble(self):
        self._minimized = True

        screen = NSScreen.mainScreen().visibleFrame()
        margin = 20

        x = screen.origin.x + screen.size.width  - _BUBBLE_W - margin - 20
        y = screen.origin.y + screen.size.height - _BUBBLE_H - margin

        self._panel.orderOut_(None)
        self._bubble.move_to(x, y)
        self._bubble.show()

    @objc.python_method
    def restoreFromBubble(self):
        self._minimized = False
        self._bubble.hide()
        self._panel.setCollectionBehavior_(1 | 4)
        self._panel.orderFrontRegardless()
        self._panel.makeKeyAndOrderFront_(None)
        NSApp.activateIgnoringOtherApps_(True)
        NSTimer.scheduledTimerWithTimeInterval_target_selector_userInfo_repeats_(
            0.1, self,
            objc.selector(self._restorePanelBehav_, signature=b"v@:@"),
            None, False)

    def _restorePanelBehav_(self, timer):
        self._panel.setCollectionBehavior_(_BEHAV_PANEL)

    def statusClick_(self, sender):
        if self._minimized:
            self.restoreFromBubble()
        elif self._panel.isVisible():
            self._panel.orderOut_(None)
        else:
            self._panel.orderFront_(None)
            self._panel.makeKeyAndOrderFront_(None)
            NSApp.activateIgnoringOtherApps_(True)

    def editConfig_(self, sender):
        if not os.path.exists(CFG_PATH): save_cfg(self._cfg)
        subprocess.Popen(["open", "-t", CFG_PATH])

    def quitApp_(self, sender):
        # Clean up the global key monitor before quitting
        if self._key_monitor:
            NSEvent.removeMonitor_(self._key_monitor)
            self._key_monitor = None
        kill_terminal()
        NSApp.terminate_(None)

    def flushJS_(self, timer):
        with self._js_lock:
            calls, self._js_q = self._js_q[:], []
        for js in calls:
            self._wv.evaluateJavaScript_completionHandler_(js, None)

    @objc.python_method
    def on_js(self, data: dict):
        action = data.get("action", "")
        if action == "minimize":
            self.minimizeToBubble()
        elif action == "clearHistory":
            self._history = []
        elif action == "setProvider":
            p = data.get("provider", "groq")
            self._cfg["provider"] = p
            save_cfg(self._cfg)
        elif action == "startVoice":
            snap         = data.get("snap", False)
            coding_mode  = data.get("coding_mode", False)
            coding_model = data.get("coding_model", None)
            self._voice_cancel = False
            threading.Thread(
                target=self._voice_worker,
                args=(snap, coding_mode, coding_model),
                daemon=True
            ).start()
        elif action == "cancelVoice":
            self._voice_cancel = True
        elif action == "send":
            text         = data.get("text", "").strip()
            snap         = data.get("snap", False)
            coding_mode  = data.get("coding_mode", False)
            coding_model = data.get("coding_model", None)
            if text:
                threading.Thread(
                    target=self._worker,
                    args=(text, snap, coding_mode, coding_model),
                    daemon=True
                ).start()

    # ── Voice recording + Whisper STT ───────────────────────────
    @objc.python_method
    def _voice_worker(self, snap: bool, coding_mode: bool, coding_model: str):
        self._push("voiceStatus('🎤 Listening…', false)")

        pcm, err = record_until_silence(
            on_status=lambda s: self._push(f"voiceStatus({json.dumps(s)}, false)")
        )

        if self._voice_cancel:
            self._push("voiceDone('')")
            return

        if err or not pcm:
            self._push(f"voiceErr({json.dumps(err or 'No speech detected')})")
            return

        self._push("voiceStatus('✨ Transcribing…', true)")
        wav_bytes             = pcm_to_wav_bytes(pcm)
        transcript, trans_err = transcribe_groq(wav_bytes, self._cfg)

        if self._voice_cancel:
            self._push("voiceDone('')")
            return

        if trans_err:
            self._push(f"voiceErr({json.dumps('Whisper error: ' + trans_err)})")
            return

        if not transcript:
            self._push("voiceErr('Whisper returned empty transcript')")
            return

        self._push(f"voiceDone({json.dumps(transcript)})")

        threading.Thread(
            target=self._worker,
            args=(transcript, snap, coding_mode, coding_model),
            daemon=True
        ).start()

    # ── AI worker ────────────────────────────────────────────────
    @objc.python_method
    def _worker(self, text: str, snap: bool,
                coding_mode: bool = False, coding_model: str = None):
        image_b64 = None
        if snap:
            image_b64 = capture_b64()
            if image_b64 is None:
                self._push("recvErr('Screen capture failed — grant Screen Recording in System Settings')")
                return

        active_cfg = dict(self._cfg)

        if coding_mode:
            if coding_model and coding_model.startswith("meta-llama/llama-3.3"):
                active_cfg["provider"]   = "groq"
                active_cfg["groq_model"] = active_cfg.get("groq_model", "meta-llama/llama-4-scout-17b-16e-instruct")
            else:
                active_cfg["provider"]         = "openrouter"
                if coding_model:
                    active_cfg["openrouter_model"] = coding_model
            sys_msg = (
                "You are an expert coding assistant. "
                "Write clean, well-commented code. "
                "Always use markdown fenced code blocks with the language tag."
            )
        elif image_b64:
            active_cfg["provider"] = "groq"
            if not active_cfg.get("groq_vision_model"):
                active_cfg["groq_vision_model"] = "meta-llama/llama-4-scout-17b-16e-instruct"
            sys_msg = "The user has shared a screenshot — analyse it carefully and answer their question."
        else:
            if active_cfg.get("provider") == "deepseek":
                active_cfg["provider"]         = "openrouter"
                active_cfg["openrouter_model"] = active_cfg.get("deepseek_model", "deepseek/deepseek-chat")
            sys_msg = (
                "You are a concise AI assistant in a floating overlay on macOS. "
                "Be helpful and brief."
            )

        msgs = [{"role": "system", "content": sys_msg}]
        msgs += self._history[-20:]
        msgs.append({"role": "user", "content": text})
        self._history.append({"role": "user", "content": text})

        def cb(chunk: str):
            self._push(f"recvChunk({json.dumps(chunk)})")

        try:
            result = ai_call(active_cfg, msgs, image_b64, cb)
            self._history.append({"role": "assistant", "content": result})
            self._push(f"recvEnd({json.dumps(result)})")
        except Exception as e:
            self._push(f"recvErr({json.dumps(str(e))})")

    @objc.python_method
    def _push(self, js: str):
        with self._js_lock:
            self._js_q.append(js)


# ── Entry point ───────────────────────────────────────────────────
def main():
    app  = NSApplication.sharedApplication()
    ctrl = Controller.alloc().init()
    app.setDelegate_(ctrl)
    ctrl.setup()
    app.run()

if __name__ == "__main__":
    main()