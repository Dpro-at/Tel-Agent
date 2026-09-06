@echo off
rem The dashboard service's start command: the Next.js standalone server on the
rem bundled Node, loopback only. Mirrors packaging/linux/tel-agent-web.service and
rem the macOS web plist - same port, same host, same working directory shape.
setlocal
cd /d "%~dp0web-app"
set NODE_ENV=production
set HOSTNAME=127.0.0.1
set PORT=38471
"%~dp0node\node.exe" web\server.js
