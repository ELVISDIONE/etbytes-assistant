#!/usr/bin/env python3
"""
E.TBYTES Assistant – Full Web Dashboard
All CLI features now in your browser.
"""
import os, sys, json, base64, io, re, time, random, shutil, threading, subprocess, secrets
from datetime import datetime
from flask import Flask, render_template_string, request, jsonify, session, send_file
from werkzeug.utils import secure_filename
from rich.console import Console as RichConsole
import requests as req

# ── Import core functions from your existing assistant ──
try:
    from etbytes_assistant import (
        groq_chat,
        config as assistant_config,
        load_config,
        save_config,
        QA_FILE,
        SKLEARN_AVAILABLE,
        OfflineAI,
        SimpleOfflineAI,
        log_activity,
        _render_ascii_art,
        ChatServer,
        get_lan_ip,
        ai_generate_quiz,
        # additional pure functions (we’ll use them directly)
    )
except ImportError:
    print("Error: etbytes_assistant.py not found. Place this script in the same folder.")
    sys.exit(1)

# ── Flask setup ──
app = Flask(__name__)
SECRET_KEY_FILE = os.path.expanduser("~/.etbytes_web_secret.key")
def _get_secret_key():
    """Persist the session secret so restarting the app doesn't log everyone out."""
    if os.path.exists(SECRET_KEY_FILE):
        with open(SECRET_KEY_FILE) as f:
            key = f.read().strip()
        if key:
            return key
    key = secrets.token_hex(32)
    with open(SECRET_KEY_FILE, "w") as f:
        f.write(key)
    os.chmod(SECRET_KEY_FILE, 0o600)
    return key
app.secret_key = _get_secret_key()

# ── Password management ──
PASSWORD_FILE = os.path.expanduser("~/.etbytes_web_password.json")
def _bootstrap_password():
    pwd = secrets.token_urlsafe(9)
    set_password(pwd)
    print(f"\n[E.TBYTES] No password was set. Generated one for you: {pwd}")
    print(f"[E.TBYTES] Saved to {PASSWORD_FILE} — change it from Settings after logging in.\n")
    return pwd
def get_password():
    if os.path.exists(PASSWORD_FILE):
        with open(PASSWORD_FILE) as f:
            pwd = json.load(f).get("password")
        return pwd or _bootstrap_password()
    return _bootstrap_password()
def set_password(new_pwd):
    with open(PASSWORD_FILE, "w") as f:
        json.dump({"password": new_pwd}, f)

# ── Initialize offline AI (once) ──
offline_ai = None
def init_offline():
    global offline_ai
    if SKLEARN_AVAILABLE:
        offline_ai = OfflineAI(QA_FILE)
    else:
        offline_ai = SimpleOfflineAI(QA_FILE)

# ── Utility: run function that expects console output, capture result ──
def capture_groq_stream(messages):
    """Collect stream tokens into a single string."""
    full = ""
    for token in groq_chat(messages, stream=True):
        full += token
    return full

def capture_groq(messages):
    """Non-streamed answer."""
    resp = list(groq_chat(messages, stream=False))
    return resp[0] if resp else ""

# ── AI-driven game opponents ──
_TTT_LINES = [(0,1,2),(3,4,5),(6,7,8),(0,3,6),(1,4,7),(2,5,8),(0,4,8),(2,4,6)]
def _ttt_fallback_move(board):
    """Simple heuristic used only if the AI call fails or returns junk."""
    for player in ('O', 'X'):
        for a, b, c in _TTT_LINES:
            cells = [a, b, c]
            vals = [board[i] for i in cells]
            empty = [i for i in cells if not board[i]]
            if len(empty) == 1 and vals.count(player) == 2:
                return empty[0]
    if not board[4]:
        return 4
    corners = [i for i in (0, 2, 6, 8) if not board[i]]
    if corners:
        return random.choice(corners)
    empty = [i for i, v in enumerate(board) if not v]
    return random.choice(empty) if empty else -1

def ai_ttt_move(board):
    """Ask the AI which cell O should take next; falls back to a heuristic on failure."""
    empty = [i for i, v in enumerate(board) if not v]
    if not empty:
        return -1
    board_str = ','.join(v if v else '_' for v in board)
    prompt = (
        "You are the O player in a Tic-Tac-Toe game against a human playing X. "
        "Cells are indexed 0-8 in this layout:\n0 1 2\n3 4 5\n6 7 8\n"
        f"Current board (comma-separated, '_' = empty): {board_str}\n"
        f"Empty cells: {empty}\n"
        "Pick the empty cell that gives O the best chance to win or block X. "
        "Respond with ONLY the index number, nothing else."
    )
    try:
        reply = capture_groq([{"role": "user", "content": prompt}])
        match = re.search(r'-?\d+', reply)
        if match:
            idx = int(match.group())
            if idx in empty:
                return idx
    except Exception:
        pass
    return _ttt_fallback_move(board)

_rps_history = {}  # sid -> recent list of the human's choices
def ai_rps_move(history):
    """Ask the AI to pick rock/paper/scissors, optionally reading the human's recent pattern."""
    hist_str = ', '.join(history[-10:]) if history else 'none yet'
    prompt = (
        "You are playing Rock Paper Scissors against a human. "
        f"The human's recent choices, oldest first: {hist_str}. "
        "Pick your next move: rock, paper, or scissors. You may use the history "
        "to anticipate a pattern, but don't be too predictable yourself. "
        "Respond with ONLY one word: rock, paper, or scissors."
    )
    try:
        reply = capture_groq([{"role": "user", "content": prompt}]).strip().lower()
        for choice in ('rock', 'paper', 'scissors'):
            if choice in reply:
                return choice
    except Exception:
        pass
    return random.choice(['rock', 'paper', 'scissors'])

# ── Server-side conversation memory ──
# Flask's session cookie is client-side and size-limited, so multi-turn
# conversations (chat, interactive fiction) are kept here instead, keyed by
# a random id stashed in the session cookie. This mirrors the terminal
# app's behavior of keeping a running `conversation` list in memory.
_conversations = {}

def _get_sid():
    if 'sid' not in session:
        session['sid'] = secrets.token_hex(16)
    return session['sid']

# ── ASCII art rendering (shared by camera capture and file upload) ──
def _ascii_render_html(rgb_img_full, width, mode, dither):
    """
    Render an image to ASCII using the exact same core algorithm as the
    terminal app (_render_ascii_art, imported from etbytes_assistant.py),
    then convert Rich's styled Text output to standalone HTML via Rich's
    own HTML exporter -- so the web version reproduces per-character
    coloring identically to the terminal version instead of reimplementing
    color-to-markup logic separately.
    """
    ascii_str, art = _render_ascii_art(rgb_img_full, width, mode=mode, dither=dither)
    rc = RichConsole(record=True, file=io.StringIO(), width=width + 4)
    rc.print(art)
    html = rc.export_html(
        inline_styles=True,
        code_format=(
            '<pre style="font-family:\'Fira Code\',\'Cascadia Code\',monospace; '
            'line-height:1.15; background:#000; color:#eee; padding:1rem; '
            'border-radius:0.6rem; overflow-x:auto; font-size:0.55rem;">{code}</pre>'
        ),
    )
    return ascii_str, html

# ── Socket Chat: real multi-client TCP chat, shared with the terminal app ──
# ChatServer and get_lan_ip live in etbytes_assistant.py so the terminal's
# Socket Chat game and this dashboard's Socket Chat page run the exact same
# real implementation instead of two parallel copies.
_chat_server = ChatServer(port=12345)

