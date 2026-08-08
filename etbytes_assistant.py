#!/usr/bin/env python3
"""
E.TBYTES Assistant - Advanced AI for Termux
Author: ELVISDIONE (E.TBYTES) <elvisteddy269@gmail.com>
Version: 2.0 (Fixed & Upgraded)
"""

import os
import sys
import json
import time
import random
import subprocess
import threading
import queue
import re
import shutil
import sqlite3
import socket
import signal
import argparse
from pathlib import Path
from datetime import datetime, timedelta
from typing import Optional, Dict, Any, List

# Offline AI imports
try:
    from sklearn.feature_extraction.text import TfidfVectorizer
    from sklearn.metrics.pairwise import cosine_similarity
    SKLEARN_AVAILABLE = True
except ImportError:
    SKLEARN_AVAILABLE = False

# External libraries (check availability)
import requests
from rich.console import Console
from rich.panel import Panel
from rich.prompt import Prompt, Confirm, IntPrompt
from rich.table import Table
from rich.tree import Tree
from rich.progress import Progress, SpinnerColumn, TextColumn
from rich.layout import Layout
from rich.live import Live
from rich.text import Text
from rich import print as rprint

# Optional imports - will be checked
try:
    from watchdog.observers import Observer
    from watchdog.events import FileSystemEventHandler
    WATCHDOG_AVAILABLE = True
except ImportError:
    WATCHDOG_AVAILABLE = False

try:
    import yt_dlp
except ImportError:
    yt_dlp = None

try:
    from fpdf import FPDF
    FPDF_AVAILABLE = True
except ImportError:
    FPDF_AVAILABLE = False

try:
    import qrcode
    QRCODE_AVAILABLE = True
except ImportError:
    QRCODE_AVAILABLE = False

try:
    import matplotlib.pyplot as plt
    MPL_AVAILABLE = True
except ImportError:
    MPL_AVAILABLE = False

try:
    import feedparser
    FEEDPARSER_AVAILABLE = True
except ImportError:
    FEEDPARSER_AVAILABLE = False

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
QA_FILE = os.path.join(SCRIPT_DIR, "qa_pairs.json")

# ---------- Configuration ----------
CONFIG_FILE = os.path.expanduser("~/.etbytes_config.json")
LOG_FILE = os.path.expanduser("~/.etbytes_log.txt")
TASKS_FILE = os.path.expanduser("~/.etbytes_tasks.json")
USER_STORAGE = os.path.expanduser("~/storage/shared")
DOWNLOADS_DIR = os.path.expanduser("~/storage/downloads")

APP_VERSION = "2.0.0"

WEB_APP_PATH = os.path.join(SCRIPT_DIR, "web_app.py")
WEB_PID_FILE = os.path.expanduser("~/.etbytes_web.pid")
WEB_LOG_FILE = os.path.expanduser("~/.etbytes_web.log")
WEB_PORT = 5000

console = Console()

# Default config - NO REAL KEYS
default_config = {
    "groq_api_key": "",                 # Must be set by user
    "model": "llama-3.1-8b-instant",
    "user_name": "",                    # Asked for by the setup wizard on first run
    "tts_voice": "en-us-female",
    "theme": "dark",
    "file_watcher_enabled": True,
    "auto_git_commit": False,
    "git_repo_path": os.path.expanduser("~"),
    "news_sources": [
        "http://feeds.bbci.co.uk/news/world/rss.xml",
        "http://rss.cnn.com/rss/edition.rss"
    ],
    "flashcard_deck": "default",
    "offline_enabled": False,
    "personality": "You are a helpful and concise assistant.",
    "learning_enabled": True,
    "log_enabled": True,
    "web_autostart": False,             # Auto-launch the web dashboard on every CLI start
    "check_updates_on_startup": True,   # Silently check for new commits when the CLI starts
    "_setup_complete": False,           # Flips to True once the first-run wizard finishes
}

def load_config():
    if os.path.exists(CONFIG_FILE):
        with open(CONFIG_FILE, 'r') as f:
            return json.load(f)
    else:
        save_config(default_config)
        return default_config.copy()

def save_config(cfg):
    with open(CONFIG_FILE, 'w') as f:
        json.dump(cfg, f, indent=2)

config = load_config()

# ---------- Logging ----------
def log_activity(action: str, details: str = ""):
    if not config.get("log_enabled", True):
        return
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    sanitized = details.replace(config.get("groq_api_key", ""), "****") if config.get("groq_api_key") else details
    with open(LOG_FILE, "a", encoding="utf-8") as f:
        f.write(f"[{timestamp}] {action}: {sanitized}\n")

# ---------- First-run Setup Wizard ----------
DEFAULT_NAME_OPTIONS = ["Master", "Coder", "Eng", "Boss", "Champ", "Chief"]

def setup_wizard():
    """Interactive onboarding: asks for a name, Groq API key, and a couple of
    preferences, then saves them to CONFIG_FILE. Only ever called from the
    CLI entry point (never on import), so importing this module as a library
    (e.g. from web_app.py) never triggers interactive prompts."""
    console.clear()
    console.print(Panel(
        "[bold magenta]🤖 Welcome to E.TBYTES Assistant[/bold magenta]\n"
        "[dim]Let's get you set up — this only takes a minute.[/dim]",
        border_style="magenta",
        subtitle="[dim]Made by ELVISDIONE (E.TBYTES) · elvisteddy269@gmail.com[/dim]"
    ))

    name = Prompt.ask("What should I call you?", default=config.get("user_name") or random.choice(DEFAULT_NAME_OPTIONS))
    config["user_name"] = name

    console.print(
        "\n[cyan]Groq powers the AI chat, code assistant, and more.[/cyan]\n"
        "[dim]Get a free key at https://console.groq.com/keys — you can also set this later from Settings.[/dim]"
    )
    key = Prompt.ask("Enter your Groq API key (leave blank to skip for now)",
                      password=True, default=config.get("groq_api_key") or "", show_default=False)
    config["groq_api_key"] = key

    config["offline_enabled"] = Confirm.ask(
        "Enable offline learning mode? (lets the assistant learn from your chats and reply without internet)",
        default=config.get("offline_enabled", False)
    )
    config["web_autostart"] = Confirm.ask(
        "Auto-start the Web Dashboard every time you launch the app?",
        default=config.get("web_autostart", False)
    )

    config["_setup_complete"] = True
    save_config(config)

    summary = Table(show_header=False, box=None, padding=(0, 1))
    summary.add_column(style="cyan")
    summary.add_column()
    summary.add_row("Name:", name)
    summary.add_row("Groq API key:", "set ✅" if key else "[yellow]not set — add it later in Settings[/yellow]")
    summary.add_row("Offline learning:", "ON" if config["offline_enabled"] else "OFF")
    summary.add_row("Web dashboard autostart:", "ON" if config["web_autostart"] else "OFF")
    console.print(Panel(summary, title="[green]You're all set![/green]", border_style="green"))
    Prompt.ask("\nPress Enter to continue")

# ---------- Dependency Checker ----------
def check_requirements():
    """Check that all required external binaries and optional Python packages are available."""
    required_binaries = ["termux-tts-speak", "termux-speech-to-text", "mpv", "git",
                         "termux-open", "termux-clipboard-get", "termux-clipboard-set",
                         "termux-notification", "nc"]
    missing_binaries = []
    for binary in required_binaries:
        if not shutil.which(binary):
            missing_binaries.append(binary)

    if missing_binaries:
        console.print(f"[yellow]Some Termux binaries missing: {', '.join(missing_binaries)}[/yellow]")
        console.print("Install them with: pkg install termux-api mpv git netcat-openbsd")

    # Check optional Python libs and inform but don't stop
    if not WATCHDOG_AVAILABLE:
        console.print("[dim]watchdog not installed (file watcher disabled)[/dim]")
    if not FPDF_AVAILABLE:
        console.print("[dim]fpdf not installed (PDF generation disabled)[/dim]")
    if not QRCODE_AVAILABLE:
        console.print("[dim]qrcode not installed (QR generation disabled)[/dim]")
    if not MPL_AVAILABLE:
        console.print("[dim]matplotlib not installed (plotting disabled)[/dim]")
    if not FEEDPARSER_AVAILABLE:
        console.print("[dim]feedparser not installed (news briefing limited)[/dim]")

    # If API key is still empty, prompt now
    if not config["groq_api_key"]:
        console.print("[bold yellow]Groq API key is not set.[/bold yellow]")
        config["groq_api_key"] = Prompt.ask("Enter your Groq API key (from console.groq.com)", password=True)
        save_config(config)

# ---------- Groq API (fixed streaming) ----------
GROQ_API_URL = "https://api.groq.com/openai/v1/chat/completions"
GROQ_TIMEOUT = 30  # seconds; applies to both connecting and gaps between reads

def groq_chat(messages: List[Dict], stream=False):
    """Call Groq API. If stream=True, yields individual content chunks."""
    if not config["groq_api_key"]:
        console.print("[red]Error: Groq API key not set.[/red]")
        yield "API key missing."
        return

    headers = {
        "Authorization": f"Bearer {config['groq_api_key']}",
        "Content-Type": "application/json"
    }
    payload = {
        "model": config["model"],
        "messages": messages,
        "temperature": 0.7,
        "max_tokens": 1024,
        "stream": stream
    }
    try:
        if stream:
            response = requests.post(GROQ_API_URL, json=payload, headers=headers, stream=True, timeout=GROQ_TIMEOUT)
            response.raise_for_status()
            for line in response.iter_lines():
                if line:
                    line = line.decode('utf-8')
                    if line.startswith("data: "):
                        data = line[6:]
                        if data == "[DONE]":
                            break
                        try:
                            chunk = json.loads(data)
                            delta = chunk.get("choices", [{}])[0].get("delta", {})
                            content = delta.get("content", "")
                            if content:
                                yield content
                        except:
                            pass
        else:
            response = requests.post(GROQ_API_URL, json=payload, headers=headers, timeout=GROQ_TIMEOUT)
            response.raise_for_status()
            full_text = response.json()["choices"][0]["message"]["content"]
            yield full_text
    except requests.exceptions.Timeout:
        log_activity("Groq API Error", "Request timed out")
        console.print("[red]API Error: request timed out.[/red]")
        yield "Sorry, the AI took too long to respond. Please try again."
    except Exception as e:
        log_activity("Groq API Error", str(e))
        console.print(f"[red]API Error: {e}[/red]")
        yield f"Sorry, an error occurred: {e}"

# ---------- Voice I/O (improved) ----------

def stream_groq_response(messages):
    """Stream Groq response and return full text."""
    full_text = ""
    for token in groq_chat(messages, stream=True):
        console.print(token, end="", style="bold cyan")
        full_text += token
        time.sleep(0.02)
    console.print()
    return full_text
def speak(text: str):
    """Text-to-speech with error reporting."""
    if not text or not text.strip():
        return
    try:
        result = subprocess.run(
            ["termux-tts-speak", "-v", config["tts_voice"], text],
            capture_output=True, text=True, timeout=20, check=False
        )
        if result.returncode != 0:
            console.print(f"[red]TTS failed (code {result.returncode}): {result.stderr.strip()}[/red]")
            log_activity("TTS Error", result.stderr)
        else:
            log_activity("TTS", text[:50])
    except FileNotFoundError:
        console.print("[red]termux-tts-speak not found.[/red]")
    except Exception as e:
        console.print(f"[red]TTS error: {e}[/red]")

def listen(timeout=5) -> str:
    """Listen for voice input with timeout. Returns transcribed text."""
    try:
        proc = subprocess.run(
            ["termux-speech-to-text", "-t", str(timeout)],
            capture_output=True, text=True, timeout=timeout+2
        )
        return proc.stdout.strip()
    except subprocess.TimeoutExpired:
        return ""
    except Exception:
        return ""

