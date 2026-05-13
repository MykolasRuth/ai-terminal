# Starting DevServer AI Terminal

This project runs as a local Python terminal app that talks to Ollama at
`http://localhost:11434`.

## Requirements

- Python installed
- Ollama installed and running
- At least one local Ollama model installed

## Start Ollama

Open Ollama from the Start menu, or run:

```powershell
ollama serve
```

If you do not have a model yet, pull one:

```powershell
ollama pull phi4-mini
```

## Start The Terminal

From PowerShell:

```powershell
cd C:\Users\devserver\Projects\ai-terminal
.\venv\Scripts\Activate.ps1
python ollama_cli.py
```

Show CLI help without starting Ollama or the terminal loop:

```powershell
python ollama_cli.py --help
python ollama_cli.py --version
```

If dependencies are missing, install them once:

```powershell
pip install -r requirements.txt
```

Then run the terminal again:

```powershell
python ollama_cli.py
```

## Run With Docker On Mac

This is useful when you want to run DevServer AI Terminal from a MacBook without
setting up the Python environment directly on macOS.

First, make sure Ollama is running on the Mac:

```bash
ollama serve
```

Build the image from this repository:

```bash
docker build -t devserver-ai-terminal .
```

Run it with your Mac `Projects` folder mounted into the container:

```bash
docker run --rm -it \
  -e OLLAMA_BASE_URL=http://host.docker.internal:11434 \
  -e DEVSERVER_START_CWD=/projects \
  -e DEVSERVER_PROJECTS_DIR=/projects \
  -v "$HOME/Projects:/projects" \
  devserver-ai-terminal
```

Or use Docker Compose:

```bash
docker compose run --rm devserver
```

Inside Docker, `/projects` is your mounted projects folder. The terminal starts
there, and project discovery also points there.

## Run Browser DevServer

Browser mode exposes the same terminal app through a password-protected web page.
The browser is only the interface; tools and file access still happen on the
machine running DevServer.

Set the browser login from SSH or a local server shell:

```powershell
python -m ai_terminal.web_auth
```

Then start the web server:

```powershell
python ollama_cli.py web
```

By default it listens only on localhost:

```text
http://127.0.0.1:8787
```

To access it from another device on Tailscale, bind it to the server network:

```powershell
$env:DEVSERVER_WEB_HOST="0.0.0.0"
$env:DEVSERVER_WEB_PORT="8787"
python ollama_cli.py web
```

Then open this from your laptop:

```text
http://<server-tailscale-name-or-ip>:8787
```

Do not put the web server directly on the public internet. Keep it behind
Tailscale or another private network.

## Run Browser DevServer At Startup

After creating at least one browser account, install the scheduled task:

```powershell
cd C:\Users\devserver\Projects\ai-terminal
.\scripts\install-web-task.ps1
```

This starts the browser DevServer on:

```text
http://<server-tailscale-name-or-ip>:8787
```

The startup task uses:

```text
Host: 0.0.0.0
Port: 8787
Projects: C:\Users\devserver\Projects
Ollama: http://localhost:11434
```

If Windows blocks startup tasks without elevation, open PowerShell as
Administrator and run the same install command. If you only need it after the
`devserver` user logs in, install it as a logon task instead:

```powershell
.\scripts\install-web-task.ps1 -AtLogon
```

Check task status:

```powershell
Get-ScheduledTask -TaskName "DevServer AI Terminal Web"
```

Logs are written here:

```text
C:\Users\devserver\Projects\ai-terminal\data\logs\web.out.log
```

Remove the startup task:

```powershell
.\scripts\uninstall-web-task.ps1
```

With Docker Compose, provide credentials from the server shell and publish the
web port:

```bash
DEVSERVER_WEB_USER=devserver \
DEVSERVER_WEB_PASSWORD='choose-a-password' \
docker compose up devserver-web
```

## Useful Commands

Inside the terminal, you can use:

```text
/tools          Show available tools
/models         Show local Ollama models
/model          Switch the active model
/orchestrator   Switch the routing model
/account        Manage browser login accounts
/doctor         Diagnose DevServer, Ollama reachability, VS Code CLI, tests, and git status
/clear          Clear chat history
/history        Show chat history
/tool-log       Show tool activity
/bye            Exit
```

Browser account commands:

```text
/account list
/account create "username" "password"
/account delete "username"
/account passwd "username" "new-password"
```

You can also ask in plain English, for example:

```text
list files in this folder
show git status
read ARCHITECTURE.md
check devserver
```

## Troubleshooting

If you see:

```text
Could not reach Ollama at http://localhost:11434.
```

Start Ollama, then run:

```powershell
python ollama_cli.py
```

If PowerShell blocks activation, use:

```powershell
Set-ExecutionPolicy -Scope CurrentUser RemoteSigned
.\venv\Scripts\Activate.ps1
```