# ── HTML Template ──
HTML = r'''<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0, user-scalable=yes">
<title>E.TBYTES Assistant</title>
<style>
:root {
  --bg: #0b0b0f;
  --surface: #1a1a24;
  --primary: #00e5ff;
  --accent: #ff4081;
  --success: #5cffa0;
  --text: #e0e0e0;
  --border: #2a2a35;
  --shadow: 0 10px 30px rgba(0,0,0,0.8);
  --transition: 0.3s cubic-bezier(0.4, 0, 0.2, 1);
  --sidebar-w: 260px;
  --topbar-h: 64px;
}
* { margin:0; padding:0; box-sizing:border-box; }
body {
  font-family: 'Segoe UI', system-ui, -apple-system, sans-serif;
  background: var(--bg); color: var(--text); overflow-x: hidden;
  min-height:100vh;
}
#launch-overlay {
  position:fixed; inset:0; background: #040408;
  display:flex; flex-direction:column; align-items:center; justify-content:center;
  z-index:1000; transition: opacity 0.8s, transform 0.8s;
}
.launch-hidden { opacity:0; pointer-events:none; transform:scale(1.1); }
.launch-logo {
  font-size:3.5rem; font-weight:800; letter-spacing:2px;
  background: linear-gradient(135deg, var(--primary), var(--accent));
  -webkit-background-clip: text; -webkit-text-fill-color: transparent;
  margin-bottom:2rem; animation: float 3s ease-in-out infinite;
}
@keyframes float {
  0%,100%{ transform:translateY(0); } 50%{ transform:translateY(-10px); }
}
.launch-form {
  background: var(--surface); padding:2rem; border-radius:1rem;
  box-shadow: var(--shadow); width:320px; display:flex; flex-direction:column; gap:1rem;
}
.launch-form input {
  padding:0.8rem 1rem; background:#2a2a35; border:1px solid var(--border);
  border-radius:0.5rem; color:white; font-size:1rem; outline:none;
}
.launch-form input:focus { border-color: var(--primary); }
.launch-form button {
  padding:0.8rem; background: linear-gradient(135deg, var(--primary), var(--accent));
  border:none; border-radius:0.5rem; color:white; font-weight:bold; cursor:pointer;
  text-transform:uppercase; letter-spacing:1px; transition: var(--transition);
}
.launch-form button:hover { filter:brightness(1.2); transform:translateY(-2px); }
.launch-error { color: var(--accent); font-size:0.9rem; }

/* ================= App shell layout ================= */
.topbar {
  position:fixed; top:0; left:0; right:0; height:var(--topbar-h);
  background: var(--surface); border-bottom:1px solid var(--border);
  display:flex; align-items:center; justify-content:space-between;
  padding:0 1.2rem; z-index:200;
}
.topbar-brand {
  font-weight:800; font-size:1.15rem; letter-spacing:1px;
  background: linear-gradient(135deg, var(--primary), var(--accent));
  -webkit-background-clip: text; -webkit-text-fill-color: transparent;
}
.topbar-right { display:flex; align-items:center; gap:0.6rem; }
.badge {
  padding:0.3rem 0.75rem; border-radius:2rem; font-size:0.72rem; font-weight:700;
  border:1px solid var(--border); background:#12121a; white-space:nowrap;
}
.badge-cyan { color: var(--primary); border-color: rgba(0,229,255,0.35); }
.badge-green { color: var(--success); border-color: rgba(92,255,160,0.35); }

.hamburger {
  position:relative; background: transparent; border:none; width:40px; height:40px;
  border-radius:50%; display:none; flex-direction:column; align-items:center;
  justify-content:center; gap:5px; cursor:pointer;
}
.hamburger span { display:block; width:20px; height:2px; background:var(--text); border-radius:2px; transition: var(--transition); }
.hamburger.active span:nth-child(1) { transform: rotate(45deg) translate(5px,5px); }
.hamburger.active span:nth-child(2) { opacity:0; }
.hamburger.active span:nth-child(3) { transform: rotate(-45deg) translate(5px,-5px); }

.drawer-backdrop {
  position:fixed; inset:0; background: rgba(0,0,0,0.55); z-index:140;
  opacity:0; pointer-events:none; transition: opacity 0.3s ease;
}
.drawer-backdrop.show { opacity:1; pointer-events:auto; }

.drawer {
  position:fixed; top:var(--topbar-h); left:0; bottom:0; width:var(--sidebar-w);
  background: var(--surface); border-right:1px solid var(--border);
  padding:1rem 0.6rem 2rem; overflow-y:auto;
  transform: translateX(-100%); transition: transform 0.35s ease;
  z-index:150; display:flex; flex-direction:column; gap:0.15rem;
}
.drawer.open { transform: translateX(0); box-shadow: var(--shadow); }
.nav-group-label {
  font-size:0.68rem; text-transform:uppercase; letter-spacing:1px;
  color:#6b6b78; margin:0.9rem 0.7rem 0.3rem;
}
.nav-group-label:first-child { margin-top:0.3rem; }
.drawer a {
  display:flex; align-items:center; gap:0.6rem; padding:0.65rem 0.8rem;
  border-radius:0.6rem; color:var(--text); text-decoration:none; font-size:0.92rem;
  border-left:3px solid transparent; transition: all 0.3s ease;
}
.drawer a:hover { background: rgba(0,229,255,0.08); transform: translateX(3px); }
.drawer a.active {
  background: rgba(0,229,255,0.12); color: var(--primary);
  border-left-color: var(--primary); text-shadow: 0 0 10px rgba(0,229,255,0.4);
}

.main-content {
  margin-top: var(--topbar-h);
  padding: 1.6rem 1.8rem 3rem;
  min-height: calc(100vh - var(--topbar-h));
}

@media (min-width: 900px) {
  .drawer { transform: translateX(0) !important; }
  .drawer-backdrop { display:none !important; }
  .hamburger { display:none !important; }
  .main-content { margin-left: var(--sidebar-w); }
  .chat-fullscreen .chat-input-area { left: var(--sidebar-w) !important; width: calc(100% - var(--sidebar-w)) !important; }
}
@media (max-width: 899px) {
  .hamburger { display:flex; }
}

.section { display:none; }
.section.active { display:block; animation: fadeInUp 0.35s ease; }
.section.chat-fullscreen.active {
  display:flex; flex-direction:column;
  height: calc(100vh - var(--topbar-h));
  margin: -1.6rem -1.8rem -3rem -1.8rem;
}

.page-title { font-size:1.6rem; font-weight:800; margin-bottom:0.2rem; text-shadow: 0 0 10px rgba(0,229,255,0.4); }
.page-subtitle { color:#9a9aa5; margin-bottom:1.3rem; font-size:0.92rem; }

.stat-strip { display:flex; flex-wrap:wrap; gap:0.7rem; margin-bottom:1.8rem; }
.stat-pill {
  background: var(--surface); border:1px solid var(--border); border-radius:0.8rem;
  padding:0.7rem 1.1rem; display:flex; flex-direction:column; gap:0.15rem; min-width:140px;
}
.stat-pill .stat-value { font-size:1.3rem; font-weight:800; color: var(--primary); }
.stat-pill .stat-label { font-size:0.68rem; color:#9a9aa5; text-transform:uppercase; letter-spacing:0.5px; }

.category-title {
  font-size:0.8rem; text-transform:uppercase; letter-spacing:1.2px;
  color:#8a8a96; margin:1.6rem 0 0.7rem;
}
.category-title:first-child { margin-top:0; }

.dashboard-grid {
  display:grid; grid-template-columns: repeat(auto-fill, minmax(210px, 1fr));
  gap:1rem; margin-bottom:0.4rem;
}

.card-icon { font-size:1.7rem; margin-bottom:0.4rem; }
.card h3 { font-size:1.02rem; margin-bottom:0.3rem; }
.card p { color:#9a9aa5; font-size:0.85rem; line-height:1.4; }

/* ---- Game panel ---- */
.game-panel { margin-top:1.4rem; background: var(--surface); border:1px solid var(--border); border-radius:1rem; padding:1.4rem; position:relative; }
.game-panel h3 { color: var(--primary); margin-bottom:0.8rem; }
.game-close {
  position:absolute; top:1rem; right:1rem; background:transparent; border:1px solid var(--border);
  width:32px; height:32px; border-radius:50%; padding:0; color:var(--text); animation:none;
}
.ttt-grid { display:grid; grid-template-columns:repeat(3,84px); gap:8px; margin:1rem 0; }
.ttt-cell {
  width:84px; height:84px; background:#0b0b0f; border:1px solid var(--border); border-radius:0.6rem;
  font-size:2.2rem; display:flex; align-items:center; justify-content:center; cursor:pointer;
  transition:0.2s; animation:none;
}
.ttt-cell:hover { border-color: var(--primary); box-shadow: none; transform:none; }
.hangman-box { display:flex; gap:2rem; flex-wrap:wrap; align-items:flex-start; }
.hangman-fig {
  font-family:'Fira Code','Cascadia Code',monospace; white-space:pre; font-size:1rem; line-height:1.2;
  background:#0a0a10; padding:1rem; border-radius:0.6rem; border:1px solid var(--border);
}
.hangman-word { font-size:1.6rem; letter-spacing:0.4rem; margin:1rem 0; font-family:monospace; }
.keyboard { display:flex; flex-wrap:wrap; gap:5px; max-width:420px; }
.key-btn { width:34px; height:34px; border-radius:0.4rem; font-size:0.85rem; padding:0; animation:none; }
.key-btn:disabled { opacity:0.3; cursor:not-allowed; animation:none; }
.quiz-option { display:block; width:100%; text-align:left; margin:0.4rem 0; background:#1a1a24; border:1px solid var(--border); animation:none; }
.quiz-option.correct { border-color: var(--success); box-shadow: 0 0 10px rgba(92,255,160,0.4); }
.quiz-option.wrong { border-color: var(--accent); }

.calc-box { max-width:320px; }
.calc-display {
  background:#0b0b0f; border:1px solid var(--border); border-radius:0.6rem;
  padding:0.9rem 1rem; text-align:right; font-size:1.7rem; font-family:'Fira Code','Cascadia Code',monospace;
  margin-bottom:0.8rem; min-height:1.7rem; overflow-x:auto; white-space:nowrap; color:var(--text);
}
.calc-grid { display:grid; grid-template-columns:repeat(4,1fr); gap:8px; }
.calc-btn {
  padding:0.95rem 0; font-size:1.15rem; border-radius:0.6rem; background:#1a1a24;
  border:1px solid var(--border); animation:none; color:var(--text);
}
.calc-btn:hover { border-color:var(--primary); transform:none; box-shadow:none; }
.calc-btn:active { background:#26262f; }
.calc-op { color:var(--primary); font-weight:700; }
.calc-clr { color:var(--accent); font-weight:700; }
.calc-eq { grid-column:span 4; background:var(--primary); color:#0a0a10; font-weight:800; margin-top:2px; }
.calc-eq:hover { border-color:var(--primary); opacity:0.9; }
.calc-result { margin-top:0.8rem; font-size:1.3rem; color:var(--primary); font-weight:700; }

/* Push chat log up so messages aren't hidden behind the bar */
.chat-log { padding-bottom: 80px; }

/* --- Fixed bottom chat input (only on AI Chat page) --- */
.chat-fullscreen .chat-input-area {
  position: fixed !important;
  bottom: 0; left: 0; right: 0; width: 100%;
  background: var(--surface); border-top: 1px solid var(--border);
  padding: 0.8rem 1.5rem; z-index: 20;
  display: flex; align-items: center; gap: 0.5rem;
  box-shadow: 0 -5px 20px rgba(0,0,0,0.6);
}

/* ---- Animated cards ---- */
.card {
  position: relative; overflow: hidden;
  background: var(--surface); border-radius: 1rem; padding: 1.2rem;
  border: 1px solid #2a2a35; transition: all 0.4s ease;
}
.card::before {
  content: ''; position: absolute; top: 0; left: -100%;
  width: 100%; height: 100%;
  background: linear-gradient(90deg, transparent, rgba(0,229,255,0.08), transparent);
  transition: left 0.5s ease;
}
.card:hover::before { left: 100%; }
.card:hover {
  border-color: #00e5ff;
  box-shadow: 0 0 25px rgba(0,229,255,0.25), 0 0 50px rgba(0,229,255,0.1);
  transform: translateY(-4px);
}

/* ---- Techy animated inputs ---- */
.tech-input, .tech-textarea {
  border: 2px solid #2a2a35; background: #1a1a24; color: #e0e0e0;
  padding: 0.7rem 1rem; border-radius: 0.5rem; outline: none;
  transition: all 0.3s ease;
  background-image: linear-gradient(90deg, transparent 0%, rgba(0,229,255,0.05) 50%, transparent 100%);
  background-size: 200% 100%;
  animation: inputGlow 3s infinite linear;
}
@keyframes inputGlow {
  0% { background-position: 200% 0; }
  100% { background-position: -200% 0; }
}
.tech-input:focus, .tech-textarea:focus {
  border-color: #00e5ff;
  box-shadow: 0 0 15px rgba(0,229,255,0.3), 0 0 30px rgba(0,229,255,0.1);
  background-image: linear-gradient(90deg, transparent 0%, rgba(0,229,255,0.1) 50%, transparent 100%);
}

/* ---- Animated buttons ---- */
button, .card button {
  position: relative;
  background: linear-gradient(135deg, #00e5ff, #ff4081);
  background-size: 200% 200%;
  border: none; color: white; font-weight: bold;
  padding: 0.7rem 1.2rem; border-radius: 0.5rem; cursor: pointer;
  transition: all 0.4s ease;
  animation: buttonShift 4s ease infinite;
}
button:hover, .card button:hover {
  box-shadow: 0 0 20px rgba(0,229,255,0.5), 0 0 40px rgba(255,64,129,0.3);
  transform: translateY(-2px);
}
@keyframes buttonShift {
  0% { background-position: 0% 50%; }
  50% { background-position: 100% 50%; }
  100% { background-position: 0% 50%; }
}

/* ---- Chat layout ---- */
.chat-fullscreen { padding:0; margin:0; }
.chat-header {
  display: flex; justify-content: space-between; align-items: center;
  padding: 1rem 1.5rem; background: var(--surface);
  border-bottom: 1px solid var(--border); position: sticky; top:0; z-index:10;
}
.chat-header h2 { margin:0; color: var(--primary); font-size:1.5rem; }
.clear-chat-btn {
  background: transparent; border:1px solid var(--border); color: var(--text);
  padding: 0.4rem 0.8rem; border-radius:0.4rem; cursor:pointer; font-size:0.9rem;
  transition:0.2s; animation:none;
}
.clear-chat-btn:hover { background: rgba(255,64,129,0.2); border-color: var(--accent); box-shadow:none; transform:none; }

.chat-log {
  flex:1; overflow-y:auto; padding: 1rem 1.5rem;
  display:flex; flex-direction:column; gap:1rem; scroll-behavior: smooth;
}

.chat-input-area textarea {
  flex:1; background:#0b0b0f; border:1px solid var(--border);
  border-radius:0.6rem; color:white; padding:0.7rem 1rem;
  resize:none; outline:none; font-size:1rem; transition:0.2s;
}
.chat-input-area textarea:focus {
  border-color: var(--primary);
  box-shadow: 0 0 10px rgba(0,229,255,0.3);
}
.send-btn {
  background: linear-gradient(135deg, var(--primary), var(--accent));
  border:none; color:white; width:48px; height:48px; border-radius:50%;
  font-size:1.4rem; cursor:pointer;
  display:flex; align-items:center; justify-content:center; transition:0.2s;
}
.send-btn:hover {
  transform: scale(1.1);
  box-shadow: 0 0 20px rgba(0,229,255,0.5);
}

/* ---- Message bubbles & avatars ---- */
.chat-msg-wrapper {
  display:flex; align-items:flex-start; gap:0.8rem; max-width:80%;
  animation: messageSlide 0.3s ease;
}
@keyframes messageSlide {
  from { opacity:0; transform:translateY(10px); }
  to { opacity:1; transform:translateY(0); }
}
.chat-msg-wrapper.user { align-self:flex-end; flex-direction:row-reverse; }
.chat-msg-wrapper.ai   { align-self:flex-start; }

.avatar { font-size:2rem; width:40px; text-align:center; line-height:1; }

.bubble {
  background: rgba(0,229,255,0.05);
  border: 1px solid rgba(0,229,255,0.2);
  border-radius: 1rem; padding: 0.7rem 1rem;
  font-size:0.95rem; line-height:1.5; word-break: break-word;
  backdrop-filter: blur(5px); position:relative;
}
.ai .bubble { border-left: 3px solid var(--primary); }

/* ---- Techy user bubble ---- */
.user .bubble {
  background: linear-gradient(135deg, rgba(255,64,129,0.15), rgba(0,229,255,0.08));
  border: 2px solid;
  border-image-slice: 1;
  border-image-source: linear-gradient(45deg, #ff4081, #00e5ff);
  animation: techPulse 2s infinite alternate, messageSlide 0.3s ease;
}
@keyframes techPulse {
  0% { box-shadow: 0 0 8px rgba(255,64,129,0.4), 0 0 15px rgba(0,229,255,0.2); }
  100% { box-shadow: 0 0 15px rgba(255,64,129,0.6), 0 0 25px rgba(0,229,255,0.4); }
}

/* ---- AI message formatting ---- */
.chat-msg.ai {
  background: rgba(0,229,255,0.05);
  border-left: 3px solid #00e5ff;
  padding: 0.8rem 1rem; border-radius:0.5rem; margin:0.5rem 0;
  animation: fadeInUp 0.4s ease; backdrop-filter:blur(5px);
  font-family: 'Segoe UI', system-ui, -apple-system, sans-serif;
  line-height:1.6; word-wrap:break-word;
}
@keyframes fadeInUp {
  from { opacity:0; transform:translateY(10px); }
  to { opacity:1; transform:translateY(0); }
}
.chat-msg.ai pre {
  background:#0a0a10; border:1px solid #2a2a35; border-radius:0.4rem;
  padding:0.8rem; margin:0.5rem 0; overflow-x:auto;
  font-family: 'Fira Code', 'Cascadia Code', monospace;
  font-size:0.9rem; white-space:pre-wrap; word-break:break-all;
}
.chat-msg.ai code {
  background:#0a0a10; padding:0.2em 0.4em; border-radius:0.2rem;
  font-family: 'Fira Code', 'Cascadia Code', monospace; font-size:0.9em;
}
.chat-msg.ai pre code { background:transparent; padding:0; }

/* ---- Typing indicator ---- */
.typing-indicator {
  display:flex; align-items:center; gap:4px; padding:0.5rem 0;
  align-self:flex-start;
}
.typing-indicator span {
  width:8px; height:8px; background: var(--primary); border-radius:50%;
  animation: typingBounce 1.4s infinite ease-in-out;
}
.typing-indicator span:nth-child(2) { animation-delay:0.2s; }
.typing-indicator span:nth-child(3) { animation-delay:0.4s; }
@keyframes typingBounce {
  0%,80%,100%{ transform:translateY(0); }
  40%{ transform:translateY(-10px); }
}

</style>
</head>
<body>

<!-- Launch -->
<div id="launch-overlay">
  <div class="launch-logo">⚡ E.TBYTES</div>
  <div class="launch-form">
    <input class="tech-input"  type="password" id="launch-password" placeholder="Enter password" autofocus>
    <button onclick="verifyPassword()">Enter the Matrix</button>
    <div class="launch-error" id="launch-error"></div>
  </div>
</div>

<!-- Top bar -->
<header class="topbar">
  <button class="hamburger" id="hamburger" onclick="toggleDrawer()">
    <span></span><span></span><span></span>
  </button>
  <div class="topbar-brand">⚡ E.TBYTES</div>
  <div class="topbar-right">
    <span class="badge badge-cyan" id="clock-badge">--:--:--</span>
    <span class="badge badge-green">🟢 Online</span>
  </div>
</header>

<div class="drawer-backdrop" id="drawer-backdrop" onclick="toggleDrawer()"></div>

<!-- Drawer -->
<nav class="drawer" id="drawer">
  <div class="nav-group-label">Overview</div>
  <a href="#" data-nav="home" onclick="navigate('home')">🏠 Dashboard</a>
  <a href="#" data-nav="chat" onclick="navigate('chat')">💬 AI Chat</a>
  <div class="nav-group-label">Create</div>
  <a href="#" data-nav="generate" onclick="navigate('generate')">✨ Generate</a>
  <a href="#" data-nav="math" onclick="navigate('math')">🧮 Math Solver</a>
  <a href="#" data-nav="code" onclick="navigate('code')">💻 Code Assistant</a>
  <a href="#" data-nav="language" onclick="navigate('language')">🌍 Language Learning</a>
  <div class="nav-group-label">Explore</div>
  <a href="#" data-nav="news" onclick="navigate('news')">📰 News Briefing</a>
  <a href="#" data-nav="download" onclick="navigate('download')">📥 Downloads</a>
  <a href="#" data-nav="games" onclick="navigate('games')">🎮 Games & Tools</a>
  <a href="#" data-nav="ascii" onclick="navigate('ascii')">🎨 ASCII Art</a>
  <a href="#" data-nav="music" onclick="navigate('music')">🎵 Music Player</a>
  <a href="#" data-nav="fiction" onclick="navigate('fiction')">🎲 Interactive Fiction</a>
  <div class="nav-group-label">Manage</div>
  <a href="#" data-nav="files" onclick="navigate('files')">📁 File Browser</a>
  <a href="#" data-nav="tasks" onclick="navigate('tasks')">📋 Task Manager</a>
  <a href="#" data-nav="organise" onclick="navigate('organise')">🗂️ AI File Organiser</a>
  <a href="#" data-nav="deps" onclick="navigate('deps')">🔍 Dependency Scanner</a>
  <a href="#" data-nav="logs" onclick="navigate('logs')">📜 View Logs</a>
  <a href="#" data-nav="settings" onclick="navigate('settings')">⚙️ Settings</a>
</nav>

<!-- Main Content -->
<div class="main-content" id="main-content">
  <!-- Home -->
  <div id="home-section" class="section active">
    <div class="page-title">🏠 Dashboard</div>
    <div class="page-subtitle">Everything your assistant can do, in one place.</div>
    <div class="stat-strip" id="stat-strip"></div>
    <div id="home-categories"></div>
  </div>

  <!-- Chat -->
  <div id="chat-section" class="section chat-fullscreen">
    <div class="chat-header">
      <h2>🤖 AI Chat</h2>
      <button class="clear-chat-btn" onclick="clearChat()">Clear</button>
    </div>
    <div class="chat-log" id="chat-log">
      <!-- messages will appear here -->
    </div>
    <div class="chat-input-area">
      <textarea id="chat-input" placeholder="Type a message..." rows="1"></textarea>
      <button class="send-btn" onclick="sendChat()">➤</button>
    </div>
  </div>

  <!-- Games -->
  <div id="games-section" class="section">
    <div class="page-title">🎮 Games & Tools</div>
    <div class="page-subtitle">Pick something to play or generate.</div>
    <div class="dashboard-grid" id="games-grid"></div>
    <div class="game-panel" id="game-panel" style="display:none"></div>
  </div>

  <!-- ASCII Art -->
  <div id="ascii-section" class="section">
    <div class="page-title">🎨 ASCII Art Generator</div>
    <div class="page-subtitle">Turn a photo into colored or grayscale ASCII art, or let AI draw one from a description.</div>

    <div class="card">
      <h3>📷 Take a photo</h3>
      <p style="color:#9a9aa5;font-size:0.85rem;margin-bottom:0.6rem">Opens your camera with a live viewfinder so you can see the shot before capturing.</p>
      <select id="ascii-cam-choice" onchange="asciiSyncCameraFacing()"><option value="environment">Back camera</option><option value="user">Front camera</option></select>
      <input class="tech-input" id="ascii-cam-file" type="file" accept="image/*" capture="environment" style="margin-top:0.6rem">
      <label style="display:block;margin-top:0.6rem">Width: <input class="tech-input" id="ascii-cam-width" type="number" min="10" max="300" value="100" style="width:90px;display:inline-block"></label>
      <select id="ascii-cam-mode" style="margin-top:0.6rem"><option value="color">Color</option><option value="grayscale">Grayscale</option></select>
      <label style="display:block;margin-top:0.6rem"><input type="checkbox" id="ascii-cam-hq" checked> High accuracy (dithering)</label>
      <button style="margin-top:0.8rem" onclick="asciiFromCamera()">Convert</button>
      <div id="ascii-cam-status" style="margin-top:0.6rem;color:#9a9aa5"></div>
    </div>

    <div class="card">
      <h3>🖼️ Choose an existing photo</h3>
      <p style="color:#9a9aa5;font-size:0.85rem;margin-bottom:0.6rem">Opens your photo library / file picker.</p>
      <input class="tech-input" id="ascii-upload-file" type="file" accept="image/*">
      <label style="display:block;margin-top:0.6rem">Width: <input class="tech-input" id="ascii-upload-width" type="number" min="10" max="300" value="120" style="width:90px;display:inline-block"></label>
      <select id="ascii-upload-mode" style="margin-top:0.6rem"><option value="color">Color</option><option value="grayscale">Grayscale</option></select>
      <label style="display:block;margin-top:0.6rem"><input type="checkbox" id="ascii-upload-hq" checked> High accuracy (dithering)</label>
      <button style="margin-top:0.8rem" onclick="asciiFromUpload()">Convert</button>
      <div id="ascii-upload-status" style="margin-top:0.6rem;color:#9a9aa5"></div>
    </div>

    <div class="card">
      <h3>✨ AI-generated ASCII art</h3>
      <input class="tech-input" id="ascii-ai-desc" placeholder="e.g. a cat sitting on a moon">
      <button style="margin-top:0.6rem" onclick="asciiFromText()">Generate</button>
    </div>

    <div id="ascii-result-wrap" style="display:none;margin-top:1rem">
      <div id="ascii-result"></div>
      <button style="margin-top:0.8rem" onclick="asciiDownload()">💾 Download as .txt</button>
    </div>
  </div>

  <!-- Music Player -->
  <div id="music-section" class="section">
    <div class="page-title">🎵 Music Player</div>
    <div class="page-subtitle">Playback happens on this device's speaker via mpv, same as the terminal app.</div>
    <div class="card">
      <h3>Local files</h3>
      <input class="tech-input" id="music-dir" placeholder="Directory (default: ~/storage/music)">
      <button onclick="musicListFiles()">List Files</button>
      <div id="music-file-list" style="margin-top:0.8rem"></div>
    </div>
    <div class="card">
      <h3>Play from a URL (e.g. YouTube)</h3>
      <input class="tech-input" id="music-url">
      <button onclick="musicPlayUrl()">Play</button>
      <div id="music-status" style="margin-top:0.6rem;color:#9a9aa5"></div>
    </div>
  </div>

  <!-- Interactive Fiction -->
  <div id="fiction-section" class="section">
    <div class="page-title">🎲 Interactive Fiction</div>
    <div class="page-subtitle">An AI dungeon master leads a text adventure. Type 'look' to re-examine your surroundings.</div>
    <button onclick="fictionStart()">Start New Adventure</button>
    <div id="fiction-log" style="margin-top:1rem;display:flex;flex-direction:column;gap:0.8rem"></div>
    <div id="fiction-input-area" style="display:none;margin-top:1rem;display:flex;gap:0.5rem">
      <input class="tech-input" id="fiction-action" placeholder="Your action..." style="flex:1" onkeydown="if(event.key==='Enter')fictionAction()">
      <button onclick="fictionAction()">Go</button>
    </div>
  </div>

  <!-- AI File Organiser -->
  <div id="organise-section" class="section">
    <div class="page-title">🗂️ AI File Organiser</div>
    <div class="page-subtitle">AI suggests a folder structure for ~/storage/downloads.</div>
    <button onclick="organisePreview()">Preview Plan</button>
    <div id="organise-result" style="margin-top:1rem"></div>
  </div>

  <!-- Dependency Scanner -->
  <div id="deps-section" class="section">
    <div class="page-title">🔍 Dependency Scanner</div>
    <div class="page-subtitle">Installed Python packages (pip freeze).</div>
    <button onclick="scanDeps()">Scan</button>
    <div id="deps-result" style="margin-top:1rem"></div>
  </div>

  <!-- View Logs -->
  <div id="logs-section" class="section">
    <div class="page-title">📜 Activity Logs</div>
    <div class="page-subtitle">Last 300 log lines.</div>
    <button onclick="loadLogs()">Refresh</button>
    <pre id="logs-result" style="margin-top:1rem;max-height:60vh;overflow-y:auto"></pre>
  </div>

  <!-- Download -->
  <div id="download-section" class="section">
    <div class="page-title">📥 Download Files</div>
    <div class="page-subtitle">Extract media links from any webpage.</div>
    <div class="card">
      <h3>Enter URL & type</h3>
      <input class="tech-input"  id="dl-url" placeholder="https://...">
      <select id="dl-type">
        <option value="2">Images</option>
        <option value="3">Videos</option>
        <option value="4">Audio</option>
        <option value="5">Documents</option>
        <option value="6">Image URLs (list)</option>
        <option value="7">Video URLs (list)</option>
        <option value="8">Audio URLs (list)</option>
        <option value="1">Full site mirror (wget)</option>
      </select>
      <button onclick="startDownload()">Download</button>
      <div id="dl-status"></div>
    </div>
  </div>

  <!-- Generate -->
  <div id="generate-section" class="section">
    <div class="page-title">✨ Generate Content</div>
    <div class="page-subtitle">Create images, PDFs, and text with AI.</div>
    <div class="card">
      <h3>Generate Image</h3>
      <input class="tech-input"  id="img-prompt" placeholder="Image description">
      <button onclick="generateImage()">Create Image</button>
      <div id="img-result"></div>
    </div>
    <div class="card">
      <h3>Generate PDF</h3>
      <textarea class="tech-textarea"  id="pdf-text" placeholder="Text for PDF..."></textarea>
      <button onclick="generatePDF()">Generate PDF</button>
    </div>
    <div class="card">
      <h3>Generate / Enhance Text</h3>
      <textarea class="tech-textarea"  id="txt-input" placeholder="Enter text or topic..."></textarea>
      <select id="txt-mode"><option value="enhance">Enhance</option><option value="new">New text</option></select>
      <button onclick="generateTXT()">Generate TXT</button>
      <div id="txt-result"></div>
    </div>
  </div>

  <!-- Math -->
  <div id="math-section" class="section">
    <div class="page-title">🧮 Math Solver</div>
    <div class="page-subtitle">Step-by-step solutions powered by Groq.</div>
    <div class="card">
      <input class="tech-input"  id="math-expr" placeholder="e.g. x^2 + 2x - 3 = 0">
      <select id="math-mode"><option value="steps">Step-by-step</option><option value="answer">Answer only</option></select>
      <button onclick="solveMath()">Solve</button>
      <pre id="math-result"></pre>
    </div>
  </div>

  <!-- News -->
  <div id="news-section" class="section">
    <div class="page-title">📰 News Briefing</div>
    <div class="page-subtitle">AI-summarized headlines.</div>
    <button onclick="fetchNews()">Fetch & Summarize</button>
    <div id="news-result"></div>
  </div>

  <!-- Code -->
  <div id="code-section" class="section">
    <div class="page-title">💻 Code Assistant</div>
    <div class="page-subtitle">Explain, refactor, or generate tests.</div>
    <textarea class="tech-textarea"  id="code-input" placeholder="Paste your code here..." rows="8"></textarea>
    <select id="code-action">
      <option value="explain">Explain</option>
      <option value="refactor">Refactor</option>
      <option value="tests">Generate tests</option>
    </select>
    <button onclick="assistCode()">Go</button>
    <pre id="code-result"></pre>
  </div>

  <!-- Language -->
  <div id="language-section" class="section">
    <div class="page-title">🌍 Language Learning</div>
    <div class="page-subtitle">Lessons and flashcards for any language.</div>
    <div class="card">
      <h3>AI Lesson</h3>
      <input class="tech-input"  id="lang-name" placeholder="Language (e.g. Spanish)">
      <select id="lang-level"><option>beginner</option><option>intermediate</option><option>advanced</option></select>
      <input class="tech-input"  id="lang-topic" placeholder="Topic (e.g. greetings)">
      <button onclick="startLesson()">Generate Lesson</button>
      <pre id="lesson-result"></pre>
    </div>
    <div class="card">
      <h3>Flashcards</h3>
      <input class="tech-input"  id="flash-lang" placeholder="Language for flashcards">
      <button onclick="loadFlashcards()">Start</button>
      <div id="flashcard-area"></div>
    </div>
  </div>

  <!-- Files -->
  <div id="files-section" class="section">
    <div class="page-title">📁 File Browser</div>
    <div class="page-subtitle">Browse your home directory.</div>
    <div id="file-list"></div>
  </div>

  <!-- Tasks -->
  <div id="tasks-section" class="section">
    <div class="page-title">📋 Task Manager</div>
    <div class="page-subtitle">Keep track of your to-dos.</div>
    <input class="tech-input"  id="task-desc" placeholder="Describe task...">
    <button onclick="addTask()">Add Task</button>
    <div id="task-list"></div>
  </div>

  <!-- Settings -->
  <div id="settings-section" class="section">
    <div class="page-title">⚙️ Settings</div>
    <div class="page-subtitle">Manage your password, API key, and assistant behavior.</div>
    <div class="card">
      <h3>Change Password</h3>
      <input class="tech-input"  type="password" id="new-password" placeholder="New password">
      <button onclick="changePassword()">Update</button>
    </div>
    <div class="card">
      <h3>Groq API Key</h3>
      <input class="tech-input"  id="api-key-input" placeholder="gsk_...">
      <button onclick="saveApiKey()">Save Key</button>
    </div>
    <div class="card">
      <h3>Assistant Settings</h3>
      <label>Model</label>
      <input class="tech-input" id="set-model" placeholder="llama-3.1-8b-instant">
      <label style="display:block;margin-top:0.6rem">User Name</label>
      <input class="tech-input" id="set-user-name">
      <label style="display:block;margin-top:0.6rem">TTS Voice</label>
      <input class="tech-input" id="set-tts-voice" placeholder="en-us-female">
      <label style="display:block;margin-top:0.6rem">AI Personality</label>
      <textarea class="tech-textarea" id="set-personality" rows="3"></textarea>
      <label style="display:block;margin-top:0.6rem"><input type="checkbox" id="set-theme-dark"> Dark theme</label>
      <label style="display:block;margin-top:0.4rem"><input type="checkbox" id="set-file-watcher"> File watcher enabled</label>
      <label style="display:block;margin-top:0.4rem"><input type="checkbox" id="set-git-commit"> Git auto-commit</label>
      <label style="display:block;margin-top:0.4rem"><input type="checkbox" id="set-offline"> Offline mode</label>
      <label style="display:block;margin-top:0.4rem"><input type="checkbox" id="set-learning"> Learning enabled</label>
      <button style="margin-top:0.8rem" onclick="saveSettings()">Save Settings</button>
    </div>
  </div>
</div>

<script>
// ── Global state ──
let currentSection = 'home';
let drawerOpen = false;
let flashcardData = [];
let flashcardIndex = 0;

// ── Live clock ──
function tickClock() {
  const el = document.getElementById('clock-badge');
  if (el) el.textContent = new Date().toLocaleTimeString();
}
setInterval(tickClock, 1000);
tickClock();

// ── Launch verification ──
async function verifyPassword() {
  const pwd = document.getElementById('launch-password').value;
  const resp = await fetch('/api/verify_password', {
    method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({password:pwd})
  });
  const data = await resp.json();
  if (data.success) {
    document.getElementById('launch-overlay').classList.add('launch-hidden');
    buildHomeCards();
    buildStatStrip();
  } else {
    document.getElementById('launch-error').innerText = 'Wrong password!';
  }
}

// Enter key sends message
document.getElementById('chat-input').addEventListener('keydown', function(e) {
  if (e.key === 'Enter' && !e.shiftKey) {
    e.preventDefault();
    sendChat();
  }
});

document.getElementById('launch-password').addEventListener('keyup', e => { if(e.key==='Enter') verifyPassword(); });

// ── Drawer ──
function toggleDrawer() {
  drawerOpen = !drawerOpen;
  document.getElementById('drawer').classList.toggle('open', drawerOpen);
  document.getElementById('drawer-backdrop').classList.toggle('show', drawerOpen);
  document.getElementById('hamburger').classList.toggle('active', drawerOpen);
}

// ── Navigation ──
function navigate(section) {
  if (drawerOpen) toggleDrawer();
  document.querySelectorAll('.section').forEach(el => el.classList.remove('active'));
  document.getElementById(section + '-section').classList.add('active');
  document.querySelectorAll('.drawer a').forEach(a => a.classList.toggle('active', a.dataset.nav === section));
  currentSection = section;
  if (section === 'chat') document.getElementById('chat-log').innerHTML = '';
  if (section === 'tasks') refreshTasks();
  if (section === 'files') loadFileBrowser('/');
  if (section === 'settings') loadSettings();
  if (section === 'games') buildGamesGrid();
  if (section === 'logs') loadLogs();
  if (section === 'home') { buildHomeCards(); buildStatStrip(); }
}

// ── Home dashboard ──
const TOOL_CATEGORIES = [
  { cat:'Create', items:[
    { name:'AI Chat', emoji:'💬', nav:'chat', desc:'Chat with your Groq-powered assistant' },
    { name:'Generate', emoji:'✨', nav:'generate', desc:'Images, PDFs and text on demand' },
    { name:'Math Solver', emoji:'🧮', nav:'math', desc:'Step-by-step or instant answers' },
    { name:'Code Assistant', emoji:'💻', nav:'code', desc:'Explain, refactor or test your code' },
    { name:'Language Learning', emoji:'🌍', nav:'language', desc:'Lessons and flashcards' },
  ]},
  { cat:'Explore', items:[
    { name:'News Briefing', emoji:'📰', nav:'news', desc:'AI-summarized headlines' },
    { name:'Downloads', emoji:'📥', nav:'download', desc:'Pull media links from any page' },
    { name:'Games & Tools', emoji:'🎮', nav:'games', desc:'Quizzes, puzzles and utilities' },
    { name:'ASCII Art', emoji:'🎨', nav:'ascii', desc:'Photos & AI-drawn ASCII art' },
    { name:'Music Player', emoji:'🎵', nav:'music', desc:'Play local files or a URL' },
    { name:'Interactive Fiction', emoji:'🎲', nav:'fiction', desc:'AI dungeon master text adventure' },
  ]},
  { cat:'Manage', items:[
    { name:'File Browser', emoji:'📁', nav:'files', desc:'Browse your home directory' },
    { name:'Task Manager', emoji:'📋', nav:'tasks', desc:'Track your to-dos' },
    { name:'AI File Organiser', emoji:'🗂️', nav:'organise', desc:'Auto-sort your Downloads' },
    { name:'Dependency Scanner', emoji:'🔍', nav:'deps', desc:'Installed Python packages' },
    { name:'View Logs', emoji:'📜', nav:'logs', desc:'Recent activity log' },
    { name:'Settings', emoji:'⚙️', nav:'settings', desc:'Password and API key' },
  ]},
];

function buildHomeCards() {
  const wrap = document.getElementById('home-categories');
  wrap.innerHTML = TOOL_CATEGORIES.map(group => `
    <div class="category-title">${group.cat}</div>
    <div class="dashboard-grid">
      ${group.items.map(t => `
        <div class="card" onclick="navigate('${t.nav}')" style="cursor:pointer">
          <div class="card-icon">${t.emoji}</div>
          <h3>${t.name}</h3>
          <p>${t.desc}</p>
        </div>`).join('')}
    </div>`).join('');
}

async function buildStatStrip() {
  const strip = document.getElementById('stat-strip');
  let taskData = {}, cfg = {};
  try { taskData = await api('/api/tasks', 'GET'); } catch(e) {}
  try { cfg = await api('/api/get_config', 'GET'); } catch(e) {}
  const tasks = taskData.tasks || [];
  const pending = tasks.filter(t => !t.completed).length;
  const toolCount = TOOL_CATEGORIES.reduce((n, g) => n + g.items.length, 0);
  strip.innerHTML = `
    <div class="stat-pill"><div class="stat-value">${pending}/${tasks.length}</div><div class="stat-label">Tasks Pending</div></div>
    <div class="stat-pill"><div class="stat-value">${cfg.api_key ? 'Configured' : 'Not set'}</div><div class="stat-label">Groq API Key</div></div>
    <div class="stat-pill"><div class="stat-value">${toolCount}</div><div class="stat-label">Tools Available</div></div>
  `;
}

// ── API helper ──
async function api(url, method='POST', body=null) {
  const opts = { method, headers:{'Content-Type':'application/json'} };
  if (method !== 'GET' && body !== null) opts.body = JSON.stringify(body);
  const resp = await fetch(url, opts);
  return resp.json();
}

// ── Chat ──
async function sendChat() {
  const input = document.getElementById('chat-input').value.trim();
  if (!input) return;
  appendChat('user', input);
  document.getElementById('chat-input').value = '';
  const log = document.getElementById('chat-log');
  const typing = document.createElement('div');
  typing.className = 'typing-indicator';
  typing.innerHTML = '<span></span><span></span><span></span>';
  log.appendChild(typing);
  log.scrollTop = log.scrollHeight;
  const data = await api('/api/chat', 'POST', {message:input});
  typing.remove();
  if (data.reply) appendChat('ai', data.reply);
}
function appendChat(role, text) {
  const log = document.getElementById('chat-log');
  const wrapper = document.createElement('div');
  wrapper.className = `chat-msg-wrapper ${role}`;
  const avatar = document.createElement('div');
  avatar.className = 'avatar';
  avatar.innerHTML = role === 'user' ? '🧑‍💻' : '🤖';
  const bubble = document.createElement('div');
  bubble.className = 'bubble';
  wrapper.appendChild(avatar);
  wrapper.appendChild(bubble);
  log.appendChild(wrapper);
  log.scrollTop = log.scrollHeight;
  if (role === 'ai') {
    typeWriter(bubble, text, 30, function() {
      formatAIMessage(bubble);
    });
  } else {
    bubble.innerText = text;
  }
}

// ── Games ──
function buildGamesGrid() {
  const games = [
    { name:'Number Guess', emoji:'🔢', desc:'Guess 1-100 in the fewest tries', fn:'numberGuess()' },
    { name:'Hangman', emoji:'🪢', desc:'Save the stick figure', fn:'hangmanStart()' },
    { name:'Tic-Tac-Toe', emoji:'⭕', desc:'Beat the computer', fn:'tttStart()' },
    { name:'General Quiz', emoji:'❓', desc:'Trivia challenge', fn:"quizStart('general')" },
    { name:'Tech Quiz', emoji:'🖥️', desc:'Tech & computing trivia', fn:"quizStart('tech')" },
    { name:'Password Gen', emoji:'🔑', desc:'Strong random passwords', fn:'passwordGenStart()' },
    { name:'QR Code', emoji:'▦', desc:'Turn text or a link into a QR code', fn:'qrMakerStart()' },
    { name:'Weather', emoji:'⛅', desc:'Live conditions by city', fn:'weatherStart()' },
    { name:'ASCII/Binary', emoji:'🔤', desc:'Text to ASCII codes & binary', fn:'asciiBinStart()' },
    { name:'Hex/Oct/Bin', emoji:'🔢', desc:'Convert a decimal number', fn:'hexOctStart()' },
    { name:'Regex Tester', emoji:'🧩', desc:'Test a pattern against text', fn:'regexTesterStart()' },
    { name:'Calculator', emoji:'🧮', desc:'Evaluate a math expression', fn:'calculatorStart()' },
    { name:'Rock Paper Scissors', emoji:'✊', desc:'Play against the computer', fn:'rpsStart()' },
    { name:'Design Patterns', emoji:'📐', desc:'AI-explained pattern with real code', fn:'designPatternStart()' },
    { name:'Web Scraper', emoji:'🕸️', desc:'Title, description, links & images', fn:'webScraperStart()' },
    { name:'Plot Data', emoji:'📈', desc:'Chart your own numbers', fn:'plotDataStart()' },
    { name:'Socket Chat', emoji:'📡', desc:'Real TCP chat room -- talk with nc from any device', fn:'socketChatStart()' },
  ];
  document.getElementById('games-grid').innerHTML = games.map(g => `
    <div class="card" style="cursor:pointer" onclick="${g.fn}">
      <div class="card-icon">${g.emoji}</div>
      <h3>${g.name}</h3>
      <p>${g.desc}</p>
    </div>`).join('');
  closeGamePanel();
}
function showGamePanel(html) {
  stopSocketChatPolling();
  const panel = document.getElementById('game-panel');
  panel.style.display = 'block';
  panel.innerHTML = '<button class="game-close" onclick="closeGamePanel()">✕</button>' + html;
  panel.scrollIntoView({behavior:'smooth', block:'nearest'});
}
function closeGamePanel() {
  stopSocketChatPolling();
  const panel = document.getElementById('game-panel');
  panel.style.display = 'none';
  panel.innerHTML = '';
}

// -- Number Guess --
let ngTarget = 0, ngAttempts = 0;
function numberGuess() {
  ngTarget = Math.floor(Math.random()*100) + 1;
  ngAttempts = 0;
  showGamePanel(`
    <h3>🔢 Number Guess</h3>
    <p>I'm thinking of a number between 1 and 100.</p>
    <input class="tech-input" id="ng-input" type="number" min="1" max="100" placeholder="Your guess"
           onkeydown="if(event.key==='Enter')ngGuess()">
    <button onclick="ngGuess()">Guess</button>
    <p id="ng-feedback" style="margin-top:0.8rem"></p>
  `);
}
function ngGuess() {
  const val = parseInt(document.getElementById('ng-input').value);
  const fb = document.getElementById('ng-feedback');
  if (isNaN(val)) { fb.textContent = 'Enter a number first.'; return; }
  ngAttempts++;
  if (val === ngTarget) fb.textContent = `🎉 Correct! It was ${ngTarget}. Attempts: ${ngAttempts}`;
  else if (val < ngTarget) fb.textContent = `Higher than ${val}... (attempt ${ngAttempts})`;
  else fb.textContent = `Lower than ${val}... (attempt ${ngAttempts})`;
}

// -- Hangman --
const HANGMAN_WORDS = ['python','termux','android','keyboard','network','function','variable','internet','assistant','firewall','database','terminal','wireless','satellite','processor'];
const HANGMAN_STAGES = [
`  +---+
      |
      |
      |
     ===`,
`  +---+
  O   |
      |
      |
     ===`,
`  +---+
  O   |
  |   |
      |
     ===`,
`  +---+
  O   |
 /|   |
      |
     ===`,
`  +---+
  O   |
 /|\\  |
      |
     ===`,
`  +---+
  O   |
 /|\\  |
 /    |
     ===`,
`  +---+
  O   |
 /|\\  |
 / \\  |
     ===`
];
let hmWord = '', hmGuessed = [], hmWrong = 0;
function hangmanStart() {
  hmWord = HANGMAN_WORDS[Math.floor(Math.random()*HANGMAN_WORDS.length)];
  hmGuessed = [];
  hmWrong = 0;
  renderHangman();
}
function renderHangman() {
  const display = hmWord.split('').map(l => hmGuessed.includes(l) ? l : '_').join(' ');
  const alphabet = 'abcdefghijklmnopqrstuvwxyz'.split('');
  const won = !display.includes('_');
  const lost = hmWrong >= HANGMAN_STAGES.length - 1;
  showGamePanel(`
    <h3>🪢 Hangman</h3>
    <div class="hangman-box">
      <div class="hangman-fig">${HANGMAN_STAGES[hmWrong]}</div>
      <div>
        <div class="hangman-word">${display}</div>
        ${won ? '<p>🎉 You won!</p>' : lost ? `<p>💀 Out of guesses. The word was <b>${hmWord}</b>.</p>` : ''}
        <div class="keyboard">
          ${alphabet.map(l => `<button class="key-btn" ${hmGuessed.includes(l)||won||lost?'disabled':''} onclick="hangmanGuess('${l}')">${l}</button>`).join('')}
        </div>
        <button style="margin-top:1rem" onclick="hangmanStart()">New word</button>
      </div>
    </div>
  `);
}
function hangmanGuess(letter) {
  if (hmGuessed.includes(letter)) return;
  hmGuessed.push(letter);
  if (!hmWord.includes(letter)) hmWrong++;
  renderHangman();
}

// -- Tic-Tac-Toe --
let tttBoard = Array(9).fill('');
let tttOver = false;
let tttThinking = false;
const TTT_LINES = [[0,1,2],[3,4,5],[6,7,8],[0,3,6],[1,4,7],[2,5,8],[0,4,8],[2,4,6]];
function tttStart() {
  tttBoard = Array(9).fill('');
  tttOver = false;
  tttThinking = false;
  renderTTT();
}
function renderTTT(status) {
  showGamePanel(`
    <h3>⭕ Tic-Tac-Toe</h3>
    <p>You're X, the AI is O.</p>
    <div class="ttt-grid">
      ${tttBoard.map((v,i) => `<div class="ttt-cell" onclick="tttMove(${i})">${v}</div>`).join('')}
    </div>
    <p>${status || ''}</p>
    <button onclick="tttStart()">Restart</button>
  `);
}
function tttWinner(b) {
  for (const [a,c,d] of TTT_LINES) if (b[a] && b[a]===b[c] && b[a]===b[d]) return b[a];
  if (b.every(cell => cell)) return 'draw';
  return null;
}
async function tttMove(i) {
  if (tttOver || tttThinking || tttBoard[i]) return;
  tttBoard[i] = 'X';
  let w = tttWinner(tttBoard);
  if (!w) {
    tttThinking = true;
    renderTTT('🤖 AI is thinking...');
    const data = await api('/api/games/ttt_move', 'POST', {board: tttBoard});
    tttThinking = false;
    if (data && data.move >= 0 && !tttBoard[data.move]) tttBoard[data.move] = 'O';
    w = tttWinner(tttBoard);
  }
  if (w) {
    tttOver = true;
    renderTTT(w === 'draw' ? "It's a draw!" : (w === 'X' ? '🎉 You win!' : '🤖 AI wins!'));
  } else {
    renderTTT();
  }
}

// -- Quizzes --
let quizState = null;
async function quizStart(kind) {
  const title = kind === 'tech' ? '🖥️ Tech Quiz' : '❓ General Quiz';
  showGamePanel(`<h3>${title}</h3><p>🤖 AI is writing your questions...</p>`);
  const data = await api('/api/games/quiz_questions', 'POST', {kind});
  quizState = { kind, i:0, score:0, bank: data.questions || [] };
  if (!quizState.bank.length) {
    showGamePanel(`<h3>${title}</h3><p>Couldn't generate questions. Try again.</p><button onclick="quizStart('${kind}')">Retry</button>`);
    return;
  }
  renderQuiz();
}
function renderQuiz(locked, chosenIdx) {
  const st = quizState;
  const title = st.kind === 'tech' ? '🖥️ Tech Quiz' : '❓ General Quiz';
  if (st.i >= st.bank.length) {
    showGamePanel(`<h3>${title}</h3><p>Final score: ${st.score}/${st.bank.length}</p><button onclick="quizStart('${st.kind}')">Play again</button>`);
    return;
  }
  const q = st.bank[st.i];
  showGamePanel(`
    <h3>${title} — Question ${st.i+1}/${st.bank.length} (Score: ${st.score})</h3>
    <p>${q.q}</p>
    ${q.options.map((opt,idx) => `
      <button class="quiz-option ${locked ? (idx===q.a?'correct':(idx===chosenIdx?'wrong':'')) : ''}"
              ${locked?'disabled':''} onclick="quizAnswer(${idx})">${opt}</button>`).join('')}
    ${locked ? `<button style="margin-top:0.8rem" onclick="quizNext()">Next</button>` : ''}
  `);
}
function quizAnswer(idx) {
  const st = quizState;
  if (idx === st.bank[st.i].a) st.score++;
  renderQuiz(true, idx);
}
function quizNext() {
  quizState.i++;
  renderQuiz();
}

// -- Password generator --
function passwordGenStart() { renderPasswordGen(); }
function renderPasswordGen(pwd) {
  showGamePanel(`
    <h3>🔑 Password Generator</h3>
    <label>Length: <input class="tech-input" id="pg-len" type="number" min="6" max="64" value="16" style="width:80px;display:inline-block"></label>
    <br><br>
    <label><input type="checkbox" id="pg-upper" checked> Uppercase</label>
    <label style="margin-left:1rem"><input type="checkbox" id="pg-digits" checked> Digits</label>
    <label style="margin-left:1rem"><input type="checkbox" id="pg-symbols" checked> Symbols</label>
    <br><br>
    <button onclick="passwordGenerate()">Generate</button>
    <p id="pg-result" style="margin-top:0.8rem;font-family:monospace;font-size:1.1rem;word-break:break-all">${pwd || ''}</p>
    ${pwd ? `<button onclick="navigator.clipboard.writeText('${pwd}')">Copy</button>` : ''}
  `);
}
function passwordGenerate() {
  const len = Math.min(64, Math.max(6, parseInt(document.getElementById('pg-len').value) || 16));
  let chars = 'abcdefghijklmnopqrstuvwxyz';
  if (document.getElementById('pg-upper').checked) chars += 'ABCDEFGHIJKLMNOPQRSTUVWXYZ';
  if (document.getElementById('pg-digits').checked) chars += '0123456789';
  if (document.getElementById('pg-symbols').checked) chars += '!@#$%^&*()-_=+';
  const arr = new Uint32Array(len);
  crypto.getRandomValues(arr);
  let pwd = '';
  for (let i=0; i<len; i++) pwd += chars[arr[i] % chars.length];
  renderPasswordGen(pwd);
}

// -- QR maker --
function qrMakerStart() { renderQrPanel(); }
function renderQrPanel(imgSrc, err) {
  showGamePanel(`
    <h3>▦ QR Code Maker</h3>
    <input class="tech-input" id="qr-text" placeholder="Text or URL">
    <button onclick="qrGenerate()">Generate</button>
    <div style="margin-top:1rem">
      ${imgSrc ? `<img src="${imgSrc}" style="max-width:220px;border-radius:0.5rem;background:#fff;padding:0.5rem">` : ''}
      ${err ? `<p style="color:var(--accent)">${err}</p>` : ''}
    </div>
  `);
}
async function qrGenerate() {
  const text = document.getElementById('qr-text').value.trim();
  if (!text) return;
  const data = await api('/api/qr', 'POST', {text});
  renderQrPanel(data.image, data.error);
}

// -- Weather --
function weatherStart() { renderWeatherPanel(); }
function renderWeatherPanel(result, err) {
  showGamePanel(`
    <h3>⛅ Weather</h3>
    <input class="tech-input" id="wx-city" placeholder="City name" onkeydown="if(event.key==='Enter')weatherFetch()">
    <button onclick="weatherFetch()">Check</button>
    <p id="wx-result" style="margin-top:0.8rem">${result || err || ''}</p>
  `);
}
async function weatherFetch() {
  const city = document.getElementById('wx-city').value.trim();
  if (!city) return;
  document.getElementById('wx-result').textContent = 'Loading...';
  const data = await api('/api/weather', 'POST', {city});
  document.getElementById('wx-result').textContent = data.result || data.error || 'No data';
}

// -- ASCII/Binary converter --
function asciiBinStart() { renderAsciiBin(); }
function renderAsciiBin(codes, bin) {
  showGamePanel(`
    <h3>🔤 ASCII / Binary</h3>
    <input class="tech-input" id="ab-text" placeholder="Enter text" onkeydown="if(event.key==='Enter')asciiBinRun()">
    <button onclick="asciiBinRun()">Convert</button>
    ${codes ? `<p style="margin-top:0.8rem;word-break:break-all"><b>ASCII:</b> ${codes}</p><p style="word-break:break-all"><b>Binary:</b> ${bin}</p>` : ''}
  `);
}
function asciiBinRun() {
  const text = document.getElementById('ab-text').value;
  const codes = [...text].map(c => c.charCodeAt(0)).join(', ');
  const bin = [...text].map(c => c.charCodeAt(0).toString(2).padStart(8,'0')).join(' ');
  renderAsciiBin(codes, bin);
}

// -- Hex/Oct/Bin converter --
function hexOctStart() { renderHexOct(); }
function renderHexOct(result) {
  showGamePanel(`
    <h3>🔢 Hex / Oct / Bin</h3>
    <input class="tech-input" id="ho-num" type="number" placeholder="Enter decimal number" onkeydown="if(event.key==='Enter')hexOctRun()">
    <button onclick="hexOctRun()">Convert</button>
    ${result ? `<p style="margin-top:0.8rem;font-family:monospace">${result}</p>` : ''}
  `);
}
function hexOctRun() {
  const num = parseInt(document.getElementById('ho-num').value);
  if (isNaN(num)) { renderHexOct('Enter a valid number.'); return; }
  renderHexOct(`Hex: 0x${num.toString(16)}, Oct: 0o${num.toString(8)}, Bin: 0b${num.toString(2)}`);
}

// -- Regex tester --
function regexTesterStart() { renderRegexTester(); }
function renderRegexTester(result) {
  showGamePanel(`
    <h3>🧩 Regex Tester</h3>
    <input class="tech-input" id="rx-pattern" placeholder="Pattern (no slashes needed)">
    <textarea class="tech-textarea" id="rx-text" placeholder="Text to search" style="margin-top:0.6rem" rows="4"></textarea>
    <button style="margin-top:0.6rem" onclick="regexTesterRun()">Find Matches</button>
    ${result !== undefined ? `<p style="margin-top:0.8rem;word-break:break-all">Matches: ${result}</p>` : ''}
  `);
}
function regexTesterRun() {
  const pattern = document.getElementById('rx-pattern').value;
  const text = document.getElementById('rx-text').value;
  try {
    const re = new RegExp(pattern, 'g');
    const matches = [...text.matchAll(re)].map(m => m[0]);
    renderRegexTester(JSON.stringify(matches));
  } catch (e) {
    renderRegexTester(`Invalid pattern: ${e.message}`);
  }
}

// -- Calculator --
let calcExpr = '';
function calculatorStart() { calcExpr = ''; renderCalculator(); }
function renderCalculator(result) {
  const rows = [
    ['C','⌫','%','/'],
    ['7','8','9','*'],
    ['4','5','6','-'],
    ['1','2','3','+'],
    ['0','.','(',')'],
  ];
  const opChars = '/*-+';
  const btnHtml = rows.map(row => row.map(b => {
    const key = b === '⌫' ? 'DEL' : (b === 'C' ? 'CLR' : b);
    const cls = b === 'C' ? 'calc-clr' : (opChars.includes(b) ? 'calc-op' : '');
    return `<button class="calc-btn ${cls}" onclick="calcPress('${key}')">${b}</button>`;
  }).join('')).join('');
  const shown = calcExpr.length ? calcExpr.replace(/</g,'&lt;').replace(/>/g,'&gt;') : '0';
  showGamePanel(`
    <h3>🧮 Calculator</h3>
    <div class="calc-box">
      <div class="calc-display" id="calc-display">${shown}</div>
      <div class="calc-grid">
        ${btnHtml}
        <button class="calc-btn calc-eq" onclick="calcPress('=')">=</button>
      </div>
      ${result !== undefined ? `<div class="calc-result">= ${result}</div>` : ''}
    </div>
  `);
}
function calcPress(key) {
  if (key === 'CLR') { calcExpr = ''; renderCalculator(); return; }
  if (key === 'DEL') { calcExpr = calcExpr.slice(0, -1); renderCalculator(); return; }
  if (key === '=') { calculatorRun(); return; }
  calcExpr += key;
  renderCalculator();
}
function calculatorRun() {
  if (!calcExpr.length) return;
  if (!/^[0-9+\-*/().\s%]+$/.test(calcExpr)) { renderCalculator('Invalid characters'); return; }
  try {
    // eslint-disable-next-line no-new-func
    const value = Function(`"use strict"; return (${calcExpr})`)();
    renderCalculator(value);
  } catch (e) {
    renderCalculator('Invalid expression');
  }
}

// -- Rock Paper Scissors --
function rpsStart() { renderRps(); }
function renderRps(result) {
  showGamePanel(`
    <h3>✊ Rock Paper Scissors</h3>
    <button onclick="rpsPlay('rock')">🪨 Rock</button>
    <button onclick="rpsPlay('paper')">📄 Paper</button>
    <button onclick="rpsPlay('scissors')">✂️ Scissors</button>
    ${result ? `<p style="margin-top:0.8rem">${result}</p>` : ''}
  `);
}
async function rpsPlay(user) {
  renderRps('🤖 AI is thinking...');
  const data = await api('/api/games/rps_move', 'POST', {user});
  const comp = data.move || ['rock','paper','scissors'][Math.floor(Math.random()*3)];
  let outcome;
  if (user === comp) outcome = 'Tie';
  else if ((user==='rock'&&comp==='scissors')||(user==='paper'&&comp==='rock')||(user==='scissors'&&comp==='paper')) outcome = 'You win!';
  else outcome = 'You lose!';
  renderRps(`AI chose ${comp}. ${outcome}`);
}

// -- Design patterns: AI-explained, with a real runnable code example --
function designPatternStart() { renderDesignPattern(); }
function renderDesignPattern(result, loading) {
  showGamePanel(`
    <h3>📐 Design Patterns</h3>
    <select id="dp-pattern">
      ${['Singleton','Factory','Observer','Strategy','Decorator','Adapter','Builder','Command'].map(p => `<option value="${p}">${p}</option>`).join('')}
    </select>
    <input class="tech-input" id="dp-language" placeholder="Language" value="Python" style="margin-top:0.6rem">
    <button style="margin-top:0.6rem" onclick="designPatternRun()">Explain & Show Code</button>
    ${loading ? `<p style="margin-top:0.8rem;color:#9a9aa5">Thinking...</p>` : ''}
    ${result ? `<pre style="margin-top:0.8rem;white-space:pre-wrap;background:#0a0a10;border:1px solid var(--border);border-radius:0.4rem;padding:0.8rem;overflow-x:auto">${result.replace(/</g,'&lt;')}</pre>` : ''}
  `);
}
async function designPatternRun() {
  const pattern = document.getElementById('dp-pattern').value;
  const language = document.getElementById('dp-language').value.trim() || 'Python';
  renderDesignPattern(null, true);
  const data = await api('/api/games/design_pattern', 'POST', {pattern, language});
  renderDesignPattern(data.result || data.error);
}

// -- Web scraper: real extraction, not just a title --
function webScraperStart() { renderWebScraper(); }
function renderWebScraper(data, loading) {
  let body = '';
  if (loading) body = `<p style="margin-top:0.8rem;color:#9a9aa5">Fetching...</p>`;
  else if (data && data.error) body = `<p style="margin-top:0.8rem;color:var(--accent)">${data.error}</p>`;
  else if (data) {
    body = `
      <div style="margin-top:0.8rem">
        <p><b>Title:</b> ${data.title}</p>
        <p><b>Description:</b> ${data.description}</p>
        <p><b>Word count:</b> ${data.word_count}</p>
        <p><b>Links:</b> ${data.link_count} (showing up to 100) <button style="margin-left:0.5rem" onclick="webScraperDownload('links')">Download</button></p>
        <p><b>Images:</b> ${data.image_count} (showing up to 50) <button style="margin-left:0.5rem" onclick="webScraperDownload('images')">Download</button></p>
      </div>`;
  }
  showGamePanel(`
    <h3>🕸️ Web Scraper</h3>
    <input class="tech-input" id="ws-url" placeholder="https://..." onkeydown="if(event.key==='Enter')webScraperRun()">
    <button style="margin-top:0.6rem" onclick="webScraperRun()">Scrape</button>
    ${body}
  `);
  window._wsData = data && !data.error ? data : null;
}
async function webScraperRun() {
  const url = document.getElementById('ws-url').value.trim();
  if (!url) return;
  renderWebScraper(null, true);
  const data = await api('/api/games/web_scraper', 'POST', {url});
  renderWebScraper(data);
}
function webScraperDownload(kind) {
  if (!window._wsData) return;
  const text = (window._wsData[kind] || []).join('\n');
  const blob = new Blob([text], {type:'text/plain'});
  const url = URL.createObjectURL(blob);
  const a = document.createElement('a'); a.href = url; a.download = `${kind}.txt`; a.click();
  URL.revokeObjectURL(url);
}

// -- Plot data: chart the user's own numbers --
function plotDataStart() { renderPlotData(); }
function renderPlotData(imgSrc, err) {
  showGamePanel(`
    <h3>📈 Plot Data</h3>
    <select id="pd-kind" onchange="plotDataToggleFields()">
      <option value="line">Line</option><option value="bar">Bar</option>
      <option value="scatter">Scatter</option><option value="pie">Pie</option>
    </select>
    <div id="pd-xy">
      <input class="tech-input" id="pd-x" placeholder="X values (comma separated)" value="1,2,3,4,5" style="margin-top:0.6rem">
      <input class="tech-input" id="pd-y" placeholder="Y values (comma separated)" value="1,4,9,16,25" style="margin-top:0.6rem">
    </div>
    <div id="pd-pie" style="display:none">
      <input class="tech-input" id="pd-labels" placeholder="Labels (comma separated)" value="A,B,C" style="margin-top:0.6rem">
      <input class="tech-input" id="pd-values" placeholder="Values (comma separated)" value="30,50,20" style="margin-top:0.6rem">
    </div>
    <input class="tech-input" id="pd-title" placeholder="Chart title (optional)" style="margin-top:0.6rem">
    <button style="margin-top:0.6rem" onclick="plotDataRun()">Generate Plot</button>
    <div style="margin-top:1rem">
      ${imgSrc ? `<img src="${imgSrc}" style="max-width:100%;background:#fff;border-radius:0.5rem;padding:0.5rem">` : ''}
      ${err ? `<p style="color:var(--accent)">${err}</p>` : ''}
    </div>
  `);
}
function plotDataToggleFields() {
  const isPie = document.getElementById('pd-kind').value === 'pie';
  document.getElementById('pd-xy').style.display = isPie ? 'none' : 'block';
  document.getElementById('pd-pie').style.display = isPie ? 'block' : 'none';
}
async function plotDataRun() {
  const kind = document.getElementById('pd-kind').value;
  const body = { kind, title: document.getElementById('pd-title').value };
  if (kind === 'pie') {
    body.labels = document.getElementById('pd-labels').value;
    body.values = document.getElementById('pd-values').value;
  } else {
    body.x = document.getElementById('pd-x').value;
    body.y = document.getElementById('pd-y').value;
  }
  const data = await api('/api/games/plot', 'POST', body);
  renderPlotData(data.image, data.error);
}

// -- Socket chat: a real TCP chat room, not a demo --
let socketChatPollTimer = null;
let socketChatSince = 0;
let socketChatInfo = null; // {port, lan_ip}

function stopSocketChatPolling() {
  if (socketChatPollTimer) { clearInterval(socketChatPollTimer); socketChatPollTimer = null; }
}

function socketChatStart() { renderSocketChatIdle(); }

function renderSocketChatIdle(err) {
  showGamePanel(`
    <h3>📡 Socket Chat</h3>
    <p>Starts a real TCP chat server on this device. Anyone on the same network can join with
    <code>nc &lt;device-ip&gt; 12345</code> and chat live with this page.</p>
    <button onclick="socketChatDoStart()">Start Server</button>
    ${err ? `<p style="margin-top:0.8rem;color:var(--accent)">${err}</p>` : ''}
  `);
}

async function socketChatDoStart() {
  const data = await api('/api/games/socket_chat/start', 'POST', {});
  if (data.error) { renderSocketChatIdle(data.error); return; }
  socketChatInfo = { port: data.port, lan_ip: data.lan_ip };
  socketChatSince = 0;
  renderSocketChatRoom([], 0);
  await socketChatPoll();
  stopSocketChatPolling();
  socketChatPollTimer = setInterval(socketChatPoll, 1500);
}

function renderSocketChatRoom(messages, clientCount) {
  const panel = document.getElementById('game-panel');
  // Preserve panel if it already contains the room (avoid wiping the log/input on every poll)
  if (!document.getElementById('sc-log')) {
    showGamePanel(`
      <h3>📡 Socket Chat -- live</h3>
      <p style="color:#9a9aa5;font-size:0.85rem">Connect from another device: <code>nc ${socketChatInfo.lan_ip} ${socketChatInfo.port}</code>
        &nbsp;|&nbsp; <span id="sc-clients">${clientCount}</span> connected</p>
      <div id="sc-log" style="height:240px;overflow-y:auto;background:#0a0a10;border:1px solid var(--border);border-radius:0.5rem;padding:0.7rem;margin:0.6rem 0;font-family:monospace;font-size:0.85rem"></div>
      <div style="display:flex;gap:0.5rem">
        <input class="tech-input" id="sc-input" placeholder="Type a message..." style="flex:1" onkeydown="if(event.key==='Enter')socketChatSend()">
        <button onclick="socketChatSend()">Send</button>
      </div>
      <button style="margin-top:0.6rem" onclick="socketChatDoStop()">Stop Server</button>
    `);
  }
  appendSocketChatMessages(messages);
  const cc = document.getElementById('sc-clients');
  if (cc) cc.textContent = clientCount;
}

function appendSocketChatMessages(messages) {
  const log = document.getElementById('sc-log');
  if (!log) return;
  for (const m of messages) {
    const line = document.createElement('div');
    line.textContent = m.from === 'system' ? `*** ${m.text} ***` : `${m.from}: ${m.text}`;
    if (m.from === 'system') line.style.color = '#9a9aa5';
    else if (m.from === 'web') line.style.color = 'var(--primary)';
    log.appendChild(line);
  }
  if (messages.length) log.scrollTop = log.scrollHeight;
}

async function socketChatPoll() {
  const data = await api(`/api/games/socket_chat/poll?since=${socketChatSince}`, 'GET');
  if (!data.running) { stopSocketChatPolling(); return; }
  socketChatSince = data.next;
  renderSocketChatRoom(data.messages, data.client_count);
}

async function socketChatSend() {
  const input = document.getElementById('sc-input');
  const text = input.value.trim();
  if (!text) return;
  input.value = '';
  await api('/api/games/socket_chat/send', 'POST', {text});
  await socketChatPoll();
}

async function socketChatDoStop() {
  stopSocketChatPolling();
  await api('/api/games/socket_chat/stop', 'POST', {});
  renderSocketChatIdle();
}

// ── Download ──
async function startDownload() {
  const url = document.getElementById('dl-url').value;
  const type = document.getElementById('dl-type').value;
  const data = await api('/api/download', 'POST', {url, type:parseInt(type)});
  document.getElementById('dl-status').innerText = data.message || 'Done';
}

// ── Generate ──
async function generateImage() {
  const prompt = document.getElementById('img-prompt').value;
  const data = await api('/api/gen_image', 'POST', {prompt});
  if (data.image) {
    document.getElementById('img-result').innerHTML = `<img src="${data.image}" style="max-width:100%">`;
  } else {
    document.getElementById('img-result').innerText = data.error || 'Error';
  }
}
async function generatePDF() {
  const text = document.getElementById('pdf-text').value;
  const resp = await fetch('/api/gen_pdf', {
    method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({text})
  });
  if (resp.ok) {
    const blob = await resp.blob();
    const url = window.URL.createObjectURL(blob);
    const a = document.createElement('a'); a.href = url; a.download = 'output.pdf'; a.click();
  }
}
async function generateTXT() {
  const input = document.getElementById('txt-input').value;
  const mode = document.getElementById('txt-mode').value;
  const data = await api('/api/gen_txt', 'POST', {text:input, mode});
  document.getElementById('txt-result').innerText = data.text || data.error;
}

// ── Math ──
async function solveMath() {
  const expr = document.getElementById('math-expr').value;
  const mode = document.getElementById('math-mode').value;
  const data = await api('/api/math', 'POST', {expression:expr, mode});
  document.getElementById('math-result').innerText = data.result;
}

// ── News ──
async function fetchNews() {
  const data = await api('/api/news', 'GET');
  document.getElementById('news-result').innerHTML = data.summary || 'No news';
}

// ── Code ──
async function assistCode() {
  const code = document.getElementById('code-input').value;
  const action = document.getElementById('code-action').value;
  const data = await api('/api/code', 'POST', {code, action});
  document.getElementById('code-result').innerText = data.result;
}

// ── Language ──
async function startLesson() {
  const lang = document.getElementById('lang-name').value;
  const level = document.getElementById('lang-level').value;
  const topic = document.getElementById('lang-topic').value;
  const data = await api('/api/language_lesson', 'POST', {lang, level, topic});
  document.getElementById('lesson-result').innerText = data.lesson;
}
async function loadFlashcards() {
  const lang = document.getElementById('flash-lang').value;
  const data = await api('/api/flashcards', 'POST', {lang});
  if (data.cards) {
    flashcardData = data.cards;
    flashcardIndex = 0;
    showFlashcard();
  }
}
function showFlashcard() {
  if (flashcardIndex >= flashcardData.length) {
    document.getElementById('flashcard-area').innerHTML = 'Done!';
    return;
  }
  const card = flashcardData[flashcardIndex];
  document.getElementById('flashcard-area').innerHTML = `
    <strong>Front:</strong> ${card.front}<br>
    <button onclick="flipFlashcard(this)">Show Back</button>
    <span style="display:none" class="back">${card.back}</span>`;
}
function flipFlashcard(btn) {
  const back = btn.nextElementSibling;
  back.style.display = 'inline';
  btn.remove();
}

// ── File Browser ──
async function loadFileBrowser(path) {
  const data = await api('/api/file_browser', 'POST', {path});
  let html = `<p><a href="#" onclick="loadFileBrowser('/')">Root</a></p><ul>`;
  data.entries.forEach(e => {
    html += `<li><a href="#" onclick="loadFileBrowser('${e.path}')">${e.name}</a></li>`;
  });
  html += '</ul>';
  document.getElementById('file-list').innerHTML = html;
}

// ── Tasks ──
async function refreshTasks() {
  const data = await api('/api/tasks', 'GET');
  const list = document.getElementById('task-list');
  list.innerHTML = data.tasks.map((t, idx) => `
    <div class="card">
      <input class="tech-input"  type="checkbox" ${t.completed?'checked':''} onchange="toggleTask(${idx})">
      ${t.desc} <small>${t.due||''}</small>
    </div>`).join('');
}
async function addTask() {
  const desc = document.getElementById('task-desc').value;
  await api('/api/add_task', 'POST', {desc});
  refreshTasks();
}

// ── Settings ──
async function loadSettings() {
  const resp = await api('/api/get_config', 'GET');
  if (resp.api_key) document.getElementById('api-key-input').value = resp.api_key;
  document.getElementById('set-model').value = resp.model || '';
  document.getElementById('set-user-name').value = resp.user_name || '';
  document.getElementById('set-tts-voice').value = resp.tts_voice || '';
  document.getElementById('set-personality').value = resp.personality || '';
  document.getElementById('set-theme-dark').checked = resp.theme === 'dark';
  document.getElementById('set-file-watcher').checked = !!resp.file_watcher_enabled;
  document.getElementById('set-git-commit').checked = !!resp.auto_git_commit;
  document.getElementById('set-offline').checked = !!resp.offline_enabled;
  document.getElementById('set-learning').checked = !!resp.learning_enabled;
}
async function changePassword() {
  const pwd = document.getElementById('new-password').value;
  await api('/api/change_password', 'POST', {password:pwd});
  alert('Password updated!');
}
async function saveApiKey() {
  const key = document.getElementById('api-key-input').value;
  await api('/api/update_api_key', 'POST', {api_key:key});
  alert('API key saved!');
}
async function saveSettings() {
  await api('/api/update_settings', 'POST', {
    model: document.getElementById('set-model').value,
    user_name: document.getElementById('set-user-name').value,
    tts_voice: document.getElementById('set-tts-voice').value,
    personality: document.getElementById('set-personality').value,
    theme: document.getElementById('set-theme-dark').checked ? 'dark' : 'light',
    file_watcher_enabled: document.getElementById('set-file-watcher').checked,
    auto_git_commit: document.getElementById('set-git-commit').checked,
    offline_enabled: document.getElementById('set-offline').checked,
    learning_enabled: document.getElementById('set-learning').checked,
  });
  alert('Settings saved!');
}

// ── ASCII Art ──
function asciiShowResult(html, text) {
  const wrap = document.getElementById('ascii-result-wrap');
  document.getElementById('ascii-result').innerHTML = html;
  wrap.style.display = 'block';
  wrap.dataset.asciiText = text || '';
  wrap.scrollIntoView({behavior:'smooth', block:'nearest'});
}
function asciiSyncCameraFacing() {
  // The `capture` attribute tells a mobile browser which physical camera to
  // open by default when the file input is tapped -- "environment" = back,
  // "user" = front. Desktop browsers ignore it and just show a file picker.
  const facing = document.getElementById('ascii-cam-choice').value;
  document.getElementById('ascii-cam-file').setAttribute('capture', facing);
}
async function _asciiUploadAndConvert(fileInput, widthId, modeId, hqId, statusEl) {
  if (!fileInput.files.length) { statusEl.textContent = 'Choose a photo first.'; return; }
  statusEl.textContent = 'Converting...';
  const fd = new FormData();
  fd.append('photo', fileInput.files[0]);
  fd.append('width', document.getElementById(widthId).value || 120);
  fd.append('mode', document.getElementById(modeId).value);
  fd.append('high_accuracy', document.getElementById(hqId).checked);
  const resp = await fetch('/api/ascii/upload', {method:'POST', body:fd});
  const data = await resp.json();
  if (data.error) { statusEl.textContent = data.error; return; }
  statusEl.textContent = '';
  asciiShowResult(data.html, data.text);
}
async function asciiFromCamera() {
  await _asciiUploadAndConvert(
    document.getElementById('ascii-cam-file'), 'ascii-cam-width', 'ascii-cam-mode', 'ascii-cam-hq',
    document.getElementById('ascii-cam-status'),
  );
}
async function asciiFromUpload() {
  await _asciiUploadAndConvert(
    document.getElementById('ascii-upload-file'), 'ascii-upload-width', 'ascii-upload-mode', 'ascii-upload-hq',
    document.getElementById('ascii-upload-status'),
  );
}
async function asciiFromText() {
  const desc = document.getElementById('ascii-ai-desc').value.trim();
  if (!desc) return;
  const data = await api('/api/ascii/text', 'POST', {description: desc});
  if (data.error) { alert(data.error); return; }
  const escaped = data.text.replace(/</g,'&lt;').replace(/>/g,'&gt;');
  asciiShowResult(`<pre style="font-family:'Fira Code',monospace;background:#000;color:#0f0;padding:1rem;border-radius:0.6rem;overflow-x:auto;white-space:pre-wrap">${escaped}</pre>`, data.text);
}
function asciiDownload() {
  const wrap = document.getElementById('ascii-result-wrap');
  const text = wrap.dataset.asciiText || '';
  if (!text) return;
  const blob = new Blob([text], {type:'text/plain'});
  const url = URL.createObjectURL(blob);
  const a = document.createElement('a'); a.href = url; a.download = 'ascii_art.txt'; a.click();
  URL.revokeObjectURL(url);
}

// ── Music Player ──
async function musicListFiles() {
  const dir = document.getElementById('music-dir').value.trim();
  const data = await api('/api/music/list', 'POST', {dir: dir || undefined});
  const box = document.getElementById('music-file-list');
  if (data.error) { box.innerHTML = `<p style="color:var(--accent)">${data.error}</p>`; return; }
  if (!data.files.length) { box.innerHTML = '<p>No audio files found.</p>'; return; }
  box.innerHTML = `<p style="color:#9a9aa5;font-size:0.85rem">${data.dir}</p>` + data.files.map(f => `
    <div class="card" style="display:flex;justify-content:space-between;align-items:center;padding:0.6rem 1rem">
      <span style="word-break:break-all">${f.split('/').pop()}</span>
      <button onclick='musicPlay(${JSON.stringify(f)})'>▶ Play</button>
    </div>`).join('');
}
async function musicPlay(path) {
  const data = await api('/api/music/play', 'POST', {path});
  document.getElementById('music-status').textContent = data.message || data.error || '';
}
async function musicPlayUrl() {
  const url = document.getElementById('music-url').value.trim();
  if (!url) return;
  const data = await api('/api/music/play', 'POST', {url});
  document.getElementById('music-status').textContent = data.message || data.error || '';
}

// ── Interactive Fiction ──
function fictionAppend(role, text) {
  const log = document.getElementById('fiction-log');
  const div = document.createElement('div');
  div.className = 'card';
  div.innerHTML = role === 'dm' ? `<b>DM:</b> ${text}` : `<b>You:</b> ${text}`;
  log.appendChild(div);
  log.scrollIntoView({behavior:'smooth', block:'end'});
}
async function fictionStart() {
  document.getElementById('fiction-log').innerHTML = '';
  fictionAppend('dm', 'The adventure begins...');
  const data = await api('/api/fiction/start', 'POST', {});
  document.getElementById('fiction-log').lastChild.innerHTML = `<b>DM:</b> ${data.scene || data.error}`;
  document.getElementById('fiction-input-area').style.display = 'flex';
}
async function fictionAction() {
  const input = document.getElementById('fiction-action');
  const action = input.value.trim();
  if (!action) return;
  fictionAppend('you', action);
  input.value = '';
  const data = await api('/api/fiction/action', 'POST', {action});
  if (data.error) { fictionAppend('dm', data.error); return; }
  fictionAppend('dm', data.scene);
}

// ── AI File Organiser ──
async function organisePreview() {
  const box = document.getElementById('organise-result');
  box.innerHTML = 'Thinking...';
  const data = await api('/api/organise/preview', 'POST', {});
  if (data.error) { box.innerHTML = `<p style="color:var(--accent)">${data.error}</p>`; return; }
  if (data.message) { box.innerHTML = `<p>${data.message}</p>`; return; }
  window._organisePlan = data.plan;
  box.innerHTML = Object.entries(data.plan).map(([folder, files]) => `
    <div class="card"><h3>📁 ${folder}</h3><p>${files.join(', ')}</p></div>
  `).join('') + '<button onclick="organiseApply()">Apply This Plan</button>';
}
async function organiseApply() {
  const data = await api('/api/organise/apply', 'POST', {plan: window._organisePlan});
  document.getElementById('organise-result').innerHTML = data.success
    ? `<p>Moved ${data.moved} file(s).</p>` : `<p style="color:var(--accent)">${data.error}</p>`;
}

// ── Dependency Scanner ──
async function scanDeps() {
  const box = document.getElementById('deps-result');
  box.innerHTML = 'Scanning...';
  const data = await api('/api/scan_dependencies', 'GET');
  if (data.error) { box.innerHTML = `<p style="color:var(--accent)">${data.error}</p>`; return; }
  box.innerHTML = `<table style="width:100%;border-collapse:collapse">` +
    data.packages.map(p => `<tr><td style="padding:0.3rem;border-bottom:1px solid var(--border)">${p.package}</td><td style="padding:0.3rem;border-bottom:1px solid var(--border);color:var(--success)">${p.version}</td></tr>`).join('') +
    `</table>`;
}

// ── View Logs ──
async function loadLogs() {
  const data = await api('/api/logs', 'GET');
  document.getElementById('logs-result').textContent = data.logs || 'No logs yet.';
}

// Transform code blocks from plain text to beautiful HTML
function formatAIMessage(element) {
  const rawText = element.innerText;
  const formatted = rawText.replace(/```(\w*)\n?([\s\S]*?)```/g, function(match, lang, code) {
    return '<pre><code>' + code.replace(/</g, '&lt;').replace(/>/g, '&gt;') + '</code></pre>';
  });
  const finalText = formatted.replace(/`([^`]+)`/g, '<code>$1</code>');
  element.innerHTML = finalText;
}

function clearChat() {
  document.getElementById('chat-log').innerHTML = '';
  api('/api/chat/clear', 'POST', {});
}

// Typewriter effect
function typeWriter(element, text, speed = 30, callback = null) {
  element.innerHTML = '';
  let i = 0;
  function type() {
    if (i < text.length) {
      element.innerHTML += text.charAt(i);
      i++;
      setTimeout(type, speed);
    } else if (callback) {
      callback();
    }
  }
  type();
}

async function toggleTask(taskId) {
  await api('/api/toggle_task', 'POST', {task_id: taskId});
  refreshTasks();
}

</script>
</body>
</html>'''

