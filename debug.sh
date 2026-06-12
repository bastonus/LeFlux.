#!/bin/bash
# Script de diagnostic pour Frenchio Stack
# Usage : sudo ./debug.sh

echo "=== [1] STATUS DES CONTENEURS ==="
docker ps -a --format "table {{.Names}}\t{{.Status}}\t{{.Ports}}"

echo -e "\n=== [2] LOGS CADDY (PROXIE ET ERREURS) ==="
docker logs frenchio-caddy --tail 30 2>&1


echo -e "\n=== [4] LOGS TUNNEL CLOUDFLARE ==="
docker logs frenchio-tunnel --tail 30 2>&1

echo -e "\n=== [5] LOGS FRENCHIO-ADDON ==="
docker logs frenchio-addon --tail 30 2>&1

echo -e "\n=== [6] LOGS QBITTORRENT ==="
docker logs frenchio-qbittorrent --tail 30 2>&1

# Load domain dynamically from .env in frenchio-stack
STACK_ENV="$(dirname "$0")/frenchio-stack/.env"
if [ -f "$STACK_ENV" ]; then
    DOMAIN=$(grep -E "^DOMAIN=" "$STACK_ENV" | cut -d= -f2 | tr -d '\r')
fi
DOMAIN=${DOMAIN:-yourdomain.com}

echo -e "\n=== [7] TEST LOCAL CADDY (PORT 8082) ==="
curl -I -H "Host: $DOMAIN" http://localhost:8082/
curl -I -H "Host: $DOMAIN" http://localhost:8082/manifest.json