# ---------- Typing effect ----------
def typewriter_effect(text: str, delay=0.03):
    for ch in text:
        console.print(ch, end="", style="bold cyan")
        time.sleep(delay)

# ---------- File Watcher + Auto Organise (fixed) ----------
if WATCHDOG_AVAILABLE:
    class FileWatcherHandler(FileSystemEventHandler):
        def on_created(self, event):
            if not event.is_directory and config["file_watcher_enabled"]:
                log_activity("File created", event.src_path)
                threading.Thread(target=self.prompt_organise, args=(event.src_path,), daemon=True).start()

        def prompt_organise(self, filepath):
            console.print(f"\n[green]New file detected: {filepath}[/green]")
            if Confirm.ask("Organise with AI?"):
                organise_files_ai()

    def start_file_watcher():
        event_handler = FileWatcherHandler()
        observer = Observer()
        scheduled = 0
        for path in [DOWNLOADS_DIR, USER_STORAGE]:
            if os.path.exists(path) and os.access(path, os.R_OK):
                try:
                    observer.schedule(event_handler, path, recursive=False)
                    scheduled += 1
                except PermissionError:
                    console.print(f"[yellow]No permission to watch {path} (run 'termux-setup-storage')[/yellow]")
            elif os.path.exists(path):
                console.print(f"[yellow]No permission to watch {path} (run 'termux-setup-storage')[/yellow]")
        if scheduled == 0:
            console.print("[yellow]File watcher not started: no accessible directories[/yellow]")
            return None
        try:
            observer.start()
        except PermissionError:
            console.print("[yellow]File watcher failed to start due to storage permissions[/yellow]")
            return None
        log_activity("File Watcher", "Started")
        return observer
else:
    def start_file_watcher():
        console.print("[yellow]File watcher disabled (install watchdog)[/yellow]")
        return None

def organise_files_ai():
    """AI-powered file organisation (improved)."""
    # Gather all files in Downloads (flatten)
    files = []
    for root, dirs, filenames in os.walk(DOWNLOADS_DIR):
        for f in filenames:
            files.append(os.path.join(root, f))
    if not files:
        console.print("[yellow]No files to organise.[/yellow]")
        return

    # Build prompt with full list (limit to 200 for API)
    file_list = "\n".join([f"- {os.path.basename(f)} ({os.path.splitext(f)[1]})" for f in files[:200]])
    prompt = f"""I have the following files in my Downloads folder. Suggest a logical folder structure to organise them. Only propose folders and which file goes where. Return ONLY a JSON object mapping target folder -> list of filenames (exact base names as listed). Do not include paths.

Files:
{file_list}

Output JSON only."""
    messages = [{"role": "user", "content": prompt}]
    response = "".join(list(groq_chat(messages, stream=False)))
    try:
        # Extract JSON
        json_match = re.search(r'\{.*\}', response, re.DOTALL)
        if json_match:
            plan = json.loads(json_match.group())
            console.print("[bold]AI suggested organisation:[/bold]")
            for folder, flist in plan.items():
                console.print(f"📁 {folder}: {', '.join(flist)}")
            if Confirm.ask("Apply this organisation?"):
                for folder, flist in plan.items():
                    target_dir = os.path.join(DOWNLOADS_DIR, folder)
                    os.makedirs(target_dir, exist_ok=True)
                    for fname in flist:
                        src = os.path.join(DOWNLOADS_DIR, fname)
                        if os.path.exists(src):
                            shutil.move(src, os.path.join(target_dir, fname))
                console.print("[green]Files organised![/green]")
                log_activity("AI Organisation", "Applied plan")
            else:
                console.print("[yellow]Organisation cancelled.[/yellow]")
        else:
            console.print("[red]AI response did not contain valid JSON.[/red]")
    except Exception as e:
        console.print(f"[red]Organisation failed: {e}[/red]")
        log_activity("Organisation Error", str(e))

# ---------- Git Auto Commit ----------
def git_auto_commit():
    if not config["auto_git_commit"]:
        return
    repo = config["git_repo_path"]
    try:
        subprocess.run(["git", "-C", repo, "add", "."], check=True, capture_output=True)
        subprocess.run(["git", "-C", repo, "commit", "-m", f"Auto-commit by E.TBYTES {datetime.now()}"], check=True, capture_output=True)
        log_activity("Git", "Auto-commit performed")
    except:
        pass

# ---------- Self-Update ----------
# Tracks whether a background startup check found new commits, so main_menu()
# can flag it without blocking on the network on every redraw.
_update_status = {"checked": False, "available": False, "behind": 0}

def check_for_updates(silent=False):
    """Compare the local checkout against its upstream branch.
    Returns (has_update: bool, behind: int, log_text: str) on success, or None
    if the check couldn't be performed (no git, not a repo, no network, etc.)."""
    if not shutil.which("git"):
        if not silent:
            console.print("[yellow]git is not installed — can't check for updates.[/yellow]")
        return None
    if not os.path.isdir(os.path.join(SCRIPT_DIR, ".git")):
        if not silent:
            console.print("[yellow]This isn't a git checkout, so it can't self-update. "
                           "Re-clone from GitHub to get the latest version.[/yellow]")
        return None
    try:
        subprocess.run(["git", "-C", SCRIPT_DIR, "fetch", "--quiet"],
                        check=True, capture_output=True, timeout=20)
        local = subprocess.run(["git", "-C", SCRIPT_DIR, "rev-parse", "@"],
                                check=True, capture_output=True, text=True, timeout=10).stdout.strip()
        remote = subprocess.run(["git", "-C", SCRIPT_DIR, "rev-parse", "@{u}"],
                                 check=True, capture_output=True, text=True, timeout=10).stdout.strip()
        if local == remote:
            return False, 0, ""
        behind = subprocess.run(["git", "-C", SCRIPT_DIR, "rev-list", "--count", f"{local}..{remote}"],
                                 check=True, capture_output=True, text=True, timeout=10).stdout.strip()
        log_text = subprocess.run(["git", "-C", SCRIPT_DIR, "log", "--oneline", f"{local}..{remote}"],
                                   check=True, capture_output=True, text=True, timeout=10).stdout.strip()
        return True, int(behind or 0), log_text
    except Exception as e:
        if not silent:
            console.print(f"[red]Update check failed: {e}[/red]")
        log_activity("Update Check Error", str(e))
        return None

def _background_update_check():
    """Runs once at CLI startup on a daemon thread; never blocks the menu."""
    if not config.get("check_updates_on_startup", True):
        return
    result = check_for_updates(silent=True)
    _update_status["checked"] = True
    if result and result[0]:
        _update_status["available"] = True
        _update_status["behind"] = result[1]

def apply_update(behind):
    """Pull already-fetched upstream commits and refresh dependencies. Pure/non-interactive
    (no console output, no prompts) so it can be reused by both the CLI and the web dashboard.
    Returns (success: bool, message: str)."""
    # Stash local edits (e.g. a hand-tweaked file) so a dirty checkout doesn't block the pull.
    status = subprocess.run(["git", "-C", SCRIPT_DIR, "status", "--porcelain"],
                             capture_output=True, text=True).stdout.strip()
    stashed = False
    if status:
        subprocess.run(["git", "-C", SCRIPT_DIR, "stash", "push", "-u", "-m", "etbytes-auto-update"],
                        check=True, capture_output=True)
        stashed = True

    try:
        subprocess.run(["git", "-C", SCRIPT_DIR, "pull", "--ff-only"], check=True, capture_output=True)
    except subprocess.CalledProcessError as e:
        log_activity("Update Error", str(e))
        if stashed:
            subprocess.run(["git", "-C", SCRIPT_DIR, "stash", "pop"], check=False)
        return False, f"Update failed: {e.stderr.decode(errors='replace') if e.stderr else e}"

    restore_note = ""
    if stashed:
        try:
            subprocess.run(["git", "-C", SCRIPT_DIR, "stash", "pop"], check=True, capture_output=True)
        except subprocess.CalledProcessError:
            restore_note = (" Your local changes were stashed but couldn't be reapplied "
                             "automatically (conflict) — run 'git stash pop' manually to recover them.")

    req_file = os.path.join(SCRIPT_DIR, "requirements.txt")
    if os.path.exists(req_file):
        subprocess.run([sys.executable, "-m", "pip", "install", "-q", "-r", req_file], check=False)

    _update_status["available"] = False
    log_activity("Update", f"Pulled {behind} commit(s)")
    return True, f"Updated — pulled {behind} commit(s).{restore_note}"

def update_assistant():
    """Interactive CLI flow: check, confirm, apply, and offer to restart into the new version."""
    console.print("[cyan]Checking for updates...[/cyan]")
    result = check_for_updates()
    if result is None:
        return
    has_update, behind, log_text = result
    if not has_update:
        console.print("[green]You're already on the latest version.[/green]")
        return

    console.print(Panel(f"[bold yellow]{behind} new commit(s) available:[/bold yellow]\n{log_text}",
                         border_style="yellow"))
    if not Confirm.ask("Pull and install the update now?", default=True):
        console.print("[dim]Update skipped.[/dim]")
        return

    console.print("[cyan]Applying update...[/cyan]")
    success, message = apply_update(behind)
    if not success:
        console.print(f"[red]{message}[/red]")
        return

    console.print(Panel(f"[bold green]{message}\nRestart E.TBYTES Assistant to use the new version.[/bold green]",
                         border_style="green"))
    if Confirm.ask("Restart now?", default=True):
        os.execv(sys.executable, [sys.executable, os.path.abspath(__file__)] + sys.argv[1:])

# ---------- Dependency Scanner ----------
def scan_dependencies():
    try:
        result = subprocess.run(["pip", "freeze"], capture_output=True, text=True)
        packages = result.stdout.strip().split("\n")
        table = Table(title="Installed Python Packages")
        table.add_column("Package", style="cyan")
        table.add_column("Version", style="green")
        for line in packages[:50]:
            if "==" in line:
                pkg, ver = line.split("==", 1)
                table.add_row(pkg, ver)
        console.print(table)
        log_activity("Dependency scan", f"Found {len(packages)} packages")
    except Exception as e:
        console.print(f"[red]Scan failed: {e}[/red]")

# ---------- Simple Scheduler ----------
class SimpleScheduler:
    def __init__(self):
        self.jobs = []
        self.running = True

    def add_job(self, command, interval_seconds):
        self.jobs.append({"command": command, "interval": interval_seconds, "last_run": 0})

    def run(self):
        while self.running:
            now = time.time()
            for job in self.jobs:
                if now - job["last_run"] >= job["interval"]:
                    threading.Thread(target=self._execute, args=(job["command"],), daemon=True).start()
                    job["last_run"] = now
            time.sleep(1)

    def _execute(self, cmd):
        if callable(cmd):
            cmd()
            log_activity("Scheduled job", getattr(cmd, "__name__", "callable"))
        else:
            subprocess.Popen(cmd, shell=True)
            log_activity("Scheduled job", cmd)

    def stop(self):
        self.running = False

scheduler = SimpleScheduler()

