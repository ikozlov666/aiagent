#!/bin/bash
set -e

echo "🚀 Starting AI Agent Sandbox..."
echo "   Display: $DISPLAY"
echo "   Workspace: /workspace"

# Create log directory
mkdir -p /var/log

# Start all services via supervisor
exec /usr/bin/supervisord -c /etc/supervisor/conf.d/supervisord.conf
