#!/usr/bin/env python3
"""
AI Eye v3 — macOS menu-bar AI overlay

Smart routing:
  📷 Screenshot  → Groq vision model   (images via Groq)
  💬 Text only   → Groq llama-3.3-70b-versatile  (fast, free)
  💻 Coding      → OpenRouter  (DeepSeek · Amazon Nova 2 · Mistral)
  🔵 DeepSeek    → DeepSeek API direct
  🦙 Local       → Ollama

Models used by AI Eye:
  - Groq text: llama-3.3-70b-versatile
  - Groq vision: llama-3.2-11b-vision-instruct
  - Gemini: gemini-2.0-flash-exp / gemini-1.5-flash
  - OpenRouter coding: deepseek/deepseek-chat, amazon/nova-lite-v1, mistralai/mistral-7b-instruct:free, meta-llama/llama-3.3-70b-instruct:free
  - DeepSeek direct: deepseek/deepseek-chat
  - Ollama local: llama3.2-vision

New in v3:
  • Clear chat button
  • Coding mode toggle → model picker bar (DeepSeek / Nova 2 / Mistral / llama-3.3)
  • Camera icon activates when screenshot mode is on
  • DeepSeek direct provider added
  • OpenRouter support for coding and DeepSeek routing
  • Groq vision for screenshots
  • Gemini fallback and streaming handling
  • Draggable minimized 👁 bubble (drag outer ring, click to restore)
  • Markdown + code-block rendering in AI responses

Configuration:
  - API keys and selectable models are stored in ~/.ai_eye.json
  - This file is intended to remain private and is excluded from git by .gitignore
"""

import sys, os, json, base64, io, threading, requests, subprocess
import objc
from Foundation import NSObject, NSMakeRect, NSTimer, NSPoint
from AppKit import (
    NSApplication, NSApp,
    NSMenu, NSMenuItem,
    NSStatusBar,
    NSPanel, NSBackingStoreBuffered,
    NSColor, NSScreen,
    NSVisualEffectView,
    NSApplicationActivationPolicyAccessory,
    NSVariableStatusItemLength,
    NSButton, NSView,
    NSFont,
)
try:
    from WebKit import WKWebView, WKWebViewConfiguration, WKUserContentController
except ImportError:
    print("❌  pip install pyobjc-framework-WebKit"); sys.exit(1)

# ── macOS window constants ────────────────────────────────────────
_STYLE_PANEL  = 1 | 2 | 8 | 32768 | 128   # Titled|Closable|Resizable|FullSizeContent|NonActivating
_STYLE_BUBBLE = 0                           # Borderless
_LEVEL        = 25                          # NSStatusWindowLevel
_BEHAV        = 1 | (1 << 8)               # CanJoinAllSpaces | FullScreenAuxiliary
_VE_MAT       = 7                          # NSVisualEffectMaterialSidebar
_VE_BLD       = 0                          # BehindWindow
_VE_STA       = 1                          # Active
_AUTORESZ     = 18                         # flexibleWidth | flexibleHeight
_BUBBLE_W     = 60
_BUBBLE_H     = 60