# ---------- Socket Chat: a real, multi-client TCP chat room ----------
# Shared by the terminal Socket Chat game and the web dashboard's Socket
# Chat page, so both surfaces talk to genuinely the same kind of server
# (and, if run from the same process, can even share connected clients).
class ChatServer:
    def __init__(self, port=12345):
        self.port = port
        self.clients = []       # live TCP client sockets
        self.messages = []      # rolling history (for UIs that poll it)
        self.lock = threading.Lock()
        self.server_socket = None
        self.running = False
        self.accept_thread = None

    def start(self):
        if self.running:
            return
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        s.bind(('0.0.0.0', self.port))
        s.listen(5)
        # A timeout lets the accept loop wake up periodically and notice
        # stop() on its own, instead of blocking forever inside accept().
        # Relying on close() alone to interrupt a thread parked in accept()
        # is a known Linux race: a connection arriving in the brief window
        # between `running = False` and the OS tearing down the socket can
        # still be silently accepted by the stale blocked call.
        s.settimeout(1.0)
        self.server_socket = s
        self.running = True
        self.accept_thread = threading.Thread(target=self._accept_loop, daemon=True)
        self.accept_thread.start()

    def _accept_loop(self):
        while self.running:
            try:
                conn, addr = self.server_socket.accept()
            except socket.timeout:
                continue
            except OSError:
                break
            if not self.running:
                try:
                    conn.close()
                except OSError:
                    pass
                break
            conn.settimeout(None)  # client sockets should block normally on recv()
            with self.lock:
                self.clients.append(conn)
            threading.Thread(target=self._client_loop, args=(conn, addr), daemon=True).start()
            self._record("system", f"{addr[0]}:{addr[1]} joined")
            self._broadcast(f"*** {addr[0]}:{addr[1]} joined the chat ***\n", exclude=conn)

    def _client_loop(self, conn, addr):
        name = f"{addr[0]}:{addr[1]}"
        try:
            conn.sendall(b"Connected. Type a message and press Enter.\n")
            buf = b""
            while self.running:
                data = conn.recv(4096)
                if not data:
                    break
                buf += data
                while b"\n" in buf:
                    line, buf = buf.split(b"\n", 1)
                    text = line.decode(errors="replace").strip()
                    if text:
                        self._record(name, text)
                        self._broadcast(f"{name}: {text}\n", exclude=conn)
        except OSError:
            pass
        finally:
            with self.lock:
                if conn in self.clients:
                    self.clients.remove(conn)
            try:
                conn.close()
            except OSError:
                pass
            self._record("system", f"{name} left")
            self._broadcast(f"*** {name} left the chat ***\n")

    def _record(self, sender, text):
        with self.lock:
            self.messages.append({"from": sender, "text": text, "ts": time.time()})
            self.messages = self.messages[-200:]

    def _broadcast(self, raw_line, exclude=None):
        with self.lock:
            targets = list(self.clients)
        data = raw_line.encode(errors="replace")
        for c in targets:
            if c is exclude:
                continue
            try:
                c.sendall(data)
            except OSError:
                pass

    def send_message(self, sender, text):
        """Inject a message from a non-socket source (terminal or web UI)."""
        self._record(sender, text)
        self._broadcast(f"{sender}: {text}\n")

    def get_messages(self, since=0):
        with self.lock:
            return list(self.messages[since:]), len(self.messages)

    def client_count(self):
        with self.lock:
            return len(self.clients)

    def stop(self):
        self.running = False
        with self.lock:
            clients, self.clients = self.clients, []
        for c in clients:
            try:
                c.close()
            except OSError:
                pass
        # Wait for the accept loop to actually exit before touching
        # server_socket ourselves -- see the note in start() about the
        # close()-doesn't-interrupt-accept() race this avoids.
        if self.accept_thread and self.accept_thread.is_alive():
            self.accept_thread.join(timeout=3.0)
        if self.server_socket:
            try:
                self.server_socket.close()
            except OSError:
                pass
        self.server_socket = None

def get_lan_ip():
    """Best-effort local network IP, so a UI can tell the user what to `nc` into."""
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("8.8.8.8", 80))  # UDP "connect" -- no packet actually sent
        return s.getsockname()[0]
    except OSError:
        return "127.0.0.1"
    finally:
        s.close()

# ---------- Web Dashboard launcher ----------
def _web_dashboard_pid():
    """Return the PID of a running web dashboard, or None (cleaning up a stale PID file)."""
    if not os.path.exists(WEB_PID_FILE):
        return None
    try:
        with open(WEB_PID_FILE) as f:
            pid = int(f.read().strip())
        os.kill(pid, 0)  # signal 0: check the process exists without killing it
        return pid
    except (OSError, ValueError):
        try:
            os.remove(WEB_PID_FILE)
        except OSError:
            pass
        return None

def _print_dashboard_urls():
    console.print(f"[green]Local:[/green]  http://127.0.0.1:{WEB_PORT}")
    console.print(f"[green]LAN:[/green]    http://{get_lan_ip()}:{WEB_PORT}")

def start_web_dashboard(blocking=False):
    """Launch web_app.py. blocking=True runs it in the foreground (used by --web);
    otherwise it's started in the background and can be reused by autostart / the menu."""
    if not os.path.exists(WEB_APP_PATH):
        console.print(f"[red]Can't find web_app.py next to this script ({WEB_APP_PATH}).[/red]")
        return

    existing_pid = _web_dashboard_pid()
    if existing_pid:
        console.print(f"[yellow]Web dashboard is already running (PID {existing_pid}).[/yellow]")
        _print_dashboard_urls()
        return

    if blocking:
        console.print("[bold cyan]Starting E.TBYTES Web Dashboard...[/bold cyan]")
        _print_dashboard_urls()
        os.execvp(sys.executable, [sys.executable, WEB_APP_PATH])
    else:
        with open(WEB_LOG_FILE, "a") as log:
            proc = subprocess.Popen(
                [sys.executable, WEB_APP_PATH],
                stdout=log, stderr=subprocess.STDOUT,
                start_new_session=True
            )
        with open(WEB_PID_FILE, "w") as f:
            f.write(str(proc.pid))
        console.print(f"[green]Web dashboard starting in the background (PID {proc.pid}).[/green]")
        console.print("[dim]It can take up to ~10s to finish loading. Logs: " + WEB_LOG_FILE + "[/dim]")
        _print_dashboard_urls()

def stop_web_dashboard():
    pid = _web_dashboard_pid()
    if not pid:
        console.print("[yellow]Web dashboard isn't running.[/yellow]")
        return
    try:
        os.kill(pid, signal.SIGTERM)
    except OSError:
        pass
    try:
        os.remove(WEB_PID_FILE)
    except OSError:
        pass
    console.print(f"[green]Stopped web dashboard (PID {pid}).[/green]")

# ---------- File Downloader ----------
def download_file():
    """Advanced downloader: select types by number, multiple allowed (comma separated)."""
    console.print("[bold]Download Files[/bold]")
    console.print("Select what to download (e.g., 1,3,5):")
    options = [
        "1. Entire site (mirror)",
        "2. Images (.jpg, .png, .gif, ...)",
        "3. Videos (.mp4, .mkv, .avi, ...)",
        "4. Audio (.mp3, .wav, .flac, ...)",
        "5. Texts (.txt, .pdf, .doc, ...)",
        "6. Image URLs (save list)",
        "7. Video URLs (save list)",
        "8. Audio URLs (save list)"
    ]
    for opt in options:
        console.print(opt)
    choices_str = Prompt.ask("Enter numbers (comma separated)", default="1")
    try:
        selected = [int(x.strip()) for x in choices_str.split(",") if x.strip().isdigit()]
    except:
        console.print("[red]Invalid input. Use numbers like 1,2,4[/red]")
        return
    if not selected:
        console.print("[yellow]No valid options selected.[/yellow]")
        return

    url = Prompt.ask("Enter URL")

    # Check for required external tools
    if 1 in selected:
        if not shutil.which("wget"):
            console.print("[red]wget not found. Install with: pkg install wget[/red]")
            return

    # Check for requests and BeautifulSoup if any scraping needed
    need_scrape = any(x in selected for x in [2,3,4,5,6,7,8])
    if need_scrape:
        try:
            from bs4 import BeautifulSoup
            import requests as req
        except ImportError:
            console.print("[red]BeautifulSoup/requests not installed. Run: pip install requests beautifulsoup4[/red]")
            return

    # --- Helper: scrape links from page ---
    def get_links(base_url, extensions=None):
        """Return a list of absolute URLs matching extensions (list of lowercased strings without dot) or all if None."""
        try:
            resp = req.get(base_url, timeout=10)
            soup = BeautifulSoup(resp.text, 'html.parser')
        except Exception as e:
            console.print(f"[red]Failed to fetch page: {e}[/red]")
            return []
        links = []
        for tag in soup.find_all(['a', 'img', 'source', 'video', 'audio']):
            href = tag.get('href') or tag.get('src')
            if not href:
                continue
            # Skip anchors, javascript, mailto
            if href.startswith('#') or href.startswith('javascript:') or href.startswith('mailto:'):
                continue
            full_url = req.compat.urljoin(base_url, href)
            if extensions is not None:
                ext = os.path.splitext(full_url.split('?')[0])[1].lower().lstrip('.')
                if ext not in extensions:
                    continue
            links.append(full_url)
        return list(set(links))  # deduplicate

    # --- Definitions of extensions ---
    img_exts = ["jpg","jpeg","png","gif","bmp","svg","webp","ico","tiff"]
    vid_exts = ["mp4","mkv","avi","mov","flv","wmv","webm","m4v","mpg","mpeg"]
    aud_exts = ["mp3","wav","flac","m4a","ogg","aac","wma","opus","mid"]
    txt_exts = ["txt","pdf","doc","docx","md","csv","log","rtf","odt","ppt","pptx","xls","xlsx"]

    # --- Download actual files using requests with progress ---
    def download_files(links, folder):
        os.makedirs(folder, exist_ok=True)
        with Progress(TextColumn("[progress.description]{task.description}"), SpinnerColumn(), transient=False) as progress:
            task = progress.add_task("Downloading...", total=len(links))
            for link in links:
                fname = os.path.basename(link.split('?')[0])
                if not fname:
                    fname = f"file_{int(time.time())}"
                fpath = os.path.join(folder, fname)
                try:
                    r = req.get(link, stream=True, timeout=30)
                    with open(fpath, 'wb') as f:
                        for chunk in r.iter_content(chunk_size=8192):
                            f.write(chunk)
                except Exception as e:
                    console.print(f"[red]Failed {link}: {e}[/red]")
                progress.advance(task)
        console.print(f"[green]Saved {len(links)} file(s) to {folder}[/green]")

    # --- Process each selected option ---
    for opt in selected:
        if opt == 1:
            # Mirror entire site with wget
            console.print("[bold]Mirroring entire site...[/bold]")
            subprocess.run([
                "wget", "--mirror", "--convert-links", "--adjust-extension",
                "--page-requisites", "--no-parent", "-e", "robots=off",
                "-P", "site_mirror", url
            ])
            console.print("[green]Site mirroring started (check site_mirror/).[/green]")
            log_activity("Download", f"Site mirror: {url}")

        elif opt == 2:
            links = get_links(url, img_exts)
            if links:
                download_files(links, "downloaded_images")
            else:
                console.print("[yellow]No images found.[/yellow]")

        elif opt == 3:
            links = get_links(url, vid_exts)
            if links:
                download_files(links, "downloaded_videos")
            else:
                console.print("[yellow]No videos found.[/yellow]")

        elif opt == 4:
            links = get_links(url, aud_exts)
            if links:
                download_files(links, "downloaded_audio")
            else:
                console.print("[yellow]No audio found.[/yellow]")

        elif opt == 5:
            links = get_links(url, txt_exts)
            if links:
                download_files(links, "downloaded_texts")
            else:
                console.print("[yellow]No text documents found.[/yellow]")

        elif opt == 6:
            links = get_links(url, img_exts)
            if links:
                filename = "image_urls.txt"
                with open(filename, "w") as f:
                    f.write("\n".join(links))
                console.print(f"[green]Image URLs saved to {filename}[/green]")
            else:
                console.print("[yellow]No image URLs found.[/yellow]")

        elif opt == 7:
            links = get_links(url, vid_exts)
            if links:
                filename = "video_urls.txt"
                with open(filename, "w") as f:
                    f.write("\n".join(links))
                console.print(f"[green]Video URLs saved to {filename}[/green]")
            else:
                console.print("[yellow]No video URLs found.[/yellow]")

        elif opt == 8:
            links = get_links(url, aud_exts)
            if links:
                filename = "audio_urls.txt"
                with open(filename, "w") as f:
                    f.write("\n".join(links))
                console.print(f"[green]Audio URLs saved to {filename}[/green]")
            else:
                console.print("[yellow]No audio URLs found.[/yellow]")

    log_activity("Download", f"Multi-download from {url}, options: {selected}")
