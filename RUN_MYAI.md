# 🐾 MyAi — How to Run & Check Status

## Quick Health Check

Open PowerShell and run this to check if everything is up:

```powershell
# Check bot server
curl http://localhost:8000/health

# Check ngrok tunnel
curl http://127.0.0.1:4040/api/tunnels

# Check Ollama
ollama list
```

**What "healthy" looks like:**
- Bot server returns: `{"status": "ok", "ollama": "connected", "model": "llama3.1:8b"}`
- ngrok shows a `public_url` starting with `https://`
- Ollama lists `llama3.1:8b` and `nomic-embed-text`

---

## Starting MyAi from Scratch

You need **3 PowerShell windows**. Open them all and run these commands in order:

### Window 1 — Ollama (the AI brain)
```powershell
ollama serve
```
> Leave this running. If you see "address already in use", Ollama is already running — that's fine, skip this step.

### Window 2 — MyAi Bot Server
```powershell
cd C:\Users\anubh\Downloads\miai-transfer
.venv\Scripts\activate
python -m app.main
```

**You should see:**
```
🐾  MyAi Agent Started
   Model:    llama3.1:8b
   Server:   http://0.0.0.0:8000
   Webhook:  http://0.0.0.0:8000/api/messages
   Health:   http://0.0.0.0:8000/health
Waiting for Teams messages...
```

### Window 3 — ngrok (tunnel to the internet)
```powershell
ngrok http 8000
```

**You should see:**
```
Session Status    online
Forwarding        https://XXXX.ngrok-free.app -> http://localhost:8000
```

---

## ⚠️ IMPORTANT: Update Azure Endpoint After Every ngrok Restart

Free ngrok gives you a **new URL every time** you restart it. You MUST update Azure:

1. Copy the new `https://XXXX.ngrok-free.app` URL from the ngrok window
2. Go to **Azure Portal** → https://portal.azure.com
3. Search for your **Azure Bot** resource (the one you named for MyAi)
4. Click **Configuration** in the left sidebar
5. Update **Messaging endpoint** to:
   ```
   https://YOUR-NEW-NGROK-URL.ngrok-free.app/api/messages
   ```
6. Click **Apply**

> 💡 **Tip:** Don't restart ngrok unless you have to — the tunnel stays active as long as the window is open.

---

## Stopping MyAi

Just close the 3 PowerShell windows, or press `Ctrl+C` in each one.

---

## Troubleshooting

| Problem | Solution |
|---------|----------|
| `python` not found | Close and reopen PowerShell (PATH needs refresh) |
| Bot server won't start | Make sure venv is activated: `.venv\Scripts\activate` |
| ngrok `ERR_NGROK_108` | You have another ngrok session. Go to https://dashboard.ngrok.com/agents and stop it. |
| Bot not replying in Teams | 1) Check bot server window for errors. 2) Verify ngrok URL matches Azure endpoint. 3) Make sure all 3 windows are running. |
| Ollama errors | Run `ollama list` to verify models are pulled. If not: `ollama pull llama3.1:8b` |
| Slow responses | Normal for 8b model. Responses take 10-30 seconds depending on complexity. |

---

## Your Credentials (for reference)

These are stored in `.env` — don't share them:

| Setting | Where It's Used |
|---------|----------------|
| `MICROSOFT_APP_ID` | Azure Bot identity |
| `MICROSOFT_APP_PASSWORD` | Azure Bot secret |
| `MICROSOFT_APP_TENANT_ID` | Your Azure tenant |

---

## Useful Commands in Teams

| Command | What it does |
|---------|-------------|
| `/help` | Show all commands |
| `/status` | Check bot health & connectivity |
| `/model <name>` | Switch AI model (e.g., `/model qwen2.5:14b`) |
| `/allow <path>` | Grant file access to a folder |
| `/revoke` | Remove all file permissions |
| `/search on` | Enable web search |
| `/search off` | Disable web search |
| `/index <path>` | Index a folder for RAG search |
| `/clear` | Clear conversation history |

---

## One-Liner Status Check

Paste this in PowerShell to check everything at once:

```powershell
Write-Host "`n=== MyAi Status ===" -ForegroundColor Cyan; try { $h = Invoke-RestMethod http://localhost:8000/health -TimeoutSec 3; Write-Host "Bot Server: ✅ $($h.status) | Ollama: $($h.ollama) | Model: $($h.model)" -ForegroundColor Green } catch { Write-Host "Bot Server: ❌ Not running" -ForegroundColor Red }; try { $t = Invoke-RestMethod http://127.0.0.1:4040/api/tunnels -TimeoutSec 3; Write-Host "ngrok:      ✅ $($t.tunnels[0].public_url)" -ForegroundColor Green } catch { Write-Host "ngrok:      ❌ Not running" -ForegroundColor Red }; try { $o = ollama list 2>&1; Write-Host "Ollama:     ✅ Running" -ForegroundColor Green } catch { Write-Host "Ollama:     ❌ Not running" -ForegroundColor Red }
```