# ── Config ────────────────────────────────────────────────────────
# Default settings are loaded from ~/.ai_eye.json when present.
# This file stores API keys and should be kept private.
CFG_PATH = os.path.expanduser("~/.ai_eye.json")
DEFAULTS = {
    # General
    "provider":          "groq",            # groq | gemini | ollama | openrouter | deepseek

    # Groq (text + vision)
    "groq_key":          "",
    "groq_model":        "llama-3.3-70b-versatile",
    "groq_vision_model": "llama-3.2-11b-vision-instruct",

    # Gemini (multimodal / screenshots)
    "gemini_key":        "",
    "gemini_model":      "gemini-2.0-flash-exp",   # also: gemini-1.5-flash

    # Ollama (local)
    "ollama_host":       "http://localhost:11434",
    "ollama_model":      "llama3.2-vision",

    # OpenRouter (coding)
    "openrouter_key":    "",
    "openrouter_model":  "deepseek/deepseek-chat", # default coding model

    # DeepSeek direct
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
        return f"Ollama error: {e}\n\nStart Ollama: ollama serve\nPull model: ollama pull llama3.2-vision"


def _gemini(cfg, messages, image_b64, chunk_cb):
    key   = cfg["gemini_key"]
    model = cfg["gemini_model"]
    if not key:
        return "⚠️ Add your gemini_key to ~/.ai_eye.json (free at aistudio.google.com)"

    if chunk_cb:
        url = (f"https://generativelanguage.googleapis.com/v1beta"
               f"/models/{model}:streamGenerateContent?alt=sse&key={key}")
    else:
        url = (f"https://generativelanguage.googleapis.com/v1beta"
               f"/models/{model}:generateContent?key={key}")

    sys_instr = None
    contents  = []
    non_sys   = [m for m in messages if m["role"] != "system"]
    sys_msgs  = [m for m in messages if m["role"] == "system"]
    if sys_msgs:
        sys_instr = {"parts": [{"text": sys_msgs[-1]["content"]}]}
    for idx, m in enumerate(non_sys):
        parts = [{"text": m["content"]}]
        # attach image to the LAST user message by index (not identity)
        if image_b64 and idx == len(non_sys) - 1 and m["role"] == "user":
            parts.append({"inline_data": {"mime_type": "image/jpeg", "data": image_b64}})
        contents.append({"role": "user" if m["role"] == "user" else "model", "parts": parts})

    body = {"contents": contents}
    if sys_instr:
        body["system_instruction"] = sys_instr

    try:
        # Always use non-streaming for Gemini — streaming has edge-case blank output
        use_url = (f"https://generativelanguage.googleapis.com/v1beta"
                   f"/models/{model}:generateContent?key={key}")
        r    = requests.post(use_url, json=body, timeout=90)
        data = r.json()
        if "error" in data:
            msg = data["error"].get("message", str(data["error"]))
            return f"⚠️ Gemini error: {msg}"
        cands = data.get("candidates", [])
        if not cands:
            # surface finish reason if blocked
            reason = data.get("promptFeedback", {}).get("blockReason", "no candidates returned")
            return f"⚠️ Gemini returned no response ({reason})"
        text = cands[0].get("content", {}).get("parts", [{}])[0].get("text", "")
        if not text:
            return "⚠️ Gemini returned empty text — try rephrasing"
        if chunk_cb:
            chunk_cb(text)   # emit as single chunk so UI shows it
        return text
    except Exception as e:
        return f"⚠️ Gemini error: {e}"


def _groq(cfg, messages, chunk_cb=None, image_b64=None):
    key = cfg.get("groq_key", "")
    if not key:
        return "⚠️ Add your groq_key to ~/.ai_eye.json (free at console.groq.com)"

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

    def _extract_groq_content(choice):
        message = choice.get("message", {})
        content = message.get("content", choice.get("content", ""))
        if isinstance(content, str):
            return content
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
                        delta = choice.get("delta", {})
                        content = delta.get("content") or delta.get("message", {}).get("content", "")
                        if isinstance(content, str) and content:
                            full += content; chunk_cb(content)
                        elif isinstance(content, dict):
                            text = content.get("text", "") or content.get("content", "")
                            if text: full += text; chunk_cb(text)
                        elif isinstance(content, list):
                            text = "".join(
                                item.get("text", "") or item.get("content", "") if isinstance(item, dict) else str(item)
                                for item in content
                            )
                            if text: full += text; chunk_cb(text)
                    except Exception:
                        pass
            return full
        data = r.json()
        if "error" in data:
            return f"Groq error: {data['error'].get('message')}"
        choice = data.get("choices", [{}])[0]
        text = _extract_groq_content(choice)
        if not text:
            return "⚠️ Groq returned empty response — try again or switch provider"
        return text
    except Exception as e:
        return f"Groq error: {e}"


def _openrouter(cfg, messages, image_b64, chunk_cb):
    key = cfg.get("openrouter_key", "")
    if not key:
        return "⚠️ Add your openrouter_key to ~/.ai_eye.json (free at openrouter.ai)"

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
                # streaming returned nothing — try non-streaming as fallback
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
            err = data["error"]
            return f"⚠️ OpenRouter error: {err.get('message', str(err))}"
        txt = data.get("choices", [{}])[0].get("message", {}).get("content") or ""
        if not txt:
            return f"⚠️ OpenRouter returned empty — model may be unavailable: {cfg.get('openrouter_model')}"
        return txt
    except Exception as e:
        return f"⚠️ OpenRouter error: {e}"


def _deepseek(cfg, messages, image_b64, chunk_cb):
    key = cfg.get("deepseek_key", "")
    if not key:
        return "⚠️ Add your deepseek_key to ~/.ai_eye.json (platform.deepseek.com)"

    headers = {
        "Authorization": f"Bearer {key}",
        "Content-Type":  "application/json",
    }
    msgs = [{"role": m["role"], "content": m["content"]} for m in messages]
    payload = {
        "model":      cfg.get("deepseek_model", "deepseek/deepseek-chat"),
        "messages":   msgs,
        "max_tokens": 4096,
        "stream":     chunk_cb is not None,
    }

    def _extract_text(choice):
        msg = choice.get("message", {})
        content = choice.get("content", msg.get("content", choice.get("content", "")))
        if isinstance(content, str):
            return content
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

    def _parse_stream_line(line):
        stripped = line.strip()
        if stripped in ("data: [DONE]", "[DONE]"):
            return None
        if stripped.startswith("data: "):
            payload_line = stripped[6:]
        else:
            payload_line = stripped
        if not payload_line:
            return None
        try:
            d = json.loads(payload_line)
        except Exception:
            return None
        choice = d.get("choices", [{}])[0]
        delta = choice.get("delta", {})
        if isinstance(delta, dict):
            content = delta.get("content") or delta.get("message", {}).get("content")
            if isinstance(content, str):
                return content
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
        return _extract_text(choice)

    def _fallback_nonstream():
        try:
            r2 = requests.post("https://api.deepseek.com/v1/chat/completions",
                               headers=headers, json={**payload, "stream": False}, timeout=120)
            data2 = r2.json()
            if "error" in data2:
                err = data2["error"]
                return f"⚠️ DeepSeek error: {err.get('message', str(err))}"
            choice = data2.get("choices", [{}])[0]
            text = _extract_text(choice)
            if not text:
                return "⚠️ DeepSeek returned empty response"
            return text
        except Exception as e2:
            return f"⚠️ DeepSeek error: {e2}"

    try:
        r = requests.post("https://api.deepseek.com/v1/chat/completions",
                          headers=headers, json=payload,
                          stream=bool(chunk_cb), timeout=120)
        if chunk_cb:
            full = ""
            for raw in r.iter_lines():
                if not raw: continue
                line = raw.decode() if isinstance(raw, bytes) else raw
                text = _parse_stream_line(line)
                if text:
                    full += text
                    chunk_cb(text)
            if full:
                return full
            fallback = _fallback_nonstream()
            if fallback and not fallback.startswith("⚠️ DeepSeek error"):
                chunk_cb(fallback)
            return fallback

        data = r.json()
        if "error" in data:
            err = data["error"]
            return f"⚠️ DeepSeek error: {err.get('message', str(err))}"
        choice = data.get("choices", [{}])[0]
        text = _extract_text(choice)
        if not text:
            return "⚠️ DeepSeek returned empty response"
        return text
    except Exception as e:
        return f"⚠️ DeepSeek error: {e}"


def ai_call(cfg, messages, image_b64=None, chunk_cb=None):
    p = cfg.get("provider", "groq")
    if p == "ollama":     return _ollama(cfg, messages, image_b64, chunk_cb)
    if p == "gemini":     return _gemini(cfg, messages, image_b64, chunk_cb)
    if p == "groq":       return _groq(cfg, messages, chunk_cb, image_b64)
    if p == "openrouter": return _openrouter(cfg, messages, image_b64, chunk_cb)
    if p == "deepseek":
        # Route the general deepseek provider through OpenRouter.
        return _openrouter(cfg, messages, image_b64, chunk_cb)
    return "Unknown provider — check ~/.ai_eye.json"


# ── Embedded HTML/CSS/JS UI ───────────────────────────────────────
# ── Replace the entire _HTML = """...""" block in ai_eye.py with this ──

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
  --ubg:rgba(79,70,229,.82);
}

