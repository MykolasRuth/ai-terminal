import asyncio
import html
import os
import queue
import secrets
import subprocess
import sys
import threading
from pathlib import Path

from fastapi import Cookie, FastAPI, Form, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse, RedirectResponse, Response

from .config import APP_DIR, SESSION_START_DIR
from .web_auth import authenticate, credentials_ready


SESSION_COOKIE = "devserver_session"
sessions = set()
terminal_session = None
terminal_lock = threading.Lock()

app = FastAPI()


class TerminalSession:
    def __init__(self):
        self.output = queue.Queue()
        self.process = None
        self.reader = None
        self.start()

    def start(self):
        env = os.environ.copy()
        env["PYTHONUNBUFFERED"] = "1"
        env["DEVSERVER_PLAIN_INPUT"] = "1"
        env["DEVSERVER_WEB_TERMINAL"] = "1"
        env["DEVSERVER_START_CWD"] = str(SESSION_START_DIR)

        self.output.put("[Starting DevServer terminal session.]\n")
        try:
            self.process = subprocess.Popen(
                [sys.executable, str(APP_DIR / "ollama_cli.py")],
                cwd=SESSION_START_DIR,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                encoding="utf-8",
                errors="replace",
                bufsize=1,
                env=env,
            )
        except Exception as error:
            self.output.put(f"[Failed to start DevServer terminal session: {error}]\n")
            return

        self.reader = threading.Thread(target=self._read_output, daemon=True)
        self.reader.start()

    def _read_output(self):
        try:
            while True:
                chunk = self.process.stdout.read(1)
                if not chunk:
                    break
                self.output.put(chunk)
        finally:
            self.output.put("\n[DevServer terminal process exited.]\n")

    def write(self, text):
        if self.process is None or self.process.poll() is not None:
            self.output.put("\n[DevServer terminal process is not running. Refresh to restart.]\n")
            return

        self.process.stdin.write(text)
        self.process.stdin.flush()

    def drain(self):
        chunks = []
        while True:
            try:
                chunks.append(self.output.get_nowait())
            except queue.Empty:
                break
        return "".join(chunks)


def get_terminal_session():
    global terminal_session
    with terminal_lock:
        if terminal_session is None or terminal_session.process.poll() is not None:
            terminal_session = TerminalSession()
        return terminal_session


def authenticated(token):
    return bool(token and token in sessions)


def login_page(error=""):
    error_html = f"<p class='error'>{html.escape(error)}</p>" if error else ""
    return HTMLResponse(
        f"""<!doctype html>
<html>
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>DevServer Login</title>
  <style>
    body {{ margin: 0; min-height: 100vh; display: grid; place-items: center; background: #0b0f14; color: #e6edf3; font-family: ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; }}
    form {{ width: min(360px, calc(100vw - 40px)); border: 1px solid #30363d; background: #111820; padding: 28px; border-radius: 8px; }}
    h1 {{ margin: 0 0 18px; font-size: 22px; }}
    label {{ display: block; margin: 14px 0 6px; color: #9fb1c1; }}
    input {{ box-sizing: border-box; width: 100%; padding: 11px 12px; background: #0b0f14; border: 1px solid #30363d; color: #e6edf3; border-radius: 6px; }}
    button {{ margin-top: 18px; width: 100%; padding: 11px 12px; border: 0; border-radius: 6px; background: #2f81f7; color: white; font-weight: 700; }}
    .error {{ color: #ff7b72; }}
    .hint {{ color: #9fb1c1; font-size: 13px; line-height: 1.5; }}
    code {{ color: #79c0ff; }}
  </style>
</head>
<body>
  <form method="post" action="/login">
    <h1>DevServer</h1>
    {error_html}
    <label>Username</label>
    <input name="username" autocomplete="username" autofocus>
    <label>Password</label>
    <input name="password" type="password" autocomplete="current-password">
    <button type="submit">Sign In</button>
    <p class="hint">Credentials are set on the server with <code>python -m ai_terminal.web_auth</code>.</p>
  </form>
</body>
</html>"""
    )