# ── Routes ──
@app.route('/')
def index():
    return render_template_string(HTML)

@app.route('/api/verify_password', methods=['POST'])
def verify_pwd():
    if request.json.get('password') == get_password():
        session['auth'] = True
        return jsonify(success=True)
    return jsonify(success=False)

@app.route('/api/change_password', methods=['POST'])
def change_pwd():
    if not session.get('auth'): return jsonify(error='Unauthorized'), 401
    set_password(request.json['password'])
    return jsonify(success=True)

@app.route('/api/get_config')
def get_config():
    if not session.get('auth'): return jsonify(error='Unauthorized'), 401
    return jsonify(
        api_key=assistant_config.get('groq_api_key', ''),
        model=assistant_config.get('model', ''),
        user_name=assistant_config.get('user_name', ''),
        tts_voice=assistant_config.get('tts_voice', ''),
        theme=assistant_config.get('theme', 'dark'),
        file_watcher_enabled=assistant_config.get('file_watcher_enabled', True),
        auto_git_commit=assistant_config.get('auto_git_commit', False),
        personality=assistant_config.get('personality', ''),
        offline_enabled=assistant_config.get('offline_enabled', False),
        learning_enabled=assistant_config.get('learning_enabled', True),
    )

@app.route('/api/update_settings', methods=['POST'])
def update_settings():
    if not session.get('auth'): return jsonify(error='Unauthorized'), 401
    data = request.json or {}
    allowed = ['model', 'user_name', 'tts_voice', 'theme', 'file_watcher_enabled',
               'auto_git_commit', 'personality', 'offline_enabled', 'learning_enabled']
    for key in allowed:
        if key in data:
            assistant_config[key] = data[key]
    save_config(assistant_config)
    return jsonify(success=True)