def music_player():
    console.print("[bold]Music Player[/bold]")
    console.print("1. Play local audio file (choose from list)")
    console.print("2. Play online (YouTube URL)")
    choice = Prompt.ask("Choice", choices=["1","2"])
    if choice == "1":
        # Ask for a directory; default to ~/storage/music or ~/storage/shared
        default_dir = os.path.expanduser("~/storage/music")
        if not os.path.isdir(default_dir):
            default_dir = os.path.expanduser("~/storage/shared")
        music_dir = Prompt.ask("Directory to scan", default=default_dir)
        if not os.path.isdir(music_dir):
            console.print(f"[red]Directory not found: {music_dir}[/red]")
            return
        # Supported audio extensions
        audio_exts = (".mp3", ".wav", ".flac", ".m4a", ".ogg", ".aac", ".wma", ".opus")
        audio_files = []
        for root, dirs, filenames in os.walk(music_dir):
            for f in filenames:
                if f.lower().endswith(audio_exts):
                    audio_files.append(os.path.join(root, f))
        if not audio_files:
            console.print("[yellow]No audio files found in that directory.[/yellow]")
            return
        # List with numbers
        console.print(f"[bold]Found {len(audio_files)} audio file(s):[/bold]")
        for idx, fpath in enumerate(audio_files, 1):
            console.print(f"  {idx}. {os.path.basename(fpath)}")
        selection = Prompt.ask("Enter file number to play (or 0 to cancel)", default="0")
        try:
            num = int(selection)
        except ValueError:
            console.print("[red]Invalid input.[/red]")
            return
        if num == 0:
            return
        if 1 <= num <= len(audio_files):
            try:
                subprocess.Popen(["mpv", audio_files[num-1]])
            except FileNotFoundError:
                console.print("[red]mpv not found. Install with: pkg install mpv[/red]")
        else:
            console.print("[red]Number out of range.[/red]")
    else:
        url = Prompt.ask("YouTube URL")
        try:
            subprocess.Popen(["mpv", url])
        except FileNotFoundError:
            console.print("[red]mpv not found. Install with: pkg install mpv[/red]")
def generate_image():
    prompt = Prompt.ask("Image description")
    with Progress(SpinnerColumn(), TextColumn("[progress.description]{task.description}"), transient=True) as progress:
        progress.add_task(description="Generating...", total=None)
        resp = requests.get(f"https://image.pollinations.ai/prompt/{prompt}")
        if resp.status_code == 200:
            content_type = resp.headers.get("Content-Type", "")
            if "image" in content_type:
                filename = f"gen_{int(time.time())}.png"
                with open(filename, "wb") as f:
                    f.write(resp.content)
                console.print(f"[green]Image saved as {filename}[/green]")
                log_activity("Image gen", prompt)
            else:
                console.print("[red]Generation returned non-image content.[/red]")
        else:
            console.print("[red]Generation failed[/red]")

# ---------- PDF / TXT Generation ----------
def generate_pdf():
    if not FPDF_AVAILABLE:
        console.print("[red]fpdf not installed. Run: pip install fpdf[/red]")
        return
    content = Prompt.ask("Enter text for PDF")
    filename = Prompt.ask("PDF filename", default="output.pdf")
    try:
        pdf = FPDF()
        pdf.add_page()
        pdf.set_font("Helvetica", size=12)
        pdf.multi_cell(0, 10, content)
        pdf.output(filename)
        console.print(f"[green]PDF created: {filename}[/green]")
        log_activity("PDF gen", filename)
    except Exception as e:
        console.print(f"[red]PDF generation error: {e}[/red]")

def generate_txt():
    console.print("[bold]Text Generator[/bold]")
    console.print("1. Enhance existing text with AI (polish, improve)")
    console.print("2. Generate new text from a topic/prompt")
    choice = Prompt.ask("Choice", choices=["1", "2"], default="1")
    if choice == "1":
        user_text = Prompt.ask("Enter the text you want to enhance")
        prompt = f"Please improve the following text, making it clearer, more engaging, and correcting any errors. Return only the improved text:\n\n{user_text}"
    else:
        topic = Prompt.ask("What should the text be about?")
        prompt = f"Write a short text about: {topic}. Be concise and engaging."
    filename = Prompt.ask("TXT filename", default="note.txt")
    messages = [{"role": "user", "content": prompt}]
    console.print("[bold]AI is generating text...[/bold]")
    full_response = "".join(list(groq_chat(messages, stream=False)))
    if not full_response.strip():
        console.print("[red]AI returned empty response. Saving original input if any.[/red]")
        if choice == "1":
            with open(filename, "w") as f:
                f.write(user_text)
        else:
            with open(filename, "w") as f:
                f.write("")
        return
    with open(filename, "w") as f:
        f.write(full_response)
    console.print(f"[green]Text file saved: {filename}[/green]")
    log_activity("TXT gen", filename)
def math_solver():
    expr = Prompt.ask("Math problem (e.g., solve x^2 + 2x - 3 = 0)")
    choice = Prompt.ask(
        "How would you like the solution? [1] Step-by-step explanation [2] Final answer only",
        choices=["1", "2"],
        default="1"
    )
    if choice == "1":
        prompt_content = f"Solve this math problem step by step, explaining each step clearly: {expr}"
    else:
        prompt_content = f"Solve this math problem and give only the final answer, no explanation: {expr}"
    messages = [{"role": "user", "content": prompt_content}]
    console.print("[bold]AI is solving...[/bold]")
    full_resp = stream_groq_response(messages)
    console.print()
def game_number_guess():
    num = random.randint(1, 100)
    guess = None
    while guess != num:
        guess = IntPrompt.ask("Guess (1-100)")
        if guess < num:
            console.print("Too low")
        elif guess > num:
            console.print("Too high")
    console.print("Correct!")

def game_hangman():
    words = ["python", "termux", "ai", "android", "groq"]
    word = random.choice(words)
    guessed = ["_"] * len(word)
    attempts = 6
    while attempts > 0 and "_" in guessed:
        console.print(" ".join(guessed))
        letter_input = Prompt.ask("Guess letter").strip()
        if not letter_input:
            continue
        letter = letter_input[0].lower()
        if letter in word:
            for i, ch in enumerate(word):
                if ch == letter:
                    guessed[i] = letter
        else:
            attempts -= 1
            console.print(f"Wrong! {attempts} left")
    if "_" not in guessed:
        console.print(f"You won! The word was {word}")
    else:
        console.print(f"Lost! Word was {word}")

_TTT_WINS = [(0,1,2),(3,4,5),(6,7,8),(0,3,6),(1,4,7),(2,5,8),(0,4,8),(2,4,6)]
def _ttt_fallback_move(board):
    """Simple heuristic used only if the AI call fails or returns junk."""
    for player in ("O", "X"):
        for a, b, c in _TTT_WINS:
            cells = [a, b, c]
            vals = [board[i] for i in cells]
            empty = [i for i in cells if board[i] == " "]
            if len(empty) == 1 and vals.count(player) == 2:
                return empty[0]
    if board[4] == " ":
        return 4
    corners = [i for i in (0, 2, 6, 8) if board[i] == " "]
    if corners:
        return random.choice(corners)
    empty = [i for i, v in enumerate(board) if v == " "]
    return random.choice(empty) if empty else -1

def ai_ttt_move(board):
    """Ask the AI which cell O should take next; falls back to a heuristic on failure."""
    empty = [i for i, v in enumerate(board) if v == " "]
    if not empty:
        return -1
    board_str = ",".join(v if v != " " else "_" for v in board)
    prompt = (
        "You are the O player in a Tic-Tac-Toe game against a human playing X. "
        "Cells are indexed 0-8 in this layout:\n0 1 2\n3 4 5\n6 7 8\n"
        f"Current board (comma-separated, '_' = empty): {board_str}\n"
        f"Empty cells: {empty}\n"
        "Pick the empty cell that gives O the best chance to win or block X. "
        "Respond with ONLY the index number, nothing else."
    )
    try:
        resp = list(groq_chat([{"role": "user", "content": prompt}], stream=False))
        reply = resp[0] if resp else ""
        match = re.search(r"-?\d+", reply)
        if match:
            idx = int(match.group())
            if idx in empty:
                return idx
    except Exception:
        pass
    return _ttt_fallback_move(board)

def game_tictactoe():
    board = [" "]*9
    def print_board():
        console.print(f"{board[0]}|{board[1]}|{board[2]}\n-+-+-\n{board[3]}|{board[4]}|{board[5]}\n-+-+-\n{board[6]}|{board[7]}|{board[8]}")
    def winner():
        for a, b, c in _TTT_WINS:
            if board[a] == board[b] == board[c] != " ":
                return board[a]
        return "draw" if " " not in board else None
    console.print("[cyan]You are X, the AI is O.[/cyan]")
    result = None
    while True:
        print_board()
        pos = IntPrompt.ask("Choose position (1-9)") - 1
        if pos < 0 or pos > 8 or board[pos] != " ":
            console.print("Invalid move")
            continue
        board[pos] = "X"
        result = winner()
        if result:
            break
        console.print("[cyan]AI is thinking...[/cyan]")
        board[ai_ttt_move(board)] = "O"
        result = winner()
        if result:
            break
    print_board()
    if result == "draw":
        console.print("Draw!")
    else:
        console.print("You win!" if result == "X" else "AI wins!")

def games_menu():
    while True:
        console.print("[bold cyan]🎮 Games & Tools[/bold cyan]")
        items = [
            "Number Guess", "Hangman", "TicTacToe", "Gen Quiz", "Tech Quiz",
            "ASCII/Bin", "Hex/Oct/Dec", "Web Scraper", "Socket Chat", "Plot Data",
            "Design Patterns", "Regex", "Password", "QR", "Weather", "Calculator", "RPS"
        ]
        # Create a two-column table
        game_table = Table(show_header=False, box=None, show_edge=False, padding=(0, 1))
        game_table.add_column(style="cyan", justify="left")
        game_table.add_column(style="cyan", justify="left")
        for i in range(0, len(items), 2):
            left = f"{i+1}. {items[i]}"
            right = f"{i+2}. {items[i+1]}" if i+1 < len(items) else ""
            game_table.add_row(left, right)
        console.print(game_table)
        console.print("0. Back")
        choice = Prompt.ask("Select", choices=[str(i) for i in range(0,len(items)+1)], default="0")
        if choice == "0":
            break
        # Map to functions
        game_funcs = {
            1: game_number_guess,
            2: game_hangman,
            3: game_tictactoe,
            4: lambda: quiz_general(),
            5: lambda: quiz_tech(),
            6: lambda: ascii_bin_converter(),
            7: lambda: hex_oct_dec(),
            8: lambda: web_scraper(),
            9: lambda: socket_chat(),
            10: lambda: plot_data(),
            11: lambda: design_patterns(),
            12: lambda: regex_tester(),
            13: lambda: password_gen(),
            14: lambda: qr_maker(),
            15: lambda: weather(),
            16: lambda: calculator(),
            17: lambda: rps()
        }
        func = game_funcs.get(int(choice))
        if func:
            func()
            Prompt.ask("\nPress Enter to continue")


