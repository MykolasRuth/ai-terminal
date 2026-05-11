# SSH Setup Notes

Use these steps to enable SSH into this Windows PC so you can run the Ollama CLI from another computer.

## Check OpenSSH Server

Open PowerShell as Administrator:

```powershell
Get-WindowsCapability -Online | Where-Object Name -like 'OpenSSH.Server*'
```

If the state is `Installed`, continue to starting the service.

If the state is `NotPresent`, install it:

```powershell
Add-WindowsCapability -Online -Name OpenSSH.Server~~~~0.0.1.0
```

## If Install Gets Stuck

Restart the PC first. Use restart, not shutdown:

```powershell
Restart-Computer
```

After reboot, check again:

```powershell
Get-WindowsCapability -Online | Where-Object Name -like 'OpenSSH.Server*'
```

If it is still stuck as `NotPresent` or `InstallPending`, run:

```powershell
DISM /Online /Cleanup-Image /RestoreHealth
sfc /scannow
```

Then restart again and try the install command once more.

## Start SSH

```powershell
Start-Service sshd
Set-Service -Name sshd -StartupType Automatic
Get-Service sshd
```

The service should show `Running`.

## Allow SSH Through Firewall

```powershell
New-NetFirewallRule -Name sshd -DisplayName "OpenSSH Server" -Enabled True -Direction Inbound -Protocol TCP -Action Allow -LocalPort 22
```

## Find This PC's IP Address

```powershell
ipconfig
```

Look for the IPv4 address on your active network adapter, such as:

```text
192.168.1.50
```

## SSH From Laptop

From the laptop:

```bash
ssh your-windows-username@your-pc-ip
```

Example:

```bash
ssh mykol@192.168.1.50
```

## Run The Ollama CLI Over SSH

Once connected:

```powershell
cd C:\Users\mykol\Projects\ai-terminal
.\venv\Scripts\activate
python ollama_cli.py
```

Ollama needs to be running on this PC. The script talks to:

```text
http://localhost:11434/api/chat
```

When connected over SSH, `localhost` means this PC.

## Security Note

Start by using SSH only on your home network. Do not port-forward SSH from your router to the public internet until you set up SSH keys and disable password login.
