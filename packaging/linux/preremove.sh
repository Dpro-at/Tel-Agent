#!/bin/sh
# deb prerm / rpm %preun - stop the services before the files go. Data in
# /var/lib/tel-agent and the key in /etc/tel-agent survive removal on purpose:
# conversations and the key that unlocks stored credentials are the operator's,
# not the package's.
set -eu
if [ -d /run/systemd/system ]; then
    systemctl disable --now tel-agent-api.service tel-agent-web.service || true
fi