@app.route('/api/update_api_key', methods=['POST'])
def update_api_key():
    if not session.get('auth'): return jsonify(error='Unauthorized'), 401
    assistant_config['groq_api_key'] = request.json['api_key']
    save_config(assistant_config)
    return jsonify(success=True)

@app.route('/api/chat', methods=['POST'])
def chat():
    if not session.get('auth'): return jsonify(error='Unauthorized'), 401
    msg = request.json['message']
    key = ('chat', _get_sid())
    conversation = _conversations.get(key)
    if conversation is None:
        conversation = [{
            "role": "system",
            "content": assistant_config.get("personality", "You are a helpful assistant.")
                       + f" The user's name is {assistant_config.get('user_name', 'User')}."
        }]
    conversation.append({"role": "user", "content": msg})
    reply = capture_groq_stream(conversation)
    conversation.append({"role": "assistant", "content": reply})
    _conversations[key] = conversation
    log_activity("AI Chat (web)", f"Q: {msg[:50]}... A: {reply[:50]}...")
    return jsonify(reply=reply)

@app.route('/api/chat/clear', methods=['POST'])
def chat_clear():
    if not session.get('auth'): return jsonify(error='Unauthorized'), 401
    _conversations.pop(('chat', _get_sid()), None)
    return jsonify(success=True)