html,body{height:100%;background:transparent!important;overflow:hidden;
  -webkit-font-smoothing:antialiased}

body{display:flex;flex-direction:column;height:100vh;
  font-family:-apple-system,"SF Pro Text",system-ui,sans-serif;
  font-size:13px;line-height:1.5;color:var(--c0)}

/* ── Header ── */
.hdr{
  display:flex;align-items:center;justify-content:space-between;
  height:42px;padding:0 10px;
  border-bottom:1px solid var(--bd);
  -webkit-app-region:drag;flex-shrink:0;
}
.hl{display:flex;align-items:center;gap:7px}
.eye{width:24px;height:24px;opacity:.75;display:flex;align-items:center;justify-content:center}
.badge{
  font-size:11px;font-family:"SF Mono","Menlo",monospace;
  color:var(--c1);letter-spacing:.025em;
}
.hr2{display:flex;align-items:center;gap:1px;-webkit-app-region:no-drag}

.ic{
  width:32px;height:32px;border:none;background:none;cursor:pointer;
  border-radius:8px;color:var(--c2);
  display:flex;align-items:center;justify-content:center;
  transition:background .12s,color .12s;outline:none;
}
.ic svg{stroke-width:1.8}
.ic:hover{background:var(--c3);color:var(--c0)}
.ic.snap{color:var(--grn);background:var(--grn-b)}
.ic.snap:hover{background:rgba(52,211,153,.17)}
.ic.code{color:var(--prp);background:var(--prp-b)}
.ic.code:hover{background:rgba(124,58,237,.19)}

