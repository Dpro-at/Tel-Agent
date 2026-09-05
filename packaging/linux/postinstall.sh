#!/bin/sh
# deb postinst / rpm %post - create the service user, generate the key if none,
# then enable and start both services.
set -eu

if ! getent passwd telagent >/dev/null; then
    useradd --system --home-dir /var/lib/tel-agent --shell /usr/sbin/nologin telagent
fi

mkdir -p /var/lib/tel-agent
chown telagent:telagent /var/lib/tel-agent
chmod 750 /var/lib/tel-agent

# Installation secrets are root-owned; the service reads them through systemd's
# EnvironmentFile, which runs as root before dropping to the service user.
chown root:root /etc/tel-agent/tel-agent.env
chmod 600 /etc/tel-agent/tel-agent.env

# Generate ENCRYPTION_KEY on first install only - an empty value in the template
# marks "never configured", and an upgrade must never touch an existing key.
if grep -q '^ENCRYPTION_KEY=$' /etc/tel-agent/tel-agent.env; then
    KEY="$(/opt/tel-agent/python/bin/python3 -c 'from api.security.crypto import generate_key; print(generate_key())')"
    sed -i "s/^ENCRYPTION_KEY=$/ENCRYPTION_KEY=${KEY}/" /etc/tel-agent/tel-agent.env
    echo "***********************************************************************"
    echo "Tel-Agent generated an ENCRYPTION_KEY in /etc/tel-agent/tel-agent.env."
    echo "BACK IT UP somewhere that is NOT the database backup - losing it makes"
    echo "every credential stored in the dashboard unrecoverable."
    echo "***********************************************************************"
fi

if [ -d /run/systemd/system ]; then
    systemctl daemon-reload
    systemctl enable --now tel-agent-api.service tel-agent-web.service
    echo "Tel-Agent is starting: dashboard on http://localhost:3000 (loopback only)."
else
    echo "systemd is not running; start the services yourself when it is:"
    echo "  systemctl enable --now tel-agent-api tel-agent-web"
fi