@app.route('/api/download', methods=['POST'])
def download():
    if not session.get('auth'): return jsonify(error='Unauthorized'), 401
    url = request.json['url']
    dtype = request.json['type']
    from bs4 import BeautifulSoup
    try:
        resp = req.get(url, timeout=10)
        soup = BeautifulSoup(resp.text, 'html.parser')
        # define file extensions per type
        ext_map = {
            2: ["jpg","jpeg","png","gif","bmp","svg","webp"],
            3: ["mp4","mkv","avi","mov","flv","wmv","webm","m4v"],
            4: ["mp3","wav","flac","m4a","ogg","aac","wma"],
            5: ["txt","pdf","doc","docx","md","csv","log","rtf","ppt","pptx","xls","xlsx"],
            6: ["jpg","jpeg","png","gif"],  # same as images but list
            7: ["mp4","mkv","avi"],
            8: ["mp3","wav","flac"],
            1: []  # mirror
        }
        links = []
        if dtype == 1:
            return jsonify(message="Site mirroring not supported via web. Use wget in terminal.")
        for tag in soup.find_all(['a','img','source','video','audio']):
            href = tag.get('href') or tag.get('src')
            if not href: continue
            if href.startswith('#') or href.startswith('javascript:'): continue
            full = req.compat.urljoin(url, href)
            ext = os.path.splitext(full.split('?')[0])[1].lower().lstrip('.')
            if dtype in [6,7,8]:  # list mode
                if ext in ext_map.get(dtype-4, []):
                    links.append(full)
            else:
                if ext in ext_map.get(dtype, []):
                    links.append(full)
        if not links:
            return jsonify(message="No matching files found.")
        # For types 6-8, return the list as text; for others return count
        if dtype in [6,7,8]:
            return jsonify(message=f"Found {len(links)} links.", urls=links[:50])
        return jsonify(message=f"Found {len(links)} files. Download not implemented yet.")
    except Exception as e:
        return jsonify(error=str(e))