/* ── Model bar (always visible) ── */
.model-bar{
  display:flex;align-items:center;gap:3px;padding:5px 10px;
  border-bottom:1px solid var(--bd);background:rgba(0,0,0,.12);flex-shrink:0;
  flex-wrap:wrap;
}
.chip{
  padding:2px 9px;border-radius:5px;
  border:1px solid rgba(255,255,255,.09);
  background:none;color:var(--c2);cursor:pointer;
  font-size:10.5px;font-family:"SF Mono","Menlo",monospace;
  transition:all .12s;outline:none;letter-spacing:.02em;
}
.chip:hover{color:var(--c0);border-color:var(--bd2)}
.chip.on{background:rgba(99,102,241,.2);color:#a5b4fc;border-color:rgba(99,102,241,.45)}
.chip.code-on{background:rgba(167,139,250,.15);color:#c4b5fd;border-color:rgba(167,139,250,.4)}
.model-label{font-size:10px;color:var(--c2);opacity:.5;margin-right:2px;font-family:"SF Mono","Menlo",monospace;letter-spacing:.03em}

/* ── Snap banner ── */
.snap-banner{
  display:none;align-items:center;justify-content:space-between;
  padding:4px 12px;font-size:11px;
  color:var(--grn);background:var(--grn-b);
  border-bottom:1px solid rgba(52,211,153,.1);flex-shrink:0;
}
.snap-banner.open{display:flex}
.snap-banner button{background:none;border:none;color:var(--c2);cursor:pointer;font-size:12px;padding:0}
.snap-banner button:hover{color:var(--c0)}

/* ── Messages ── */
.msgs{
  flex:1;overflow-y:auto;padding:12px 10px;
  display:flex;flex-direction:column;gap:5px;
  scroll-behavior:smooth;
}
.msgs::-webkit-scrollbar{width:3px}
.msgs::-webkit-scrollbar-thumb{background:var(--bd2);border-radius:2px}

.row{display:flex}
.row.u{justify-content:flex-end}
.row.a{justify-content:flex-start}
.row.s{justify-content:center;margin:2px 0}

.bbl{
  max-width:84%;border-radius:14px;
  font-size:13px;line-height:1.55;word-break:break-word;padding:8px 11px;
}
.bbl.u{
  background:var(--ubg);color:#fff;
  border-radius:14px 14px 3px 14px;white-space:pre-wrap;
}
.bbl.a{
  background:var(--c3);border:1px solid var(--bd2);
  border-radius:14px 14px 14px 3px;
}
.bbl.s{font-size:11px;color:var(--c2);padding:2px 6px;background:none;border:none}

/* Markdown */
.bbl.a pre{
  background:rgba(0,0,0,.42);border:1px solid rgba(255,255,255,.06);
  border-radius:7px;padding:9px 11px;margin:6px 0;
  font-size:11px;font-family:"SF Mono","Menlo",monospace;
  overflow-x:auto;white-space:pre;
}
.bbl.a code{font-size:11px;font-family:"SF Mono","Menlo",monospace}
.bbl.a :not(pre)>code{background:rgba(255,255,255,.08);padding:1px 5px;border-radius:4px}
.bbl.a strong{color:rgba(255,255,255,.95)}
.bbl.a p{margin-bottom:5px}
.bbl.a p:last-child{margin-bottom:0}

/* Typing dots */
.typing{display:flex;gap:4px;padding:8px 11px}
.d{
  width:4px;height:4px;border-radius:50%;background:var(--c2);
  animation:pulse 1.3s ease-in-out infinite;
}
.d:nth-child(2){animation-delay:.15s}
.d:nth-child(3){animation-delay:.3s}
@keyframes pulse{0%,80%,100%{opacity:.25;transform:scale(.8)}40%{opacity:1;transform:scale(1)}}

/* ── Input ── */
.inp-wrap{padding:9px 10px;border-top:1px solid var(--bd);flex-shrink:0}
.inp-box{
  display:flex;align-items:flex-end;gap:5px;
  background:var(--c3);border:1px solid var(--bd2);
  border-radius:12px;padding:4px 4px 4px 11px;
  transition:border-color .15s;
}
.inp-box:focus-within{border-color:rgba(99,102,241,.4)}
textarea{
  flex:1;background:transparent;border:none;outline:none;
  color:var(--c0);font-size:13px;
  font-family:-apple-system,"SF Pro Text",system-ui,sans-serif;
  resize:none;line-height:1.5;padding:5px 0;
  max-height:96px;min-height:28px;
}
textarea::placeholder{color:var(--c2)}
.go{
  width:32px;height:32px;border:none;background:var(--acc);color:#fff;
  border-radius:9px;cursor:pointer;flex-shrink:0;
  display:flex;align-items:center;justify-content:center;margin-bottom:1px;
  transition:opacity .12s,transform .08s;
}
.go svg{stroke-width:2.4}
.go:hover{opacity:.85}
.go:active{transform:scale(.91)}
.go:disabled{opacity:.22;cursor:default}
</style>
</head>
<body>

<!-- Header -->
<div class="hdr">
  <div class="hl">
    <svg class="eye" viewBox="0 0 20 20" fill="none">
      <ellipse cx="10" cy="10" rx="8.5" ry="5" stroke="white" stroke-width="1.8"/>
      <circle cx="10" cy="10" r="2.8" fill="white" opacity=".95"/>
      <circle cx="11" cy="9" r="1.05" fill="rgba(0,0,0,.5)"/>
    </svg>
    <span class="badge" id="badge">groq · auto</span>
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
    <button class="ic" onclick="minimize()" title="Minimize"
      style="font-size:18px;font-weight:500;color:var(--c2);padding-bottom:1px">−</button>
  </div>
</div>

<!-- Model bar (hidden when coding mode is on) -->
<div class="model-bar" id="modelBar">
  <span class="model-label">provider:</span>
  <button class="chip" id="pv-groq"       onclick="setProvider('groq')">groq</button>
  <button class="chip" id="pv-gemini"     onclick="setProvider('gemini')">gemini</button>
  <button class="chip" id="pv-deepseek"   onclick="setProvider('deepseek')">deepseek</button>
</div>
<!-- Coding model bar (shown only when code toggle is on) -->
<div class="model-bar" id="codeBar" style="display:none;background:var(--prp-b);">
  <span class="model-label">code model:</span>
  <button class="chip code-on" id="p0" onclick="pickCode('deepseek/deepseek-chat',0)">deepseek</button>
  <button class="chip"         id="p1" onclick="pickCode('amazon/nova-lite-v1',1)">nova-2</button>
  <button class="chip"         id="p2" onclick="pickCode('mistralai/mistral-7b-instruct:free',2)">mistral</button>
  <button class="chip"         id="p3" onclick="pickCode('meta-llama/llama-3.3-70b-instruct:free',3)">llama-3.3</button>
</div>

<!-- Screenshot banner -->
<div class="snap-banner" id="snapBanner">
  <span>Screenshot attached</span>
  <button onclick="toggleSnap()">✕</button>
</div>

<!-- Messages -->
<div class="msgs" id="msgs">
  <div class="row s"><div class="bbl s">Ready</div></div>
</div>

<!-- Input -->
<div class="inp-wrap">
  <div class="inp-box">
    <textarea id="inp" rows="1" placeholder="Message…"
      onkeydown="onKey(event)" oninput="grow(this)"></textarea>
    <button class="go" id="sendBtn" onclick="send()">
      <svg width="11" height="11" viewBox="0 0 11 11" fill="none" stroke="white" stroke-width="1.8" stroke-linecap="round">
        <path d="M5.5 9.5V1.5M2 5L5.5 1.5 9 5"/>
      </svg>
    </button>
  </div>
</div>

<script>
var snapOn=false,codeOn=false,busy=false,aiEl=null;
var codeModel='deepseek/deepseek-chat';
var codePills=['p0','p1','p2','p3'];
var codeModelMap={
  'deepseek/deepseek-chat':'deepseek',
  'amazon/nova-lite-v1':'nova-2',
  'mistralai/mistral-7b-instruct:free':'mistral',
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

function updateORModel(name){
  var reverseMap={
    'deepseek':'deepseek/deepseek-chat',
    'nova-2':'amazon/nova-lite-v1',
    'mistral':'mistralai/mistral-7b-instruct:free',
    'llama-3.3':'meta-llama/llama-3.3-70b-instruct:free'
  };
  var full=reverseMap[name];
  if(full){
    codeModel=full;
    codePills.forEach(function(id){
      var el=document.getElementById(id);
      if(el) el.className='chip'+(el.textContent.trim()===name?' code-on':'');
    });
    updateBadge();
  }
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
  // swap bars: provider bar ↔ coding model bar
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
function clearChat(){
  document.getElementById('msgs').innerHTML='<div class="row s"><div class="bbl s">Cleared</div></div>';
  aiEl=null;post({action:'clearHistory'});
}
function minimize(){post({action:'minimize'});}
function updateBadge(){
  var b=document.getElementById('badge');
  if(codeOn){
    b.textContent='code · '+(codeModelMap[codeModel]||codeModel);
  }else if(snapOn){b.textContent='vision · groq';}
  else{b.textContent=currentProvider+' · ready';}
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
// Keep a running full-text buffer so recvEnd always has the complete string
var _fullBuf='';
function recvChunk(c){
  stopThink();
  _fullBuf+=c;
  if(!aiEl){aiEl=addRow('a','',false);}
  // render markdown incrementally so code blocks appear as they stream
  aiEl.innerHTML=renderMd(_fullBuf);
  scroll();
}
function recvEnd(f){
  stopThink();
  var text=f||_fullBuf;  // use server final string; fall back to accumulated buffer
  _fullBuf='';
  if(!text){
    // completely empty — show a warning instead of blank bubble
    addSys('⚠ Model returned empty response — try again or switch provider');
    if(aiEl){aiEl.remove();} aiEl=null; setBusy(false); return;
  }
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


# ── Bubble message handler ────────────────────────────────────────
class _BubbleMsgHandler(NSObject):
    _ctrl = None
    def userContentController_didReceiveScriptMessage_(self, ucc, msg):
        if _BubbleMsgHandler._ctrl:
            _BubbleMsgHandler._ctrl.restoreFromBubble()


# ── Floating bubble HTML (draggable via -webkit-app-region) ───────
_BUBBLE_HTML = """<!DOCTYPE html><html><head>
<style>
*{margin:0;padding:0;box-sizing:border-box}
html,body{width:100%;height:100%;background:transparent!important;overflow:hidden}
.outer{
  width:100%;height:100%;
  display:flex;align-items:center;justify-content:center;
  -webkit-app-region:drag;
  cursor:grab;
}
.icon{
  font-size:28px;
  -webkit-app-region:no-drag;
  cursor:pointer;
  user-select:none;
  transition:transform .15s;
}
.icon:hover{transform:scale(1.15)}
</style>
</head><body>
<div class="outer">
  <div class="icon" onclick="restore()">👁</div>
</div>
<script>
function restore(){
  window.webkit.messageHandlers.bubble.postMessage('restore');
}
</script>
</body></html>"""


# ── Floating bubble panel ─────────────────────────────────────────
class BubblePanel(NSObject):
    @objc.python_method
    def build(self, ctrl, x, y):
        self._ctrl = ctrl

        panel = NSPanel.alloc().initWithContentRect_styleMask_backing_defer_(
            NSMakeRect(x, y, _BUBBLE_W, _BUBBLE_H),
            _STYLE_BUBBLE, NSBackingStoreBuffered, False)
        panel.setLevel_(_LEVEL)
        panel.setCollectionBehavior_(_BEHAV)
        panel.setBackgroundColor_(NSColor.clearColor())
        panel.setOpaque_(False)
        panel.setHasShadow_(True)
        panel.setMovableByWindowBackground_(True)   # drag the outer ring

        ve = NSVisualEffectView.alloc().initWithFrame_(panel.contentView().bounds())
        ve.setMaterial_(7)
        ve.setBlendingMode_(0)
        ve.setState_(1)
        ve.setAutoresizingMask_(_AUTORESZ)
        ve.setWantsLayer_(True)
        ve.layer().setCornerRadius_(30)
        ve.layer().setMasksToBounds_(True)
        panel.contentView().addSubview_(ve)

        # WKWebView for the draggable+clickable eye emoji
        wk_cfg = WKWebViewConfiguration.alloc().init()
        ucc    = WKUserContentController.alloc().init()
        wk_cfg.setUserContentController_(ucc)
        handler = _BubbleMsgHandler.alloc().init()
        _BubbleMsgHandler._ctrl = ctrl
        ucc.addScriptMessageHandler_name_(handler, "bubble")

        wv = WKWebView.alloc().initWithFrame_configuration_(ve.bounds(), wk_cfg)
        wv.setAutoresizingMask_(_AUTORESZ)
        try: wv.setValue_forKey_(False, "drawsBackground")
        except Exception: pass
        ve.addSubview_(wv)
        wv.loadHTMLString_baseURL_(_BUBBLE_HTML, None)

        self._panel = panel

    @objc.python_method
    def show(self):   self._panel.orderFront_(None)

    @objc.python_method
    def hide(self):   self._panel.orderOut_(None)

    @objc.python_method
    def move_to(self, x, y):
        self._panel.setFrameOrigin_(NSPoint(x, y))


# ── Main Controller ───────────────────────────────────────────────
class Controller(NSObject):

    def init(self):
        self = objc.super(Controller, self).init()
        if self is None: return None
        self._cfg       = load_cfg()
        self._history   = []
        self._js_q      = []
        self._js_lock   = threading.Lock()
        self._panel     = None
        self._wv        = None
        self._bubble    = None
        self._minimized = False
        return self

    @objc.python_method
    def setup(self):
        NSApp.setActivationPolicy_(NSApplicationActivationPolicyAccessory)
        self._build_statusbar()
        self._build_panel()

        # Build bubble (hidden until minimized)
        fr = self._panel.frame()
        bx = fr.origin.x + fr.size.width  / 2 - _BUBBLE_W / 2
        by = fr.origin.y + fr.size.height / 2 - _BUBBLE_H / 2
        self._bubble = BubblePanel.alloc().init()
        self._bubble.build(self, bx, by)

        # Flush JS queue every 40 ms
        NSTimer.scheduledTimerWithTimeInterval_target_selector_userInfo_repeats_(
            0.04, self,
            objc.selector(self.flushJS_, signature=b"v@:@"),
            None, True)

        # Hide terminal window 1.2 s after launch
        NSTimer.scheduledTimerWithTimeInterval_target_selector_userInfo_repeats_(
            1.2, self,
            objc.selector(self._doHideTerminal_, signature=b"v@:@"),
            None, False)

    def _doHideTerminal_(self, timer):
        hide_terminal()

    def _initBadge_(self, timer):
        p = self._cfg.get("provider", "groq")
        self._push(f"updateBadgeFromNative({json.dumps(p)})")

    # ── Status bar ───────────────────────────────────────────────
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

    # ── Main panel ───────────────────────────────────────────────
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
        panel.setCollectionBehavior_(_BEHAV)
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

        # Sync badge to saved provider after a short delay (WKWebView needs to load first)
        def _init_badge(timer):
            p = self._cfg.get("provider", "groq")
            self._push(f"updateBadgeFromNative({json.dumps(p)})")
        NSTimer.scheduledTimerWithTimeInterval_target_selector_userInfo_repeats_(
            0.8, self,
            objc.selector(self._initBadge_, signature=b"v@:@"),
            None, False)

    # ── Window delegate ──────────────────────────────────────────
    def windowShouldClose_(self, sender):
        self._panel.orderOut_(None)
        return False

    # ── Minimize / restore ───────────────────────────────────────
    @objc.python_method
    def minimizeToBubble(self):
        self._minimized = True
        fr = self._panel.frame()
        bx = fr.origin.x + fr.size.width  / 2 - _BUBBLE_W / 2
        by = fr.origin.y + fr.size.height / 2 - _BUBBLE_H / 2
        self._panel.orderOut_(None)
        self._bubble.move_to(bx, by)
        self._bubble.show()

    @objc.python_method
    def restoreFromBubble(self):
        self._minimized = False
        self._bubble.hide()
        self._panel.orderFront_(None)
        self._panel.makeKeyAndOrderFront_(None)

    # ── Status bar click ─────────────────────────────────────────
    def statusClick_(self, sender):
        if self._minimized:
            self.restoreFromBubble()
        elif self._panel.isVisible():
            self._panel.orderOut_(None)
        else:
            self._panel.orderFront_(None)

    # ── Provider menu items ──────────────────────────────────────
    def setGroq_(self, sender):
        self._cfg["provider"] = "groq"; save_cfg(self._cfg)
        self._push("updateBadgeFromNative('groq')")

    def setGemini_(self, sender):
        self._cfg["provider"] = "gemini"; save_cfg(self._cfg)
        self._push("updateBadgeFromNative('gemini')")

    def setDeepSeek_(self, sender):
        self._cfg["provider"] = "deepseek"; save_cfg(self._cfg)
        self._push("updateBadgeFromNative('deepseek')")

    def setOpenRouter_(self, sender):
        self._cfg["provider"] = "openrouter"; save_cfg(self._cfg)
        self._push("updateBadgeFromNative('openrouter')")

    def setOllama_(self, sender):
        self._cfg["provider"] = "ollama"; save_cfg(self._cfg)
        self._push("updateBadgeFromNative('ollama')")

    def setGeminiFlash2_(self, sender):
        self._cfg["gemini_model"] = "gemini-2.0-flash-exp"; save_cfg(self._cfg)

    def setGeminiFlash15_(self, sender):
        self._cfg["gemini_model"] = "gemini-1.5-flash"; save_cfg(self._cfg)

    def setORDeepSeek_(self, sender):
        self._cfg["openrouter_model"] = "deepseek/deepseek-chat"; save_cfg(self._cfg)
        self._push("updateORModel('deepseek')")

    def setORNova_(self, sender):
        self._cfg["openrouter_model"] = "amazon/nova-lite-v1"; save_cfg(self._cfg)
        self._push("updateORModel('nova-2')")

    def setORMistral_(self, sender):
        self._cfg["openrouter_model"] = "mistralai/mistral-7b-instruct:free"; save_cfg(self._cfg)
        self._push("updateORModel('mistral')")

    def setORLlama_(self, sender):
        self._cfg["openrouter_model"] = "meta-llama/llama-3.3-70b-instruct:free"; save_cfg(self._cfg)
        self._push("updateORModel('llama-3.3')")

    def editConfig_(self, sender):
        if not os.path.exists(CFG_PATH): save_cfg(self._cfg)
        subprocess.Popen(["open", "-t", CFG_PATH])

    def quitApp_(self, sender):
        kill_terminal()
        NSApp.terminate_(None)

    # ── JS flush timer ───────────────────────────────────────────
    def flushJS_(self, timer):
        with self._js_lock:
            calls, self._js_q = self._js_q[:], []
        for js in calls:
            self._wv.evaluateJavaScript_completionHandler_(js, None)

    # ── JS bridge ────────────────────────────────────────────────
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

    @objc.python_method
    def _worker(self, text: str, snap: bool,
                coding_mode: bool = False, coding_model: str = None):
        # ── Capture screenshot ──────────────────────────────────
        image_b64 = None
        if snap:
            image_b64 = capture_b64()
            if image_b64 is None:
                self._push("recvErr('Screen capture failed — grant Screen Recording "
                           "in System Settings → Privacy & Security')")
                return

        # ── Smart provider routing ──────────────────────────────
        active_cfg = dict(self._cfg)

        if coding_mode:
            # Coding mode: use Groq for llama-3.3, otherwise OpenRouter
            if coding_model and coding_model.startswith("meta-llama/llama-3.3"):
                active_cfg["provider"] = "groq"
                active_cfg["groq_model"] = active_cfg.get(
                    "groq_model", "llama-3.3-70b-versatile")
            else:
                active_cfg["provider"] = "openrouter"
                if coding_model:
                    active_cfg["openrouter_model"] = coding_model
            sys_msg = (
                "You are an expert coding assistant. "
                "Write clean, well-commented code. "
                "Always use markdown fenced code blocks with the language tag. "
            )
        elif image_b64:
            # Screenshot: ALWAYS Groq vision model — use Groq for images instead of Gemini
            active_cfg["provider"] = "groq"
            if not active_cfg.get("groq_vision_model"):
                active_cfg["groq_vision_model"] = "meta-llama/llama-4-scout-17b-16e-instruct"
            sys_msg = (
                "The user has shared a screenshot — analyse it carefully and answer their question."
            )
        else:
            # Plain text: respect whatever provider the user picked — but route
            # general "deepseek" through OpenRouter so both modes use the same path.
            if active_cfg.get("provider") == "deepseek":
                active_cfg["provider"] = "openrouter"
                active_cfg["openrouter_model"] = active_cfg.get(
                    "deepseek_model", "deepseek/deepseek-chat")
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