# Static fallback banks, used only if the AI generation call fails.
QUIZ_BANKS = {
    "general": [
        {"q": "What is the capital of France?", "options": ["Berlin", "Madrid", "Paris", "Rome"], "a": 2},
        {"q": "How many continents are there?", "options": ["5", "6", "7", "8"], "a": 2},
        {"q": "What is the largest ocean?", "options": ["Atlantic", "Indian", "Arctic", "Pacific"], "a": 3},
        {"q": "Who wrote Romeo and Juliet?", "options": ["Dickens", "Shakespeare", "Hemingway", "Tolstoy"], "a": 1},
        {"q": "What planet is known as the Red Planet?", "options": ["Venus", "Mars", "Jupiter", "Saturn"], "a": 1},
    ],
    "tech": [
        {"q": "What does CPU stand for?", "options": ["Central Processing Unit", "Computer Personal Unit", "Central Program Utility", "Core Processing Unit"], "a": 0},
        {"q": "What does HTML stand for?", "options": ["HyperText Markup Language", "HighText Machine Language", "Hyperlink Text Markup Language", "Home Tool Markup Language"], "a": 0},
        {"q": "Who created Python?", "options": ["Dennis Ritchie", "James Gosling", "Guido van Rossum", "Bjarne Stroustrup"], "a": 2},
        {"q": "What does RAM stand for?", "options": ["Random Access Memory", "Read Access Memory", "Run Access Module", "Rapid Access Memory"], "a": 0},
        {"q": "What does API stand for?", "options": ["Application Programming Interface", "Automated Program Instruction", "Applied Programming Index", "App Process Interface"], "a": 0},
    ],
}

def ai_generate_quiz(kind, count=5):
    """Ask the AI for a fresh batch of multiple-choice questions; falls back to a static bank on failure."""
    topic = "general knowledge" if kind == "general" else "technology and computing"
    prompt = (
        f"Generate {count} multiple-choice trivia questions about {topic}. "
        'Respond with ONLY a JSON array, no other text, in this exact form: '
        '[{"q": "question text", "options": ["opt1","opt2","opt3","opt4"], "a": 0}] '
        'where "a" is the zero-based index of the correct option in "options". '
        "Make the questions varied and the options plausible but unambiguous."
    )
    try:
        resp = list(groq_chat([{"role": "user", "content": prompt}], stream=False))
        reply = resp[0] if resp else ""
        match = re.search(r'\[.*\]', reply, re.DOTALL)
        if match:
            data = json.loads(match.group())
            questions = []
            for item in data:
                q, opts, idx = item.get("q"), item.get("options"), item.get("a")
                if (isinstance(q, str) and isinstance(opts, list) and len(opts) == 4
                        and isinstance(idx, int) and 0 <= idx < 4):
                    questions.append({"q": q, "options": opts, "a": idx})
            if questions:
                return questions[:count]
    except Exception:
        pass
    bank = QUIZ_BANKS.get(kind, QUIZ_BANKS["general"])
    return random.sample(bank, min(count, len(bank)))

def run_quiz_mc(kind, title):
    console.print(f"[bold cyan]{title}[/bold cyan]")
    questions = ai_generate_quiz(kind, 5)
    letters = ["A", "B", "C", "D"]
    score = 0
    for i, q in enumerate(questions, 1):
        console.print(f"\n[bold]{i}. {q['q']}[/bold]")
        for letter, opt in zip(letters, q["options"]):
            console.print(f"  {letter}) {opt}")
        ans = Prompt.ask("Your answer", choices=letters[:len(q["options"])]).upper()
        if letters.index(ans) == q["a"]:
            console.print("[green]Correct![/green]")
            score += 1
        else:
            console.print(f"[red]Wrong. Answer: {letters[q['a']]}) {q['options'][q['a']]}[/red]")
    console.print(f"\n[bold]Final score: {score}/{len(questions)}[/bold]")

def quiz_general():
    run_quiz_mc("general", "❓ General Quiz")
def quiz_tech():
    run_quiz_mc("tech", "🖥️ Tech Quiz")
def ascii_bin_converter():
    text = Prompt.ask("Enter text")
    console.print(f"ASCII: {[ord(c) for c in text]}")
    console.print(f"Binary: {' '.join(format(ord(c), '08b') for c in text)}")

def hex_oct_dec():
    num = IntPrompt.ask("Enter decimal number")
    console.print(f"Hex: {hex(num)}, Oct: {oct(num)}, Bin: {bin(num)}")

def web_scraper():
    url = Prompt.ask("URL")
    try:
        from bs4 import BeautifulSoup
        r = requests.get(url, timeout=10)
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
            links.append(requests.compat.urljoin(url, href))
        links = sorted(set(links))

        images = []
        for img in soup.find_all("img", src=True):
            images.append(requests.compat.urljoin(url, img["src"]))
        images = sorted(set(images))

        console.print(Panel(
            f"[bold]Title:[/bold] {title}\n"
            f"[bold]Description:[/bold] {description}\n"
            f"[bold]Word count:[/bold] {word_count}\n"
            f"[bold]Links found:[/bold] {len(links)}\n"
            f"[bold]Images found:[/bold] {len(images)}",
            title=url, border_style="cyan"
        ))
        if links:
            console.print("[bold]Sample links:[/bold]")
            for link in links[:15]:
                console.print(f"  {link}")
            if len(links) > 15:
                console.print(f"  ... and {len(links) - 15} more")

        if Confirm.ask("Save full link + image list to a file?", default=False):
            filename = Prompt.ask("Filename", default="scrape_results.txt")
            with open(filename, "w", encoding="utf-8") as f:
                f.write(f"URL: {url}\nTitle: {title}\nDescription: {description}\nWord count: {word_count}\n\n")
                f.write(f"Links ({len(links)}):\n" + "\n".join(links) + "\n\n")
                f.write(f"Images ({len(images)}):\n" + "\n".join(images) + "\n")
            console.print(f"[green]Saved to {filename}[/green]")

        log_activity("Web Scraper", url)
    except Exception as e:
        console.print(f"[red]Error: {e}[/red]")

def socket_chat():
    console.print("[bold]📡 Socket Chat[/bold]")
    console.print("Starts a real chat server on this device. Others on the same network can join with:")
    port_str = Prompt.ask("Port", default="12345")
    try:
        port = int(port_str)
    except ValueError:
        port = 12345

    server = ChatServer(port=port)
    try:
        server.start()
    except OSError as e:
        console.print(f"[red]Could not start server on port {port}: {e}[/red]")
        return

    ip = get_lan_ip()
    console.print(f"[green]nc {ip} {port}[/green]")
    console.print("Type a message and press Enter to broadcast it. Type 'exit' to stop the server.\n")

    stop_event = threading.Event()
    last_shown = 0

    def watcher():
        nonlocal last_shown
        while not stop_event.is_set():
            msgs, nxt = server.get_messages(last_shown)
            for m in msgs:
                if m["from"] == "system":
                    console.print(f"[dim]*** {m['text']} ***[/dim]")
                elif m["from"] != "you":
                    console.print(f"[cyan]{m['from']}:[/cyan] {m['text']}")
            last_shown = nxt
            time.sleep(0.5)

    threading.Thread(target=watcher, daemon=True).start()

    while True:
        msg = Prompt.ask("You")
        if msg.strip().lower() == "exit":
            break
        if msg.strip():
            server.send_message("you", msg)

    stop_event.set()
    server.stop()
    console.print(f"[yellow]Server on port {port} stopped.[/yellow]")
    log_activity("Socket Chat", f"Session on port {port} ended")

def plot_data():
    if not MPL_AVAILABLE:
        console.print("[red]matplotlib not installed[/red]")
        return
    kind = Prompt.ask("Chart type", choices=["line", "bar", "scatter", "pie"], default="line")
    if kind == "pie":
        labels_str = Prompt.ask("Labels (comma separated)", default="A,B,C")
        values_str = Prompt.ask("Values (comma separated)", default="30,50,20")
        labels = [s.strip() for s in labels_str.split(",")]
        try:
            values = [float(v.strip()) for v in values_str.split(",")]
        except ValueError:
            console.print("[red]Values must be numbers.[/red]")
            return
        fig, ax = plt.subplots()
        ax.pie(values, labels=labels, autopct="%1.1f%%")
    else:
        x_str = Prompt.ask("X values (comma separated)", default="1,2,3,4,5")
        y_str = Prompt.ask("Y values (comma separated)", default="1,4,9,16,25")
        try:
            x = [float(v.strip()) for v in x_str.split(",")]
            y = [float(v.strip()) for v in y_str.split(",")]
        except ValueError:
            console.print("[red]Values must be numbers.[/red]")
            return
        if len(x) != len(y):
            console.print("[red]X and Y must have the same number of values.[/red]")
            return
        fig, ax = plt.subplots()
        if kind == "line":
            ax.plot(x, y, marker="o")
        elif kind == "bar":
            ax.bar(x, y)
        else:
            ax.scatter(x, y)

    title = Prompt.ask("Chart title", default="")
    if title:
        ax.set_title(title)
    filename = Prompt.ask("Filename", default="plot.png")
    fig.savefig(filename)
    plt.close(fig)
    console.print(f"[green]Plot saved as {filename}[/green]")
    log_activity("Plot", f"{kind} chart -> {filename}")

def design_patterns():
    console.print("[bold]📐 Design Patterns[/bold]")
    pattern = Prompt.ask(
        "Pattern",
        choices=["Singleton", "Factory", "Observer", "Strategy", "Decorator",
                 "Adapter", "Builder", "Command", "Custom"],
        default="Singleton",
    )
    if pattern == "Custom":
        pattern = Prompt.ask("Which pattern?")
    language = Prompt.ask("Language", default="Python")
    prompt = (
        f"Explain the {pattern} design pattern concisely (2-3 sentences: what problem it solves "
        f"and when to use it), then give a complete, runnable {language} code example demonstrating it."
    )
    messages = [{"role": "user", "content": prompt}]
    console.print("[bold cyan]AI:[/bold cyan]")
    result = stream_groq_response(messages)
    console.print()
    if Confirm.ask("Save to file?", default=False):
        filename = Prompt.ask("Filename", default=f"{pattern.lower()}_pattern.txt")
        with open(filename, "w", encoding="utf-8") as f:
            f.write(result)
        console.print(f"[green]Saved to {filename}[/green]")
    log_activity("Design Patterns", f"{pattern} in {language}")

def regex_tester():
    pattern = Prompt.ask("Regex")
    text = Prompt.ask("Text")
    matches = re.findall(pattern, text)
    console.print(f"Matches: {matches}")

def password_gen():
    import string, secrets
    length = IntPrompt.ask("Length", default=12)
    chars = string.ascii_letters + string.digits + string.punctuation
    pwd = ''.join(secrets.choice(chars) for _ in range(length))
    console.print(f"Password: {pwd}")

def qr_maker():
    if not QRCODE_AVAILABLE:
        console.print("[red]qrcode not installed[/red]")
        return
    data = Prompt.ask("Data")
    img = qrcode.make(data)
    img.save("qr.png")
    console.print("QR saved as qr.png")

def weather():
    try:
        subprocess.run(["curl", "wttr.in"])
    except FileNotFoundError:
        console.print("[red]curl not found. Install with: pkg install curl[/red]")

def calculator():
    expr = Prompt.ask("Expression")
    try:
        res = eval(expr, {"__builtins__": {}}, {})
        console.print(f"= {res}")
    except:
        console.print("Invalid")

_rps_history = []  # persists across rounds within this CLI session
def ai_rps_move(history):
    """Ask the AI to pick rock/paper/scissors, optionally reading the human's recent pattern."""
    hist_str = ", ".join(history[-10:]) if history else "none yet"
    prompt = (
        "You are playing Rock Paper Scissors against a human. "
        f"The human's recent choices, oldest first: {hist_str}. "
        "Pick your next move: rock, paper, or scissors. You may use the history "
        "to anticipate a pattern, but don't be too predictable yourself. "
        "Respond with ONLY one word: rock, paper, or scissors."
    )
    try:
        resp = list(groq_chat([{"role": "user", "content": prompt}], stream=False))
        reply = (resp[0] if resp else "").strip().lower()
        for choice in ("rock", "paper", "scissors"):
            if choice in reply:
                return choice
    except Exception:
        pass
    return random.choice(["rock", "paper", "scissors"])