@app.get("/")
def index(devserver_session: str | None = Cookie(default=None)):
    if not credentials_ready():
        return HTMLResponse(
            "<pre>DevServer web credentials are not configured.\n\n"
            "SSH into the server and run:\n"
            "  python -m ai_terminal.web_auth\n\n"
            "Then restart: python -m ai_terminal.web_server</pre>",
            status_code=503,
        )

    if not authenticated(devserver_session):
        return login_page()

    return HTMLResponse(TERMINAL_PAGE)


@app.post("/login")
def login(username: str = Form(default=""), password: str = Form(default="")):
    if not authenticate(username, password):
        return login_page("Invalid username or password.")

    token = secrets.token_urlsafe(32)
    sessions.add(token)
    response = RedirectResponse("/", status_code=303)
    response.set_cookie(
        SESSION_COOKIE,
        token,
        httponly=True,
        samesite="lax",
    )
    return response


@app.post("/logout")
def logout(devserver_session: str | None = Cookie(default=None)):
    if devserver_session:
        sessions.discard(devserver_session)
    response = RedirectResponse("/", status_code=303)
    response.delete_cookie(SESSION_COOKIE)
    return response


@app.websocket("/terminal")
async def terminal(websocket: WebSocket):
    token = websocket.cookies.get(SESSION_COOKIE)
    if not authenticated(token):
        print("Rejected unauthenticated web terminal connection.", flush=True)
        await websocket.close(code=1008)
        return

    await websocket.accept()
    print("Accepted web terminal connection.", flush=True)
    session = get_terminal_session()

    async def receive_input():
        while True:
            message = await websocket.receive_text()
            session.write(message)

    receive_task = asyncio.create_task(receive_input())
    try:
        while True:
            output = session.drain()
            if output:
                await websocket.send_text(output)
            await asyncio.sleep(0.03)
    except WebSocketDisconnect:
        pass
    finally:
        receive_task.cancel()


