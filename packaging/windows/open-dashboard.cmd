@echo off
rem The installer's last step. Waits until the dashboard actually answers before the
rem browser opens - the two services need a few seconds after "start", and a page
rem opened onto "connection refused" is the first thing a new user would see otherwise.
rem Gives up after 90 seconds and opens the page anyway; the dashboard shows its own
rem "checking this installation" state while it comes up.
powershell.exe -NoProfile -ExecutionPolicy Bypass -Command ^
  "for ($i = 0; $i -lt 90; $i++) { try { Invoke-WebRequest -UseBasicParsing -Uri 'http://127.0.0.1:38471/' -TimeoutSec 2 | Out-Null; break } catch { Start-Sleep -Seconds 1 } }"
start "" "http://localhost:38471"