def rps():
    choices = ["rock", "paper", "scissors"]
    user = Prompt.ask("Your choice", choices=choices)
    comp = ai_rps_move(_rps_history)
    _rps_history.append(user)
    del _rps_history[:-10]
    console.print(f"AI: {comp}")
    if user == comp:
        console.print("Tie")
    elif (user=="rock" and comp=="scissors") or (user=="paper" and comp=="rock") or (user=="scissors" and comp=="paper"):
        console.print("You win!")
    else:
        console.print("You lose!")

# ---------- File Browser ----------
def file_browser(path="/"):
    if not path:
        path = "/"
    while True:
        if not os.path.exists(path):
            console.print("[red]Path not found[/red]")
            break
        tree = Tree(f"📁 {path}")
        try:
            for item in sorted(os.listdir(path)):
                full = os.path.join(path, item)
                if os.path.isdir(full):
                    tree.add(f"📂 {item}")
                else:
                    tree.add(f"📄 {item}")
        except PermissionError:
            console.print("[red]Permission denied[/red]")
        console.print(tree)
        new = Prompt.ask("Enter directory or file to open (q to quit)", default="q")
        if new == "q":
            break
        new_path = os.path.join(path, new)
        if os.path.isdir(new_path):
            path = new_path
        elif os.path.isfile(new_path):
            try:
                subprocess.run(["termux-open", new_path])
            except FileNotFoundError:
                console.print("[red]termux-open not found. Install Termux:API (pkg install termux-api).[/red]")
        else:
            console.print("Invalid")

# ---------- AI Chat (fixed streaming) ----------
def ai_chat():
    conversation = [
        {"role": "system", "content": config.get("personality", "You are a helpful assistant.") + f" The user's name is {config['user_name']}."}
    ]
    console.print(Panel(f"[bold green]AI Chat with {config['user_name']}[/bold green]\nType 'exit' to quit, 'voice' for voice input, 'file' to browse.", border_style="cyan"))

    # Initialize offline AI on first chat if learning enabled
    if config.get("learning_enabled", True) and offline_ai is None:
        init_offline_ai()

    # Simple internet connectivity check
    def is_internet_available():
        try:
            requests.get("https://api.groq.com", timeout=2)
            return True
        except:
            return False

    use_offline = config.get("offline_enabled", False) or not is_internet_available()
    if use_offline and offline_ai is None:
        init_offline_ai()

    while True:
        user_input = Prompt.ask(f"[bold yellow]{config['user_name']}[/bold yellow]")
        if user_input.lower() == "exit":
            break
        elif user_input.lower() == "voice":
            console.print("[dim]Listening (5s)...[/dim]")
            spoken = listen(timeout=5)
            if spoken:
                console.print(f"You said: {spoken}")
                user_input = spoken
            else:
                console.print("[red]No speech detected[/red]")
                continue
        elif user_input.lower() == "file":
            file_browser()
            continue

        conversation.append({"role": "user", "content": user_input})

        # Offline mode handling
        if use_offline and offline_ai is not None:
            offline_answer = offline_ai.search(user_input)
            if offline_answer:
                console.print("[bold yellow]Offline answer:[/bold yellow] " + offline_answer)
                conversation.append({"role": "assistant", "content": offline_answer})
                if Confirm.ask("Speak the response?", default=False):
                    speak(offline_answer)
                continue
            else:
                console.print("[red]I don't have an offline answer for that yet.[/red]")
                continue

        # Online AI response
        console.print("[bold cyan]AI:[/bold cyan] ", end="")
        full_response = stream_groq_response(conversation)
        console.print()
        conversation.append({"role": "assistant", "content": full_response})

        # Save Q&A for offline learning
        if config.get("learning_enabled", True) and offline_ai is not None and full_response and not use_offline:
            offline_ai.add_qa(user_input, full_response)

        if Confirm.ask("Speak the response?", default=False):
            speak(full_response)

        log_activity("AI Chat", f"Q: {user_input[:50]}... A: {full_response[:50]}...")
def load_tasks():
    if os.path.exists(TASKS_FILE):
        with open(TASKS_FILE, 'r') as f:
            return json.load(f)
    return []

def save_tasks(tasks):
    with open(TASKS_FILE, 'w') as f:
        json.dump(tasks, f, indent=2)

def task_manager():
    tasks = load_tasks()
    while True:
        console.print("[bold]📋 Task Manager[/bold]")
        console.print("1. Add task (natural language)")
        console.print("2. List tasks")
        console.print("3. Complete task")
        console.print("4. AI parse due dates")
        console.print("0. Back")
        choice = Prompt.ask("Choice", choices=["0","1","2","3","4"], default="0")
        if choice == "0":
            break
        elif choice == "1":
            desc = Prompt.ask("Describe task (e.g., 'Buy groceries tomorrow at 5pm')")
            # Use AI to extract due date? (simple version)
            tasks.append({"desc": desc, "due": None, "completed": False, "created": str(datetime.now())})
            save_tasks(tasks)
            console.print("[green]Task added.[/green]")
        elif choice == "2":
            if not tasks:
                console.print("No tasks.")
            else:
                for idx, t in enumerate(tasks):
                    status = "[x]" if t["completed"] else "[ ]"
                    due_str = f" (due: {t['due']})" if t["due"] else ""
                    console.print(f"{idx+1}. {status} {t['desc']}{due_str}")
        elif choice == "3":
            if not tasks:
                continue
            for idx, t in enumerate(tasks):
                console.print(f"{idx+1}. {t['desc']}")
            num = IntPrompt.ask("Task number to complete") - 1
            if 0 <= num < len(tasks):
                tasks[num]["completed"] = True
                save_tasks(tasks)
                console.print("[green]Marked complete.[/green]")
        elif choice == "4":
            # AI parse due dates for tasks without due date
            for t in tasks:
                if not t["due"]:
                    prompt = f"Extract the due date from this task description. If there is a date/time, return it in 'YYYY-MM-DD HH:MM' format. If none, say 'none'. Task: '{t['desc']}'"
                    msg = [{"role": "user", "content": prompt}]
                    resp = "".join(list(groq_chat(msg, stream=False)))
                    if "none" not in resp.lower():
                        # crude extraction
                        t["due"] = resp.strip()
            save_tasks(tasks)
            console.print("[green]AI parsed due dates.[/green]")

# ---------- New Feature: News Briefing ----------
def news_briefing():
    if not FEEDPARSER_AVAILABLE:
        console.print("[red]feedparser not installed. Install with pip install feedparser[/red]")
        return
    console.print("[bold]📰 News Briefing[/bold]")
    sources = config.get("news_sources", [])
    if not sources:
        console.print("No news sources configured.")
        return
    all_entries = []
    for url in sources:
        try:
            feed = feedparser.parse(url)
            for entry in feed.entries[:5]:
                all_entries.append(entry)
        except Exception as e:
            console.print(f"[yellow]Failed to parse {url}: {e}[/yellow]")
    if not all_entries:
        console.print("No news fetched.")
        return
    # Summarize with AI
    headlines = "\n".join([f"- {entry.title}" for entry in all_entries])
    prompt = f"Here are today's top headlines. Provide a brief summary (2-3 sentences) of the major events, then list each headline with a one-line summary.\n\n{headlines}"
    messages = [{"role": "user", "content": prompt}]
    console.print("[bold cyan]AI Summary:[/bold cyan]")
    full = stream_groq_response(messages)
    console.print()
    log_activity("News briefing", f"Processed {len(all_entries)} articles")

# ---------- New Feature: Code Assistant ----------
def code_assistant():
    console.print("[bold]💻 Code Assistant[/bold]")
    console.print("1. Explain code")
    console.print("2. Refactor code")
    console.print("3. Generate unit tests")
    console.print("0. Back")
    choice = Prompt.ask("Choice", choices=["0","1","2","3"], default="0")
    if choice == "0":
        return
    console.print("Paste your code (end with a line containing only '###'):")
    lines = []
    while True:
        try:
            line = input()
        except EOFError:
            break
        if line.strip() == "###":
            break
        lines.append(line)
    code = "\n".join(lines)
    if not code.strip():
        return
    if choice == "1":
        prompt = f"Explain the following code in simple terms:\n\n{code}"
    elif choice == "2":
        prompt = f"Refactor this code to be more efficient and readable. Show the improved code and explain the changes:\n\n{code}"
    else:
        prompt = f"Generate unit tests for this code using pytest:\n\n{code}"
    messages = [{"role": "user", "content": prompt}]
    console.print("[bold cyan]AI:[/bold cyan]")
    full = stream_groq_response(messages)
    console.print()
    log_activity("Code Assistant", choice)

# ---------- New Feature: Language Learning ----------
def language_learning():
    console.print("[bold]🌍 Language Learning[/bold]")
    console.print("1. Start a lesson (AI generated)")
    console.print("2. Flashcards (review)")
    console.print("0. Back")
    choice = Prompt.ask("Choice", choices=["0","1","2"], default="0")
    if choice == "0":
        return
    if choice == "1":
        language = Prompt.ask("Which language? (e.g., Spanish, French)")
        level = Prompt.ask("Your level", choices=["beginner", "intermediate", "advanced"], default="beginner")
        topic = Prompt.ask("Topic (e.g., greetings, food, travel)", default="common phrases")
        prompt = f"Create a short {level} level {language} lesson about {topic}. Include 5 new words/phrases with English translations and an example sentence for each. Then a short practice dialogue."
        messages = [{"role": "user", "content": prompt}]
        console.print("[bold cyan]Lesson:[/bold cyan]")
        full = ""
        for token in groq_chat(messages, stream=True):
            console.print(token, end="", style="bold cyan")
            full += token
            time.sleep(0.01)
        console.print()
    else:
        # Simple flashcard system
        flashcards_file = os.path.expanduser(f"~/.etbytes_flashcards_{config.get('flashcard_deck','default')}.json")
        if not os.path.exists(flashcards_file):
            # Generate some with AI
            lang = Prompt.ask("Language for flashcards?")
            prompt = f"Generate 10 English-{lang} vocabulary flashcards in JSON format: [{{\"front\": \"...\", \"back\": \"...\"}}]"
            messages = [{"role": "user", "content": prompt}]
            resp = "".join(list(groq_chat(messages, stream=False)))
            try:
                json_match = re.search(r'\[.*\]', resp, re.DOTALL)
                if json_match:
                    cards = json.loads(json_match.group())
                    with open(flashcards_file, 'w') as f:
                        json.dump(cards, f)
                    console.print("[green]Flashcards created.[/green]")
                else:
                    console.print("[red]AI failed to generate flashcards.[/red]")
                    return
            except:
                console.print("[red]Error generating flashcards.[/red]")
                return
        with open(flashcards_file, 'r') as f:
            cards = json.load(f)
        random.shuffle(cards)
        for card in cards[:10]:
            console.print(f"Front: {card['front']}")
            input("Press Enter to see back...")
            console.print(f"Back: {card['back']}\n")
        console.print("Session complete.")

