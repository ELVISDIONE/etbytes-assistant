# 🤖 E.TBYTES Assistant

**A full-featured AI assistant that lives in your terminal — built for [Termux](https://termux.dev/), powered by [Groq](https://groq.com/).**

Chat, play games, browse files, generate images/PDFs/QR codes, solve math, get news briefings, learn languages, run an AI Dungeon Master, and more — all from one CLI menu. Flip a switch and the same assistant is also a full web dashboard you can open from your phone's browser or any device on your LAN.

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![Python 3.9+](https://img.shields.io/badge/python-3.9%2B-blue.svg)](https://www.python.org/)
[![Made for Termux](https://img.shields.io/badge/made%20for-Termux-3DDC84.svg)](https://termux.dev/)

---

## ✨ Features

| | |
|---|---|
| 💬 **AI Chat** | Streaming chat via Groq's LLMs, with an offline learning mode that works without internet |
| 🎮 **Games & Learning** | Built-in games plus flashcard-style learning decks |
| 📁 **File Browser & 🗂️ AI Organiser** | Browse your device storage; let the AI sort your files |
| 📥 **Downloader** & 🎵 **Music Player** | Grab files and play local audio, right from the CLI |
| 🖼️ **Image / 📄 PDF / 📝 TXT / 🎨 ASCII Art** | Generate images, documents, and text art on demand |
| 🧮 **Math Solver** | Ask it anything from arithmetic to algebra |
| 📰 **News Briefing** | RSS-powered summaries of the day's headlines |
| 💻 **Code Assistant** | Explain, write, and debug code without leaving the terminal |
| 🌍 **Language Learning** | Practice a new language with an AI tutor |
| 🎲 **Interactive Fiction** | An AI Dungeon Master runs a text adventure just for you |
| ⚙️ **Settings** | Everything is configurable — name, model, personality, theme, and more |
| 🌐 **Web Dashboard** | The whole toolkit again, as a browser UI you can reach from any device on your network |

## 🚀 Quick Start

### 1. Install (Termux)

```bash
pkg update && pkg install python git
git clone https://github.com/ELVISDIONE/etbytes-assistant.git
cd etbytes-assistant
pip install -r requirements.txt
```

> Works outside Termux too (any Linux/macOS with Python 3.9+) — a handful of niceties (TTS, clipboard, notifications) are Termux-specific and simply no-op elsewhere.

### 2. Run it

```bash
python etbytes_assistant.py
```

First run drops you straight into a **setup wizard**:

```
🤖 Welcome to E.TBYTES Assistant
Let's get you set up — this only takes a minute.

What should I call you? › ...
Enter your Groq API key (leave blank to skip for now) › ...
Enable offline learning mode? [y/n]
Auto-start the Web Dashboard every time you launch the app? [y/n]
```

Get a free Groq API key at **[console.groq.com/keys](https://console.groq.com/keys)** — or skip it and add it later from Settings. Everything you enter is saved locally to `~/.etbytes_config.json`, never committed or sent anywhere except the AI provider you configured.

### 3. Take a look

```
╭──────────────────────────────────────────────────────╮
│           🤖 E.TBYTES ASSISTANT v2.0                  │
│              Advanced AI for Termux                   │
╰──────────────────────────────────────────────────────╯
1.  💬 AI Chat                  2.  🎮 Games & Learning
3.  📁 File Browser             4.  📥 Download File
5.  🎵 Music Player             6.  🖼️ Generate Image
7.  📄 Generate PDF             8.  📝 Generate TXT
9.  🧮 Math Solver              10. 🔍 Dependency Scanner
11. 🗂️ AI File Organiser        12. 📋 Task Manager
13. 📰 News Briefing            14. 💻 Code Assistant
15. 🌍 Language Learning        16. ⚙️ Settings
17. 📜 View Logs                18. 🎲 Interactive Fiction (RPG)
19. 🎨 ASCII Art Generator      20. 🌐 Web Dashboard
0.  🚪 Exit
```

## 🌐 Web Dashboard

Everything above, in a browser — handy when you'd rather tap than type, or want to use it from another device on the same Wi-Fi.

```bash
# Launch just the dashboard (foreground):
python etbytes_assistant.py --web

# Or from the CLI menu: option 20 starts/stops it in the background,
# so you can keep using the terminal menu at the same time.
```

Want it running automatically every time you open the app? Turn on **Settings → Toggle Web Dashboard Autostart**, or answer "yes" to the autostart question in the setup wizard. On launch you'll get both a local URL and a LAN URL, so any device on your network can connect.

A random login password is generated the first time the dashboard starts (printed to the console) — change it from the dashboard's Settings page once you're in.

## 🛠️ CLI reference

```bash
python etbytes_assistant.py            # normal interactive CLI
python etbytes_assistant.py --web      # web dashboard only, runs in the foreground
python etbytes_assistant.py --setup    # re-run the setup wizard at any time
```

## ⚙️ Configuration

All settings live in `~/.etbytes_config.json` (created on first run, never tracked by git). Key fields:

| Key | Description |
|---|---|
| `groq_api_key` | Your Groq API key ([get one free](https://console.groq.com/keys)) |
| `user_name` | What the assistant calls you |
| `model` | Groq model id (default: `llama-3.1-8b-instant`) |
| `personality` | System prompt / behavior instructions for the AI |
| `offline_enabled` | Use local learned Q&A matching when there's no internet |
| `web_autostart` | Launch the web dashboard automatically every time you start the CLI |

Everything here can also be changed from the in-app **Settings** menu — no need to hand-edit the file.

## 📦 Dependencies

Core dependencies are installed via `requirements.txt`. A few features are optional and gracefully degrade if their library isn't installed (the app tells you what's missing on startup):

- `scikit-learn` — smarter offline learning (TF-IDF matching)
- `watchdog` — live file-watching for the AI File Organiser
- `yt-dlp` — music downloads

## 🤝 Contributing

Issues and PRs are welcome — fork it, branch it, send a pull request.

## 📄 License

[MIT](LICENSE) — do what you want with it, just keep the license notice.