@app.route('/api/gen_image', methods=['POST'])
def gen_image():
    if not session.get('auth'): return jsonify(error='Unauthorized'), 401
    prompt = request.json['prompt']
    img_url = f"https://image.pollinations.ai/prompt/{prompt}"
    # Return base64 or direct URL
    return jsonify(image=img_url)

@app.route('/api/gen_pdf', methods=['POST'])
def gen_pdf():
    if not session.get('auth'): return jsonify(error='Unauthorized'), 401
    text = request.json['text']
    from fpdf import FPDF
    pdf = FPDF()
    pdf.add_page()
    pdf.set_font("Helvetica", size=12)
    pdf.multi_cell(0, 10, text)
    pdf_output = io.BytesIO()
    pdf.output(pdf_output)
    pdf_output.seek(0)
    return send_file(pdf_output, mimetype='application/pdf', as_attachment=True, download_name='output.pdf')

@app.route('/api/gen_txt', methods=['POST'])
def gen_txt():
    if not session.get('auth'): return jsonify(error='Unauthorized'), 401
    user_text = request.json['text']
    mode = request.json['mode']
    if mode == 'enhance':
        prompt = f"Improve this text: {user_text}"
    else:
        prompt = f"Write a short text about: {user_text}"
    msg = [{"role":"user","content":prompt}]
    result = capture_groq_stream(msg)
    return jsonify(text=result)