# ---------- Settings Menu ----------
def settings_menu():
    while True:
        console.print("[bold]⚙️ Settings[/bold]")

        # Build a two-column table
        settings_table = Table(show_header=False, box=None, show_edge=False, padding=(0, 1))
        settings_table.add_column(style="cyan", justify="left")
        settings_table.add_column(style="cyan", justify="left")

        items = [
            f"1. Change Groq API Key (current: {'*'*10 if config['groq_api_key'] else 'NOT SET'})",
            f"2. Change Model (current: {config['model']})",
            f"3. Change User Name (current: {config['user_name']})",
            f"4. Change TTS Voice (current: {config['tts_voice']})",
            f"5. Toggle Theme ({config['theme']})",
            f"6. Toggle File Watcher ({'ON' if config['file_watcher_enabled'] else 'OFF'})",
            f"7. Toggle Git Auto Commit ({'ON' if config['auto_git_commit'] else 'OFF'})",
            "8. Copy API Key to clipboard",
            "9. Copy Model to clipboard",
            "10. Set AI Personality",
            f"11. Toggle Offline Mode ({'ON' if config.get('offline_enabled', False) else 'OFF'})",
            f"12. Toggle Learning ({'ON' if config.get('learning_enabled', True) else 'OFF'})",
            f"13. Toggle Web Dashboard Autostart ({'ON' if config.get('web_autostart', False) else 'OFF'})",
            f"14. Toggle Check Updates on Startup ({'ON' if config.get('check_updates_on_startup', True) else 'OFF'})",
        ]

        for i in range(0, len(items), 2):
            left = items[i]
            right = items[i+1] if i+1 < len(items) else ""
            settings_table.add_row(left, right)

        console.print(settings_table)
        console.print("0. Back")

        choice = Prompt.ask("Choice", choices=[str(i) for i in range(0,15)], default="0")
        if choice == "0":
            break
        elif choice == "1":
            new_key = Prompt.ask("Enter new Groq API key", password=True)
            config["groq_api_key"] = new_key
            save_config(config)
            console.print("[green]API key updated.[/green]")
        elif choice == "2":
            new_model = Prompt.ask("Enter model name", default=config["model"])
            config["model"] = new_model
            save_config(config)
        elif choice == "3":
            new_name = Prompt.ask("Enter your name", default=config["user_name"])
            config["user_name"] = new_name
            save_config(config)
        elif choice == "4":
            new_voice = Prompt.ask("Voice (e.g., en-us-female, en-us-male)", default=config["tts_voice"])
            config["tts_voice"] = new_voice
            save_config(config)
        elif choice == "5":
            config["theme"] = "light" if config["theme"] == "dark" else "dark"
            save_config(config)
            console.print(f"Theme changed to {config['theme']}. Restart to apply fully.")
        elif choice == "6":
            config["file_watcher_enabled"] = not config["file_watcher_enabled"]
            save_config(config)
        elif choice == "7":
            config["auto_git_commit"] = not config["auto_git_commit"]
            save_config(config)
        elif choice == "8":
            key = config.get("groq_api_key", "")
            if key:
                try:
                    subprocess.run(["termux-clipboard-set", key])
                    console.print("[green]API key copied to clipboard.[/green]")
                except FileNotFoundError:
                    console.print("[red]termux-clipboard-set not found. Install Termux:API.[/red]")
            else:
                console.print("[red]No API key set.[/red]")
        elif choice == "9":
            model = config.get("model", "")
            try:
                subprocess.run(["termux-clipboard-set", model])
                console.print(f"[green]Model '{model}' copied to clipboard.[/green]")
            except FileNotFoundError:
                console.print("[red]termux-clipboard-set not found. Install Termux:API.[/red]")
        elif choice == "10":
            new_personality = Prompt.ask("Enter AI personality / behavior instructions", default=config.get("personality", "You are a helpful and concise assistant."))
            config["personality"] = new_personality
            save_config(config)
            console.print("[green]Personality updated.[/green]")
        elif choice == "11":
            config["offline_enabled"] = not config.get("offline_enabled", False)
            save_config(config)
            console.print(f"[green]Offline mode {'enabled' if config['offline_enabled'] else 'disabled'}.[/green]")
        elif choice == "12":
            config["learning_enabled"] = not config.get("learning_enabled", True)
            save_config(config)
            console.print(f"[green]Learning {'enabled' if config['learning_enabled'] else 'disabled'}.[/green]")
        elif choice == "13":
            config["web_autostart"] = not config.get("web_autostart", False)
            save_config(config)
            console.print(f"[green]Web dashboard autostart {'enabled' if config['web_autostart'] else 'disabled'}.[/green]")
        elif choice == "14":
            config["check_updates_on_startup"] = not config.get("check_updates_on_startup", True)
            save_config(config)
            console.print(f"[green]Startup update checks {'enabled' if config['check_updates_on_startup'] else 'disabled'}.[/green]")
def interactive_fiction():
    console.print("[bold]🎮 Interactive Fiction (AI Dungeon Master)[/bold]")
    console.print("You are in a mysterious world. Type actions, and the AI will continue the story.")
    console.print("Type 'exit' to quit, 'inventory' to see your items, 'look' to examine your surroundings.")

    conversation = [
        {"role": "system", "content": "You are a creative dungeon master. Lead an immersive text adventure. Keep responses concise and vivid. Describe the scene, react to the player's actions, and advance the story."}
    ]
    inventory = []
    last_scene = ""
    scene_set = False
    while True:
        if not scene_set:
            start_prompt = "The adventure begins. Describe the starting location and the immediate situation. Make it engaging."
            messages = [{"role": "user", "content": start_prompt}]
            console.print("[bold cyan]DM:[/bold cyan] ", end="")
            full = stream_groq_response(messages)
            console.print()
            conversation.append({"role": "assistant", "content": full})
            last_scene = full
            scene_set = True
        else:
            action = Prompt.ask("Your action")
            if action.lower() == "exit":
                console.print("[green]Exiting the adventure. Farewell![/green]")
                break
            if action.lower() == "inventory":
                if inventory:
                    console.print("You are carrying: " + ", ".join(inventory))
                else:
                    console.print("You have nothing.")
                continue
            if action.lower() == "look":
                prompt = f"Current scene: {last_scene}\nBased on the scene above, describe your surroundings again in detail."
            else:
                prompt = action

            conversation.append({"role": "user", "content": prompt})
            console.print("[bold cyan]DM:[/bold cyan] ", end="")
            full = stream_groq_response(conversation)
            console.print()
            conversation.append({"role": "assistant", "content": full})
            last_scene = full
    log_activity("Interactive Fiction", "Session ended")
ASCII_RAMP = " .'`^\",:;Il!i><~+_-?][}{1)(|\\/tfjrxnuvczXYUJCLQ0OZmwqpdbkhao*#MW&8%B@$"[::-1]