TERMINAL_PAGE = """<!doctype html>
<html>
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>DevServer</title>
  <style>
    html, body { margin: 0; height: 100%; background: #05070a; color: #d6deeb; font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, "Liberation Mono", monospace; }
    body { display: grid; grid-template-rows: auto auto auto 1fr auto; }
    header { height: 38px; display: flex; align-items: center; justify-content: space-between; padding: 0 14px; background: #0d1117; border-bottom: 1px solid #30363d; color: #8b949e; font-family: ui-sans-serif, system-ui, sans-serif; font-size: 13px; }
    header strong { color: #f0f6fc; }
    form { margin: 0; }
    button { background: transparent; border: 1px solid #30363d; color: #8b949e; border-radius: 5px; padding: 4px 8px; }
    #project-links { display: none; gap: 8px; align-items: center; padding: 8px 14px; background: #070b10; border-bottom: 1px solid #30363d; overflow-x: auto; }
    #project-links.visible { display: flex; }
    #project-links-label { color: #8b949e; white-space: nowrap; font-family: ui-sans-serif, system-ui, sans-serif; font-size: 12px; }
    .project-link { white-space: nowrap; border: 1px solid #30363d; background: #161b22; color: #d6deeb; border-radius: 5px; padding: 5px 10px; font: inherit; cursor: pointer; }
    .project-link:hover { background: #21262d; border-color: #8b949e; color: #f0f6fc; }
    #activity { display: none; align-items: center; gap: 9px; padding: 7px 14px; background: #0b1016; border-bottom: 1px solid #30363d; color: #8b949e; font-family: ui-sans-serif, system-ui, sans-serif; font-size: 13px; }
    #activity.visible { display: flex; }
    #activity-dot { width: 7px; height: 7px; border-radius: 999px; background: #8b949e; animation: pulse 1.05s ease-in-out infinite; }
    #activity-text { white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
    @keyframes pulse { 0%, 100% { opacity: .35; transform: scale(.8); } 50% { opacity: 1; transform: scale(1.15); } }
    #terminal { overflow: auto; padding: 14px; white-space: pre-wrap; word-break: normal; overflow-wrap: anywhere; line-height: 1.42; font-size: 14px; }
    #entry { display: flex; align-items: flex-start; gap: 8px; padding: 10px 14px; border-top: 1px solid #30363d; background: #0d1117; }
    #prompt { color: #7ee787; padding-top: 7px; }
    textarea { flex: 1; min-height: 38px; max-height: 180px; resize: vertical; background: #05070a; color: #f0f6fc; border: 1px solid #30363d; border-radius: 6px; padding: 8px 10px; font: inherit; outline: none; }
    .ansi-bold { font-weight: 700; }
    .ansi-dim { opacity: .58; }
    .ansi-red { color: #ff7b72; }
    .ansi-green { color: #7ee787; }
    .ansi-yellow { color: #d29922; }
    .ansi-blue { color: #79c0ff; }
    .ansi-magenta { color: #d2a8ff; }
    .ansi-cyan { color: #56d4dd; }
    .ansi-white { color: #f0f6fc; }
    #status { color: #8b949e; font-weight: 400; margin-left: 8px; }
    #status.connected { color: #7ee787; }
    #status.error { color: #ff7b72; }
  </style>
</head>
<body>
  <header>
    <div><strong>DevServer</strong> terminal <span id="status">connecting</span></div>
    <form method="post" action="/logout"><button>Log out</button></form>
  </header>
  <div id="project-links"><span id="project-links-label">Project links</span></div>
  <div id="activity"><span id="activity-dot"></span><span id="activity-text">working</span></div>
  <div id="terminal"></div>
  <div id="entry">
    <div id="prompt">&gt;</div>
    <textarea id="input" spellcheck="false" autocomplete="off"></textarea>
  </div>
  <script>
    const terminal = document.getElementById("terminal");
    const input = document.getElementById("input");
    const status = document.getElementById("status");
    const projectLinks = document.getElementById("project-links");
    const activity = document.getElementById("activity");
    const activityText = document.getElementById("activity-text");
    const socket = new WebSocket(`${location.protocol === "https:" ? "wss" : "ws"}://${location.host}/terminal`);
    let currentClasses = [];
    const knownLinks = new Set();
    let currentProjectLinkLabel = "Project";
    let controlBuffer = "";
    let controlMode = false;
    let atLineStart = true;

    function setStatus(text, cls = "") {
      status.textContent = text;
      status.className = cls;
    }

    function appendText(text) {
      text = processControlLines(text);
      if (!text) return;
      text = captureProjectLinkLabel(text);
      collectProjectLinks(text);
      const parts = text.split(/(\\x1b\\[[0-9;?]*[A-Za-z])/g);
      for (const part of parts) {
        const match = part.match(/^\\x1b\\[([0-9;?]*)([A-Za-z])$/);
        if (match) {
          if (match[2] === "m") applyAnsi(match[1]);
          continue;
        }
        if (!part) continue;
        const span = document.createElement("span");
        span.textContent = part;
        for (const cls of currentClasses) span.classList.add(cls);
        terminal.appendChild(span);
      }
      terminal.scrollTop = terminal.scrollHeight;
    }

    function processControlLines(text) {
      let visible = "";
      const statusPrefix = "DEVSERVER_STATUS:";
      const clearPrefix = "DEVSERVER_STATUS_CLEAR";
      for (const char of text) {
        if (controlMode) {
          controlBuffer += char;
          if (char === "\\n") {
            handleControlLine(controlBuffer.replace(/\\r?\\n$/, ""));
            controlBuffer = "";
            controlMode = false;
            atLineStart = true;
          }
          continue;
        }

        if (controlBuffer) {
          controlBuffer += char;
          if (statusPrefix.startsWith(controlBuffer) || clearPrefix.startsWith(controlBuffer)) {
            continue;
          }
          if (controlBuffer.startsWith(statusPrefix) || controlBuffer.startsWith(clearPrefix)) {
            controlMode = true;
            if (char === "\\n") {
              handleControlLine(controlBuffer.replace(/\\r?\\n$/, ""));
              controlBuffer = "";
              controlMode = false;
              atLineStart = true;
            }
            continue;
          }
          visible += controlBuffer;
          atLineStart = char === "\\n";
          controlBuffer = "";
          continue;
        }

        if (atLineStart && char === "D") {
          controlBuffer = char;
          continue;
        }

        visible += char;
        atLineStart = char === "\\n";
      }
      return visible;
    }

    function handleControlLine(line) {
      if (line.startsWith("DEVSERVER_STATUS:")) {
        setActivity(line.slice("DEVSERVER_STATUS:".length).trim() || "working");
      } else if (line === "DEVSERVER_STATUS_CLEAR") {
        clearActivity();
      }
    }

    function setActivity(text) {
      activityText.textContent = text;
      activity.classList.add("visible");
    }

    function clearActivity() {
      activity.classList.remove("visible");
    }

    function captureProjectLinkLabel(text) {
      return text.replace(/^DEVSERVER_PROJECT_LINK_LABEL:\\s*(.+)$/gm, (_match, label) => {
        currentProjectLinkLabel = label.trim() || "Project";
        return "";
      });
    }

    function collectProjectLinks(text) {
      const cleaned = text.replace(/\\x1b\\[[0-9;?]*[ -/]*[@-~]/g, "");
      const matches = cleaned.match(/https?:\\/\\/[^\\s\\])}>"]+/g) || [];
      for (let url of matches) {
        url = url.replace(/[.,;:]+$/, "");
        url = browserReachableUrl(url);
        if (knownLinks.has(url)) continue;
        knownLinks.add(url);
        addProjectLink(url);
      }
    }

    function browserReachableUrl(url) {
      try {
        const parsed = new URL(url);
        if (parsed.hostname === "127.0.0.1" || parsed.hostname === "localhost") {
          parsed.hostname = location.hostname;
        }
        return parsed.toString();
      } catch {
        return url;
      }
    }

    function addProjectLink(url) {
      projectLinks.classList.add("visible");
      const button = document.createElement("button");
      button.type = "button";
      button.className = "project-link";
      button.textContent = `Open ${currentProjectLinkLabel} ↗`;
      const port = portForUrl(url);
      const portLabel = port ? ` :${port}` : "";
      button.textContent = `Open ${currentProjectLinkLabel}${portLabel} \u2197`;
      button.title = url;
      button.addEventListener("click", () => window.open(url, "_blank", "noopener"));
      projectLinks.appendChild(button);
    }

    function portForUrl(url) {
      try {
        const parsed = new URL(url);
        return parsed.port || (parsed.protocol === "https:" ? "443" : "80");
      } catch {
        return "";
      }
    }

    function labelForUrl(url) {
      try {
        const parsed = new URL(url);
        return `${parsed.hostname}:${parsed.port || (parsed.protocol === "https:" ? "443" : "80")}`;
      } catch {
        return url;
      }
    }

    function applyAnsi(code) {
      if (!code || code === "0") {
        currentClasses = [];
        return;
      }
      for (const item of code.split(";")) {
        if (item === "0") currentClasses = [];
        if (item === "1") currentClasses.push("ansi-bold");
        if (item === "2") currentClasses.push("ansi-dim");
        if (item === "31") currentClasses.push("ansi-red");
        if (item === "32") currentClasses.push("ansi-green");
        if (item === "33") currentClasses.push("ansi-yellow");
        if (item === "34") currentClasses.push("ansi-blue");
        if (item === "35") currentClasses.push("ansi-magenta");
        if (item === "36") currentClasses.push("ansi-cyan");
        if (item === "97") currentClasses.push("ansi-white");
      }
    }

    socket.onopen = () => setStatus("connected", "connected");
    socket.onmessage = event => appendText(event.data);
    socket.onerror = () => {
      setStatus("connection error", "error");
      appendText("\\n[WebSocket connection error.]\\n");
    };
    socket.onclose = () => {
      setStatus("disconnected", "error");
      appendText("\\n[Disconnected from DevServer web terminal.]\\n");
    };

    input.addEventListener("keydown", event => {
      if (event.key === "Enter" && !event.shiftKey) {
        event.preventDefault();
        const value = input.value;
        if (socket.readyState === WebSocket.OPEN) {
          socket.send(value + "\\n");
          appendText(value + "\\n");
        } else {
          appendText("\\n[Web terminal is not connected yet.]\\n");
        }
        input.value = "";
      }
    });

    input.focus();
  </script>
</body>
</html>"""


def main():
    import uvicorn

    host = os.environ.get("DEVSERVER_WEB_HOST", "127.0.0.1")
    port = int(os.environ.get("DEVSERVER_WEB_PORT", "8787"))
    uvicorn.run(app, host=host, port=port)


if __name__ == "__main__":
    main()