@app.route('/api/math', methods=['POST'])
def math():
    if not session.get('auth'): return jsonify(error='Unauthorized'), 401
    expr = request.json['expression']
    mode = request.json['mode']
    if mode == 'steps':
        prompt = f"Solve step by step: {expr}"
    else:
        prompt = f"Solve, only final answer: {expr}"
    msg = [{"role":"user","content":prompt}]
    result = capture_groq_stream(msg)
    return jsonify(result=result)

@app.route('/api/news')
def news():
    if not session.get('auth'): return jsonify(error='Unauthorized'), 401
    # Simple news summary using groq with dummy headlines
    headlines = "• Breaking news sample 1\n• Sample 2"
    msg = [{"role":"user","content":f"Summarize these headlines: {headlines}"}]
    summary = capture_groq_stream(msg)
    return jsonify(summary=summary)

@app.route('/api/code', methods=['POST'])
def code():
    if not session.get('auth'): return jsonify(error='Unauthorized'), 401
    code_text = request.json['code']
    action = request.json['action']
    if action == 'explain':
        prompt = f"Explain this code: {code_text}"
    elif action == 'refactor':
        prompt = f"Refactor this code: {code_text}"
    else:
        prompt = f"Generate unit tests: {code_text}"
    msg = [{"role":"user","content":prompt}]
    result = capture_groq_stream(msg)
    return jsonify(result=result)

@app.route('/api/language_lesson', methods=['POST'])
def lang_lesson():
    if not session.get('auth'): return jsonify(error='Unauthorized'), 401
    lang = request.json['lang']
    level = request.json['level']
    topic = request.json['topic']
    prompt = f"Create a short {level} {lang} lesson about {topic}. Include 5 new words and a dialogue."
    msg = [{"role":"user","content":prompt}]
    lesson = capture_groq_stream(msg)
    return jsonify(lesson=lesson)

@app.route('/api/flashcards', methods=['POST'])
def flashcards():
    if not session.get('auth'): return jsonify(error='Unauthorized'), 401
    lang = request.json['lang']
    prompt = f"Generate 10 English-{lang} vocabulary flashcards in JSON: [{{\"front\":\"...\",\"back\":\"...\"}}]"
    msg = [{"role":"user","content":prompt}]
    raw = capture_groq(msg)
    try:
        cards = json.loads(re.search(r'\[.*\]', raw, re.DOTALL).group())
        return jsonify(cards=cards)
    except:
        return jsonify(error="Parsing failed")

@app.route('/api/qr', methods=['POST'])
def make_qr():
    if not session.get('auth'): return jsonify(error='Unauthorized'), 401
    text = request.json.get('text', '').strip()
    if not text:
        return jsonify(error="No text provided")
    import qrcode
    img = qrcode.make(text)
    buf = io.BytesIO()
    img.save(buf, format='PNG')
    b64 = base64.b64encode(buf.getvalue()).decode()
    return jsonify(image=f"data:image/png;base64,{b64}")

@app.route('/api/weather', methods=['POST'])
def weather():
    if not session.get('auth'): return jsonify(error='Unauthorized'), 401
    city = request.json.get('city', '').strip()[:50]
    if not city:
        return jsonify(error="No city provided")
    from urllib.parse import quote
    try:
        resp = req.get(f"https://wttr.in/{quote(city, safe='')}?format=3", timeout=10)
        return jsonify(result=resp.text.strip())
    except Exception as e:
        return jsonify(error=str(e))

FILE_BROWSER_ROOTS = [os.path.realpath(os.path.expanduser('~'))]
if os.path.exists(os.path.expanduser('~/storage')):
    FILE_BROWSER_ROOTS.append(os.path.realpath(os.path.expanduser('~/storage')))

def _file_browser_path_allowed(path):
    real = os.path.realpath(path)
    return any(real == root or real.startswith(root + os.sep) for root in FILE_BROWSER_ROOTS)

@app.route('/api/file_browser', methods=['POST'])
def file_browser():
    if not session.get('auth'): return jsonify(error='Unauthorized'), 401
    path = request.json.get('path', os.path.expanduser('~'))
    # Fallback to home directory if path is inaccessible or outside the allowed roots
    if not os.path.exists(path) or not _file_browser_path_allowed(path):
        path = os.path.expanduser('~')
    try:
        entries = []
        for item in os.listdir(path):
            full = os.path.join(path, item)
            if not _file_browser_path_allowed(full):
                continue
            is_dir = os.path.isdir(full)
            entries.append({'name': item, 'path': full, 'is_dir': is_dir})
        return jsonify(entries=sorted(entries, key=lambda e: (not e['is_dir'], e['name'].lower())))
    except PermissionError:
        # If we still can't read the path, return home directory instead
        if path != os.path.expanduser('~'):
            return file_browser()  # retry with home
        return jsonify(entries=[{'name': 'Permission denied', 'path': '', 'is_dir': False}])
    except Exception as e:
        return jsonify(entries=[{'name': f'Error: {str(e)}', 'path': '', 'is_dir': False}])


@app.route('/api/toggle_task', methods=['POST'])
def toggle_task():
    if not session.get('auth'): return jsonify(error='Unauthorized'), 401
    TASKS_FILE = os.path.expanduser("~/.etbytes_tasks.json")
    if not os.path.exists(TASKS_FILE):
        return jsonify(error="No tasks file")
    with open(TASKS_FILE) as f:
        tasks = json.load(f)
    idx = request.json.get('task_id', -1)
    if 0 <= idx < len(tasks):
        tasks[idx]['completed'] = not tasks[idx].get('completed', False)
        with open(TASKS_FILE, 'w') as f:
            json.dump(tasks, f)
        return jsonify(success=True)
    return jsonify(error="Invalid task ID"), 400

@app.route('/api/tasks')
def get_tasks():
    if not session.get('auth'): return jsonify(error='Unauthorized'), 401
    TASKS_FILE = os.path.expanduser("~/.etbytes_tasks.json")
    if os.path.exists(TASKS_FILE):
        with open(TASKS_FILE) as f:
            tasks = json.load(f)
    else:
        tasks = []
    return jsonify(tasks=tasks)

@app.route('/api/add_task', methods=['POST'])
def add_task():
    if not session.get('auth'): return jsonify(error='Unauthorized'), 401
    desc = request.json['desc']
    TASKS_FILE = os.path.expanduser("~/.etbytes_tasks.json")
    tasks = []
    if os.path.exists(TASKS_FILE):
        with open(TASKS_FILE) as f:
            tasks = json.load(f)
    tasks.append({'desc':desc, 'due':None, 'completed':False, 'created':str(datetime.now())})
    with open(TASKS_FILE, 'w') as f:
        json.dump(tasks, f)
    return jsonify(success=True)