def _render_ascii_art(rgb_img_full, width, mode="color", dither=False, ramp=ASCII_RAMP):
    """
    Core image -> ASCII renderer shared by the live-camera and existing-photo
    -file ASCII features.

    rgb_img_full : a PIL Image already converted to RGB and EXIF-corrected,
                    at its ORIGINAL resolution (downscaling happens in here).
    width         : desired output width in characters.
    mode          : "color" (true per-character RGB), "grayscale" (per
                    -character shades of gray, no hue), or "none" (plain,
                    uncolored text).
    dither        : when True, applies Floyd-Steinberg error diffusion to the
                    luminance values before they're mapped to ramp
                    characters. Rounding each pixel's brightness to the
                    nearest of only ~70 ramp "levels" independently throws
                    away a little information every time; dithering instead
                    carries that per-pixel rounding error forward into
                    neighbouring not-yet-processed pixels, so on average the
                    image is reproduced far more accurately even though each
                    individual character is still just one of the same ~70
                    glyphs. This is what powers the "high accuracy" mode for
                    pre-taken photos -- it visibly reduces banding in smooth
                    gradients (skies, skin, walls) versus naive per-pixel
                    rounding.
    Returns (ascii_str, rich.text.Text) -- ascii_str is the plain-text
    version (safe to save to a .txt file), art is the colorized version
    ready for console.print().
    """
    from PIL import Image, ImageOps

    aspect_ratio = rgb_img_full.height / rgb_img_full.width
    height = max(1, int(aspect_ratio * width * 0.5))  # correct for tall terminal font cells
    rgb_img = rgb_img_full.resize((width, height), Image.LANCZOS)

    gray_img = ImageOps.autocontrast(rgb_img.convert("L"), cutoff=1)

    chars = ramp
    n = len(chars)

    # Luminance kept as floats (not the raw 0-255 ints) specifically so that
    # dithering can add fractional error to a pixel without that error being
    # silently truncated away before it's used.
    lum_grid = [[float(gray_img.getpixel((x, y))) for x in range(width)] for y in range(height)]

    ascii_str = ""
    art = Text()
    for y in range(height):
        for x in range(width):
            lum = lum_grid[y][x]
            # Diffused error can push a neighbour's running total slightly
            # outside 0-255; clamp before it's used as an index or a color.
            lum = 0.0 if lum < 0.0 else (255.0 if lum > 255.0 else lum)

            # bright pixels -> dense glyphs, dark pixels -> space, so detail
            # (and color) survives on a typical dark terminal background
            idx = int((255 - lum) * (n - 1) // 255)
            idx = max(0, min(n - 1, idx))
            ch = chars[idx]
            ascii_str += ch

            if dither:
                # The brightness this glyph actually represents (the center
                # of its quantization bucket), so we diffuse the ROUNDING
                # ERROR -- not the raw pixel value -- forward to neighbours.
                quantized = 255.0 - (idx * 255.0 / (n - 1))
                error = lum - quantized
                if x + 1 < width:
                    lum_grid[y][x + 1] += error * 7 / 16
                if y + 1 < height:
                    if x - 1 >= 0:
                        lum_grid[y + 1][x - 1] += error * 3 / 16
                    lum_grid[y + 1][x] += error * 5 / 16
                    if x + 1 < width:
                        lum_grid[y + 1][x + 1] += error * 1 / 16

            if mode == "color":
                r, g, b = rgb_img.getpixel((x, y))
                art.append(ch, style=f"rgb({r},{g},{b})")
            elif mode == "grayscale":
                g = int(lum)
                art.append(ch, style=f"rgb({g},{g},{g})")
            else:
                art.append(ch)
        ascii_str += "\n"
        art.append("\n")

    return ascii_str, art


def _camera_to_ascii():
    # Check for PIL
    try:
        from PIL import Image, ImageOps
    except ImportError:
        console.print("[red]Pillow (PIL) is not installed. Run: pip install Pillow[/red]")
        return

    cam_choice = Prompt.ask("Camera", choices=["back", "front"], default="back")
    camera_id = "0" if cam_choice == "back" else "1"

    photo_path = f"ascii_input_{int(time.time())}.jpg"
    console.print("[yellow]Taking photo...[/yellow]")
    try:
        result = subprocess.run(
            ["termux-camera-photo", "-c", camera_id, photo_path],
            check=False, timeout=10, capture_output=True, text=True,
        )
    except subprocess.TimeoutExpired:
        console.print("[red]Camera timed out. No photo taken.[/red]")
        return
    except FileNotFoundError:
        console.print("[red]termux-camera-photo not available. Install Termux:API.[/red]")
        return
    if not os.path.exists(photo_path) or os.path.getsize(photo_path) == 0:
        detail = result.stderr.strip() if result.stderr else "unknown error"
        console.print(f"[red]Failed to take photo: {detail}[/red]")
        return

    term_width = shutil.get_terminal_size(fallback=(80, 24)).columns
    default_width = min(term_width - 2, 100)
    width_str = Prompt.ask("Output width (characters)", default=str(default_width))
    try:
        width = max(10, int(width_str))
    except ValueError:
        width = default_width

    use_color = Confirm.ask("Render in color?", default=True)

    try:
        img = Image.open(photo_path)
        img = ImageOps.exif_transpose(img)  # respect camera orientation
        rgb_img_full = img.convert("RGB")

        ascii_str, art = _render_ascii_art(
            rgb_img_full, width, mode=("color" if use_color else "none"), dither=False,
        )

        console.print(art)

        save = Confirm.ask("Save ASCII art to file?", default=False)
        if save:
            filename = Prompt.ask("Filename", default="ascii_art.txt")
            with open(filename, "w") as f:
                f.write(ascii_str)
            console.print(f"[green]Saved to {filename}[/green]")

        keep_photo = Confirm.ask("Keep captured photo?", default=False)
        if not keep_photo:
            try:
                os.remove(photo_path)
            except OSError:
                pass
    except Exception as e:
        console.print(f"[red]Image processing error: {e}[/red]")
        return


def _file_to_ascii():
    """
    Convert an already-existing photo on disk (downloaded, previously taken,
    whatever) into ASCII art -- as opposed to _camera_to_ascii(), which takes
    a brand-new photo first. Offers a choice of full color vs. grayscale
    rendering, and a "high accuracy" (Floyd-Steinberg dithered) mode, since
    a pre-taken file isn't bottlenecked by camera capture latency and can
    afford a larger width / the extra dithering pass.
    """
    try:
        from PIL import Image, ImageOps
    except ImportError:
        console.print("[red]Pillow (PIL) is not installed. Run: pip install Pillow[/red]")
        return

    raw_path = Prompt.ask("Path to the photo (jpg/png/etc.)")
    path = os.path.expanduser(raw_path.strip().strip('"').strip("'"))
    if not os.path.isfile(path):
        console.print(f"[red]File not found: {path}[/red]")
        return

    try:
        img = Image.open(path)
        img = ImageOps.exif_transpose(img)  # respect any stored orientation tag
        rgb_img_full = img.convert("RGB")
    except Exception as e:
        console.print(f"[red]Could not open image: {e}[/red]")
        return

    term_width = shutil.get_terminal_size(fallback=(80, 24)).columns
    # No camera-latency constraint here, so allow a wider default/ceiling
    # than the live-capture path -- more characters sample more of the
    # source image, which is itself a form of "higher accuracy".
    default_width = min(term_width - 2, 140)
    width_str = Prompt.ask(
        "Output width (characters) — higher = more detail", default=str(default_width)
    )
    try:
        width = max(10, min(300, int(width_str)))
    except ValueError:
        width = default_width

    mode = Prompt.ask("Render mode", choices=["color", "grayscale"], default="color")
    high_accuracy = Confirm.ask(
        "High accuracy mode (Floyd-Steinberg dithering, smoother gradients, a bit slower)?",
        default=True,
    )

    try:
        ascii_str, art = _render_ascii_art(rgb_img_full, width, mode=mode, dither=high_accuracy)
    except Exception as e:
        console.print(f"[red]Image processing error: {e}[/red]")
        return

    console.print(art)

    save = Confirm.ask("Save ASCII art to file?", default=False)
    if save:
        filename = Prompt.ask("Filename", default="ascii_photo.txt")
        with open(filename, "w") as f:
            f.write(ascii_str)
        console.print(f"[green]Saved to {filename}[/green]")

    log_activity("ASCII Art", f"Converted existing file ({mode}, high_accuracy={high_accuracy})")


def ascii_art_generator():
    console.print("[bold]🎨 ASCII Art Generator[/bold]")
    console.print("1. Convert an image to ASCII (using camera)")
    console.print("2. Convert an existing photo file to ASCII (color/grayscale, high accuracy)")
    console.print("3. Generate ASCII art from text description (AI)")
    choice = Prompt.ask("Choice", choices=["1", "2", "3"], default="1")

    if choice == "1":
        _camera_to_ascii()
    elif choice == "2":
        _file_to_ascii()
    else:
        # AI‑generated ASCII art
        prompt_text = Prompt.ask("Describe the ASCII art you want (e.g., 'a cat sitting on a moon')")
        full_prompt = f"Generate only the ASCII art for: {prompt_text}. Do not explain, just output the art."
        messages = [{"role": "user", "content": full_prompt}]
        console.print("[bold]Generating...[/bold]")
        full = "".join(list(groq_chat(messages, stream=False)))
        console.print(full)
        save = Confirm.ask("Save to file?", default=False)
        if save:
            filename = Prompt.ask("Filename", default="ascii_ai.txt")
            with open(filename, "w") as f:
                f.write(full)
            console.print(f"[green]Saved to {filename}[/green]")
    log_activity("ASCII Art", "Generation completed")

# ---------- Offline AI (self-learning) ----------

class SimpleOfflineAI:
    """Lightweight keyword-matching fallback when sklearn is unavailable."""
    def __init__(self, qa_file):
        self.qa_file = qa_file
        self.qa_pairs = []
        self.load()

    def load(self):
        if os.path.exists(self.qa_file):
            try:
                with open(self.qa_file, "r", encoding="utf-8") as f:
                    self.qa_pairs = json.load(f)
            except:
                self.qa_pairs = []
        else:
            self.qa_pairs = []

    def add_qa(self, question, answer):
        self.qa_pairs.append({
            "question": question,
            "answer": answer,
            "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        })
        if len(self.qa_pairs) > 500:
            self.qa_pairs = self.qa_pairs[-500:]
        with open(self.qa_file, "w", encoding="utf-8") as f:
            json.dump(self.qa_pairs, f, indent=2)

    def search(self, query, threshold=0.5):
        query_words = set(query.lower().split())
        best_score = 0
        best_answer = None
        for qa in self.qa_pairs:
            q_words = set(qa["question"].lower().split())
            if not query_words or not q_words:
                continue
            common = query_words & q_words
            score = len(common) / max(len(query_words), 1)
            if score > best_score and score >= threshold:
                best_score = score
                best_answer = qa["answer"]
        return best_answer


class OfflineAI:
    """Simple TF-IDF based offline knowledge retrieval."""
    def __init__(self, qa_file):
        self.qa_file = qa_file
        self.qa_pairs = []
        self.questions = []
        self.vectorizer = TfidfVectorizer()
        self.matrix = None
        self.load()

    def load(self):
        if os.path.exists(self.qa_file):
            try:
                with open(self.qa_file, "r", encoding="utf-8") as f:
                    self.qa_pairs = json.load(f)
            except:
                self.qa_pairs = []
        else:
            self.qa_pairs = []
        self.rebuild_index()

    def rebuild_index(self):
        self.questions = [qa["question"] for qa in self.qa_pairs]
        if self.questions and SKLEARN_AVAILABLE:
            try:
                self.matrix = self.vectorizer.fit_transform(self.questions)
            except:
                self.matrix = None
        else:
            self.matrix = None

    def add_qa(self, question, answer):
        self.qa_pairs.append({
            "question": question,
            "answer": answer,
            "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        })
        if len(self.qa_pairs) > 500:
            self.qa_pairs = self.qa_pairs[-500:]
        with open(self.qa_file, "w", encoding="utf-8") as f:
            json.dump(self.qa_pairs, f, indent=2)
        self.rebuild_index()

    def search(self, query, threshold=0.6):
        if not self.questions or self.matrix is None or not SKLEARN_AVAILABLE:
            return None
        try:
            query_vec = self.vectorizer.transform([query])
            sims = cosine_similarity(query_vec, self.matrix)[0]
            best_idx = sims.argmax()
            if sims[best_idx] >= threshold:
                return self.qa_pairs[best_idx]["answer"]
        except:
            pass
        return None

offline_ai = None
def init_offline_ai():
    global offline_ai
    if SKLEARN_AVAILABLE:
        offline_ai = OfflineAI(QA_FILE)
    else:
        offline_ai = SimpleOfflineAI(QA_FILE)
        console.print("[yellow]scikit-learn not installed. Using simple keyword matching for offline AI.[/yellow]")

def main_menu():
    # Check requirements first
    check_requirements()
    if config.get("learning_enabled", True):
        init_offline_ai()

    # Start file watcher if enabled and available
    watcher = None
    if config["file_watcher_enabled"] and WATCHDOG_AVAILABLE:
        watcher = start_file_watcher()

    # Start scheduler thread
    threading.Thread(target=scheduler.run, daemon=True).start()
    if config["auto_git_commit"]:
        scheduler.add_job(git_auto_commit, 3600)

    # Non-blocking check for a newer version of the assistant itself
    threading.Thread(target=_background_update_check, daemon=True).start()

    while True:
        console.clear()
        console.print(Panel(f"[bold magenta]🤖 E.TBYTES ASSISTANT v{APP_VERSION}[/bold magenta]", subtitle="Advanced AI for Termux"))
        console.print("[dim]Made by ELVISDIONE (E.TBYTES) · elvisteddy269@gmail.com[/dim]", justify="center")
        if _update_status["available"]:
            console.print(f"[yellow]🔔 {_update_status['behind']} update(s) available — choose option 21 to update.[/yellow]",
                           justify="center")
        menu_table = Table(show_header=False, box=None, show_edge=False, padding=(0, 1))
        menu_table.add_column(style="cyan", justify="left")
        menu_table.add_column(style="cyan", justify="left")
        menu_table.add_row("1.  💬 AI Chat", "2.  🎮 Games & Learning")
        menu_table.add_row("3.  📁 File Browser", "4.  📥 Download File")
        menu_table.add_row("5.  🎵 Music Player", "6.  🖼️ Generate Image")
        menu_table.add_row("7.  📄 Generate PDF", "8.  📝 Generate TXT")
        menu_table.add_row("9.  🧮 Math Solver", "10. 🔍 Dependency Scanner")
        menu_table.add_row("11. 🗂️ AI File Organiser", "12. 📋 Task Manager")
        menu_table.add_row("13. 📰 News Briefing", "14. 💻 Code Assistant")
        menu_table.add_row("15. 🌍 Language Learning", "16. ⚙️ Settings")
        menu_table.add_row("17. 📜 View Logs", "18.  🎲 Interactive Fiction (RPG)")
        menu_table.add_row("19.  🎨 ASCII Art Generator", "20. 🌐 Web Dashboard")
        menu_table.add_row("21. ⬆️  Update Assistant", "0.  🚪 Exit")
        console.print(menu_table)
        choice = Prompt.ask("Select option", choices=[str(i) for i in range(0,22)], default="0")
        if choice == "0":
            if watcher:
                watcher.stop()
                watcher.join()
            scheduler.stop()
            log_activity("App", "Exited")
            console.print("[red]Goodbye![/red]")
            break
        elif choice == "1": ai_chat()
        elif choice == "2": games_menu()
        elif choice == "3": file_browser()
        elif choice == "4": download_file()
        elif choice == "5": music_player()
        elif choice == "6": generate_image()
        elif choice == "7": generate_pdf()
        elif choice == "8": generate_txt()
        elif choice == "9": math_solver()
        elif choice == "10": scan_dependencies()
        elif choice == "11": organise_files_ai()
        elif choice == "12": task_manager()
        elif choice == "13": news_briefing()
        elif choice == "14": code_assistant()
        elif choice == "15": language_learning()
        elif choice == "16": settings_menu()
        elif choice == "17":
            if os.path.exists(LOG_FILE):
                with open(LOG_FILE, "r", encoding="utf-8") as f:
                    console.print(f.read())
            else:
                console.print("No logs yet.")
        elif choice == "18":
            interactive_fiction()
        elif choice == "19":
            ascii_art_generator()
        elif choice == "20":
            if _web_dashboard_pid():
                if Confirm.ask("Web dashboard is running. Stop it?", default=False):
                    stop_web_dashboard()
            else:
                start_web_dashboard()
        elif choice == "21":
            update_assistant()
        Prompt.ask("\nPress Enter to continue")

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="E.TBYTES Assistant")
    parser.add_argument("--web", action="store_true", help="Launch only the web dashboard (foreground)")
    parser.add_argument("--setup", action="store_true", help="Re-run the first-run setup wizard")
    parser.add_argument("--update", action="store_true", help="Check for and install updates, then exit")
    args = parser.parse_args()

    if args.update:
        update_assistant()
        sys.exit(0)

    if args.setup or not config.get("_setup_complete"):
        setup_wizard()

    if args.web:
        start_web_dashboard(blocking=True)
    else:
        if config.get("web_autostart"):
            start_web_dashboard(blocking=False)
        main_menu()


# ---------- Interactive Fiction / Chat RPG ----------