# ── ASCII Art ──
@app.route('/api/ascii/upload', methods=['POST'])
def ascii_upload():
    if not session.get('auth'): return jsonify(error='Unauthorized'), 401
    try:
        from PIL import Image, ImageOps
    except ImportError:
        return jsonify(error="Pillow (PIL) is not installed on the server. Run: pip install Pillow")

    if 'photo' not in request.files or request.files['photo'].filename == '':
        return jsonify(error="No file uploaded")
    file = request.files['photo']

    try:
        width = max(10, min(300, int(request.form.get('width', 120))))
    except (TypeError, ValueError):
        width = 120
    mode = request.form.get('mode', 'color')
    if mode not in ('color', 'grayscale'):
        mode = 'color'
    dither = request.form.get('high_accuracy', 'true').lower() != 'false'

    try:
        img = Image.open(file.stream)
        img = ImageOps.exif_transpose(img)
        rgb_img_full = img.convert("RGB")
        ascii_str, html = _ascii_render_html(rgb_img_full, width, mode, dither)
    except Exception as e:
        return jsonify(error=f"Could not process image: {e}")

    log_activity("ASCII Art (web)", f"Uploaded file ({mode}, high_accuracy={dither})")
    return jsonify(html=html, text=ascii_str)

@app.route('/api/ascii/text', methods=['POST'])
def ascii_text():
    if not session.get('auth'): return jsonify(error='Unauthorized'), 401
    desc = (request.json or {}).get('description', '').strip()
    if not desc:
        return jsonify(error="No description provided")
    prompt = f"Generate only the ASCII art for: {desc}. Do not explain, just output the art."
    art = capture_groq_stream([{"role": "user", "content": prompt}])
    log_activity("ASCII Art (web)", f"AI text art: {desc[:50]}")
    return jsonify(text=art)

# ── Music Player ──
@app.route('/api/music/list', methods=['POST'])
def music_list():
    if not session.get('auth'): return jsonify(error='Unauthorized'), 401
    directory = (request.json or {}).get('dir') or os.path.expanduser('~/storage/music')
    if not os.path.isdir(directory):
        directory = os.path.expanduser('~/storage/shared')
    if not os.path.isdir(directory):
        return jsonify(error=f"Directory not found: {directory}", files=[])
    audio_exts = (".mp3", ".wav", ".flac", ".m4a", ".ogg", ".aac", ".wma", ".opus")
    files = []
    try:
        for root, dirs, filenames in os.walk(directory):
            for f in filenames:
                if f.lower().endswith(audio_exts):
                    files.append(os.path.join(root, f))
    except PermissionError:
        return jsonify(error=f"Permission denied: {directory}", files=[])
    return jsonify(files=files[:200], dir=directory)

@app.route('/api/music/play', methods=['POST'])
def music_play():
    if not session.get('auth'): return jsonify(error='Unauthorized'), 401
    data = request.json or {}
    path, url = data.get('path'), data.get('url')
    target = path or url
    if not target:
        return jsonify(error="Nothing to play")
    try:
        subprocess.Popen(["mpv", target])
    except FileNotFoundError:
        return jsonify(error="mpv not found. Install with: pkg install mpv")
    return jsonify(success=True, message=f"Now playing on this device: {os.path.basename(target) if path else target}")

# ── Dependency Scanner ──
@app.route('/api/scan_dependencies')
def scan_deps():
    if not session.get('auth'): return jsonify(error='Unauthorized'), 401
    try:
        result = subprocess.run(["pip", "freeze"], capture_output=True, text=True)
        packages = []
        for line in result.stdout.strip().split("\n"):
            if "==" in line:
                pkg, ver = line.split("==", 1)
                packages.append({"package": pkg, "version": ver})
        return jsonify(packages=packages[:200])
    except Exception as e:
        return jsonify(error=str(e))

# ── AI File Organiser ──
@app.route('/api/organise/preview', methods=['POST'])
def organise_preview():
    if not session.get('auth'): return jsonify(error='Unauthorized'), 401
    downloads_dir = os.path.expanduser("~/storage/downloads")
    files = []
    for root, dirs, filenames in os.walk(downloads_dir):
        for f in filenames:
            files.append(os.path.join(root, f))
    if not files:
        return jsonify(message="No files to organise.")
    file_list = "\n".join([f"- {os.path.basename(f)} ({os.path.splitext(f)[1]})" for f in files[:200]])
    prompt = f"""I have the following files in my Downloads folder. Suggest a logical folder structure to organise them. Only propose folders and which file goes where. Return ONLY a JSON object mapping target folder -> list of filenames (exact base names as listed). Do not include paths.

Files:
{file_list}

Output JSON only."""
    resp = capture_groq([{"role": "user", "content": prompt}])
    json_match = re.search(r'\{.*\}', resp, re.DOTALL)
    if not json_match:
        return jsonify(error="AI response did not contain valid JSON.")
    try:
        plan = json.loads(json_match.group())
    except Exception as e:
        return jsonify(error=f"Could not parse AI plan: {e}")
    return jsonify(plan=plan)

@app.route('/api/organise/apply', methods=['POST'])
def organise_apply():
    if not session.get('auth'): return jsonify(error='Unauthorized'), 401
    plan = (request.json or {}).get('plan')
    downloads_dir = os.path.expanduser("~/storage/downloads")
    if not plan:
        return jsonify(error="No plan provided")
    moved = 0
    for folder, flist in plan.items():
        target_dir = os.path.join(downloads_dir, folder)
        os.makedirs(target_dir, exist_ok=True)
        for fname in flist:
            src = os.path.join(downloads_dir, fname)
            if os.path.exists(src):
                shutil.move(src, os.path.join(target_dir, fname))
                moved += 1
    log_activity("AI Organisation (web)", f"Applied plan, moved {moved} files")
    return jsonify(success=True, moved=moved)

# ── View Logs ──
@app.route('/api/logs')
def get_logs():
    if not session.get('auth'): return jsonify(error='Unauthorized'), 401
    log_file = os.path.expanduser("~/.etbytes_log.txt")
    if not os.path.exists(log_file):
        return jsonify(logs="No logs yet.")
    with open(log_file, "r", encoding="utf-8") as f:
        lines = f.readlines()
    return jsonify(logs="".join(lines[-300:]))

# ── Interactive Fiction ──
@app.route('/api/fiction/start', methods=['POST'])
def fiction_start():
    if not session.get('auth'): return jsonify(error='Unauthorized'), 401
    conversation = [{
        "role": "system",
        "content": "You are a creative dungeon master. Lead an immersive text adventure. "
                   "Keep responses concise and vivid. Describe the scene, react to the "
                   "player's actions, and advance the story."
    }]
    start_prompt = "The adventure begins. Describe the starting location and the immediate situation. Make it engaging."
    conversation.append({"role": "user", "content": start_prompt})
    scene = capture_groq_stream(conversation)
    conversation.append({"role": "assistant", "content": scene})
    _conversations[('fiction', _get_sid())] = conversation
    return jsonify(scene=scene)

@app.route('/api/fiction/action', methods=['POST'])
def fiction_action():
    if not session.get('auth'): return jsonify(error='Unauthorized'), 401
    key = ('fiction', _get_sid())
    conversation = _conversations.get(key)
    if not conversation:
        return jsonify(error="No active adventure. Start a new one first.")
    action = (request.json or {}).get('action', '').strip()
    if not action:
        return jsonify(error="No action provided")
    if action.lower() == 'look':
        last_scene = conversation[-1]['content'] if conversation[-1]['role'] == 'assistant' else ''
        prompt = f"Current scene: {last_scene}\nBased on the scene above, describe your surroundings again in detail."
    else:
        prompt = action
    conversation.append({"role": "user", "content": prompt})
    scene = capture_groq_stream(conversation)
    conversation.append({"role": "assistant", "content": scene})
    _conversations[key] = conversation
    return jsonify(scene=scene)

@app.route('/api/fiction/reset', methods=['POST'])
def fiction_reset():
    if not session.get('auth'): return jsonify(error='Unauthorized'), 401
    _conversations.pop(('fiction', _get_sid()), None)
    return jsonify(success=True)

# ── Games that need server-side work ──
@app.route('/api/games/quiz_questions', methods=['POST'])
def quiz_questions():
    if not session.get('auth'): return jsonify(error='Unauthorized'), 401
    kind = (request.json or {}).get('kind', 'general')
    if kind not in ('general', 'tech'):
        kind = 'general'
    return jsonify(questions=ai_generate_quiz(kind, 5))

@app.route('/api/games/ttt_move', methods=['POST'])
def ttt_move():
    if not session.get('auth'): return jsonify(error='Unauthorized'), 401
    board = (request.json or {}).get('board') or ['']*9
    return jsonify(move=ai_ttt_move(board))

@app.route('/api/games/rps_move', methods=['POST'])
def rps_move():
    if not session.get('auth'): return jsonify(error='Unauthorized'), 401
    user_choice = (request.json or {}).get('user', '')
    sid = _get_sid()
    history = _rps_history.setdefault(sid, [])
    move = ai_rps_move(history)
    if user_choice in ('rock', 'paper', 'scissors'):
        history.append(user_choice)
        del history[:-10]
    return jsonify(move=move)

@app.route('/api/games/web_scraper', methods=['POST'])
def web_scraper():
    if not session.get('auth'): return jsonify(error='Unauthorized'), 401
    url = (request.json or {}).get('url', '').strip()
    if not url:
        return jsonify(error="No URL provided")
    try:
        from bs4 import BeautifulSoup
        r = req.get(url, timeout=10)
        soup = BeautifulSoup(r.text, 'html.parser')

        title = soup.title.string.strip() if soup.title and soup.title.string else "(none)"
        desc_tag = soup.find("meta", attrs={"name": "description"})
        description = desc_tag["content"].strip() if desc_tag and desc_tag.get("content") else "(none)"
        word_count = len(soup.get_text(separator=" ").split())

        links = []
        for a in soup.find_all("a", href=True):
            href = a["href"]
            if href.startswith("#") or href.startswith("javascript:") or href.startswith("mailto:"):
                continue
            links.append(req.compat.urljoin(url, href))
        links = sorted(set(links))

        images = []
        for img in soup.find_all("img", src=True):
            images.append(req.compat.urljoin(url, img["src"]))
        images = sorted(set(images))

        return jsonify(
            title=title, description=description, word_count=word_count,
            links=links[:100], link_count=len(links),
            images=images[:50], image_count=len(images),
        )
    except Exception as e:
        return jsonify(error=str(e))

@app.route('/api/games/socket_chat/start', methods=['POST'])
def socket_chat_start():
    if not session.get('auth'): return jsonify(error='Unauthorized'), 401
    if not _chat_server.running:
        try:
            _chat_server.start()
        except OSError as e:
            return jsonify(error=f"Could not start server on port {_chat_server.port}: {e}")
    return jsonify(success=True, port=_chat_server.port, lan_ip=get_lan_ip())

@app.route('/api/games/socket_chat/send', methods=['POST'])
def socket_chat_send():
    if not session.get('auth'): return jsonify(error='Unauthorized'), 401
    if not _chat_server.running:
        return jsonify(error="Server not running. Start it first.")
    text = (request.json or {}).get('text', '').strip()
    if not text:
        return jsonify(error="Empty message")
    _chat_server.send_message("web", text)
    return jsonify(success=True)

@app.route('/api/games/socket_chat/poll')
def socket_chat_poll():
    if not session.get('auth'): return jsonify(error='Unauthorized'), 401
    try:
        since = int(request.args.get('since', 0))
    except ValueError:
        since = 0
    messages, next_since = _chat_server.get_messages(since)
    return jsonify(
        messages=messages, next=next_since,
        running=_chat_server.running,
        client_count=_chat_server.client_count() if _chat_server.running else 0,
    )

@app.route('/api/games/socket_chat/stop', methods=['POST'])
def socket_chat_stop():
    if not session.get('auth'): return jsonify(error='Unauthorized'), 401
    _chat_server.stop()
    return jsonify(success=True)

@app.route('/api/games/plot', methods=['POST'])
def plot_data():
    if not session.get('auth'): return jsonify(error='Unauthorized'), 401
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
    except ImportError:
        return jsonify(error="matplotlib not installed")

    data = request.json or {}
    kind = data.get('kind', 'line')
    title = (data.get('title') or '').strip()

    fig, ax = plt.subplots()
    try:
        if kind == 'pie':
            labels = [s.strip() for s in data.get('labels', '').split(',') if s.strip()]
            values = [float(v.strip()) for v in data.get('values', '').split(',') if v.strip()]
            if not labels or not values or len(labels) != len(values):
                plt.close(fig)
                return jsonify(error="Provide matching, non-empty labels and values.")
            ax.pie(values, labels=labels, autopct="%1.1f%%")
        else:
            x = [float(v.strip()) for v in data.get('x', '').split(',') if v.strip()]
            y = [float(v.strip()) for v in data.get('y', '').split(',') if v.strip()]
            if not x or not y or len(x) != len(y):
                plt.close(fig)
                return jsonify(error="X and Y must be non-empty and the same length.")
            if kind == 'bar':
                ax.bar(x, y)
            elif kind == 'scatter':
                ax.scatter(x, y)
            else:
                ax.plot(x, y, marker="o")
    except ValueError:
        plt.close(fig)
        return jsonify(error="Values must be numbers.")

    if title:
        ax.set_title(title)
    buf = io.BytesIO()
    fig.savefig(buf, format='png')
    plt.close(fig)
    buf.seek(0)
    b64 = base64.b64encode(buf.getvalue()).decode()
    log_activity("Plot (web)", f"{kind} chart")
    return jsonify(image=f"data:image/png;base64,{b64}")

@app.route('/api/games/design_pattern', methods=['POST'])
def design_pattern():
    if not session.get('auth'): return jsonify(error='Unauthorized'), 401
    data = request.json or {}
    pattern = (data.get('pattern') or 'Singleton').strip()
    language = (data.get('language') or 'Python').strip()
    prompt = (
        f"Explain the {pattern} design pattern concisely (2-3 sentences: what problem it solves "
        f"and when to use it), then give a complete, runnable {language} code example demonstrating it."
    )
    result = capture_groq_stream([{"role": "user", "content": prompt}])
    log_activity("Design Patterns (web)", f"{pattern} in {language}")
    return jsonify(result=result)

@app.route('/favicon.ico')
def favicon():
    return '', 204

if __name__ == '__main__':
    init_offline()
    get_password()  # bootstrap and print a generated password on first run
    # Debug mode exposes the Werkzeug interactive debugger, which allows arbitrary code
    # execution to anyone who can trigger an unhandled exception. Since this app binds to
    # 0.0.0.0 (so it's reachable from other devices on the LAN), keep it off unless a dev
    # explicitly opts in.
    debug_mode = os.environ.get('ETBYTES_DEBUG', '').lower() in ('1', 'true', 'yes')
    app.run(host='0.0.0.0', port=5000, debug=debug_mode)
