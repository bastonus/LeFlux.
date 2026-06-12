#!/bin/bash

# ==========================================================
# 🚀 Frenchio Stack - Lancement & Installation Stremio Web
# ==========================================================
# Usage : sudo ./start.sh

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[0;33m'
CYAN='\033[0;36m'
BOLD='\033[1m'
NC='\033[0m'

echo -e "${CYAN}${BOLD}"
echo -e "  ███████╗██████╗ ███████╗███╗   ██╗ ██████╗██╗  ██╗██╗ ██████╗ "
echo -e "  ██╔════╝██╔══██╗██╔════╝████╗  ██║██╔════╝██║  ██║██║██╔═══██╗"
echo -e "  █████╗  ██████╔╝█████╗  ██╔██╗ ██║██║     ███████║██║██║   ██║"
echo -e "  ██╔══╝  ██╔══██╗██╔══╝  ██║╚██╗██║██║     ██╔══██║██║██║   ██║"
echo -e "  ██║     ██║  ██║███████╗██║ ╚████║╚██████╗██║  ██║██║╚██████╔╝"
echo -e "  ╚═╝     ╚═╝  ╚═╝╚══════╝╚═╝  ╚═══╝ ╚═════╝╚═╝  ╚═╝╚═╝ ╚═════╝ "
echo -e "${NC}"
echo -e "${CYAN}  🎬 Stremio Web HTTPS — Lancement automatique${NC}"
echo -e "${CYAN}  ============================================${NC}\n"

# ── 1. Vérification root ──────────────────────────────────
if [ "$EUID" -ne 0 ]; then
    echo -e "${RED}❌ Ce script doit être lancé avec sudo.${NC}"
    echo -e "${YELLOW}   Exemple : ${GREEN}sudo ./start.sh${NC}"
    exit 1
fi

REAL_USER=${SUDO_USER:-$USER}
REAL_UID=$(id -u "$REAL_USER")
REAL_GID=$(id -g "$REAL_USER")
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"

# ── 1b. Installation automatique d'OpenSSH Server si nécessaire ──
if ! dpkg -l | grep -q openssh-server; then
    echo -e "⏳ Installation d'openssh-server..."
    apt-get update && apt-get install -y openssh-server
fi


echo -e "👤 Utilisateur : ${CYAN}$REAL_USER${NC}"
cd "$SCRIPT_DIR"

# ── 2. Arrêt des anciens conteneurs (très important avant d'écrire la config) ────
echo -e "⏳ Arrêt des anciens conteneurs..."
docker compose -f frenchio-stack/docker-compose.yml down &>/dev/null

# ── 3. Création des répertoires ───────────────────────────
echo -e "⏳ Création des répertoires..."
mkdir -p frenchio-stack/config
mkdir -p frenchio-stack/templates
mkdir -p frenchio-stack/qb_config/qBittorrent
mkdir -p frenchio-stack/downloads/incomplete
mkdir -p frenchio-stack/joal_data

# ── 3d. JOAL UI auto-configuration injection ─────────────
echo -e "⏳ Préparation de l'auto-configuration de JOAL WebUI..."
mkdir -p frenchio-stack/joal_ui
if [ ! -f frenchio-stack/joal_ui/index.html ]; then
    docker run --rm --entrypoint cat anthonyraymond/joal:latest /joal/joal.jar > temp_joal.jar 2>/dev/null
    python3 -c "import zipfile; zipfile.ZipFile('temp_joal.jar').extract('BOOT-INF/classes/public/index.html', 'frenchio-stack/joal_ui')" 2>/dev/null
    mv frenchio-stack/joal_ui/BOOT-INF/classes/public/index.html frenchio-stack/joal_ui/index.html 2>/dev/null
    rm -rf frenchio-stack/joal_ui/BOOT-INF temp_joal.jar
fi

python3 -c '
import os
path = "frenchio-stack/joal_ui/index.html"
if os.path.exists(path):
    with open(path, "r", encoding="utf-8") as f:
        html = f.read()
    script = "<script>!function(){var config={host:window.location.hostname,port:window.location.port||(window.location.protocol===\"https:\"?\"443\":\"80\"),pathPrefix:\"joal-secret\",secretToken:\"joal-secret-key\"};localStorage.setItem(\"guiConfig\",JSON.stringify(config))}()</script>"
    if "guiConfig" not in html:
        html = html.replace("<head>", "<head>" + script)
        with open(path, "w", encoding="utf-8") as f:
            f.write(html)
'

# ── 3c. qBittorrent : pré-configuration ───────────────────
echo -e "⏳ Configuration qBittorrent (admin/adminadmin)..."
cat << 'QBEOF' > frenchio-stack/qb_config/qBittorrent/qBittorrent.conf
[AutoRun]
enabled=false
program=

[BitTorrent]
Session\AddTorrentStopped=false
Session\DefaultSavePath=/downloads/
Session\MaxActiveDownloads=20
Session\MaxActiveTorrents=50
Session\MaxActiveUploads=0
Session\Port=6881
Session\QueueingSystemEnabled=true
Session\SSL\Port=63292
Session\ShareLimitAction=Stop
Session\ShareLimitMode=0
Session\ShareRatioLimit=0
Session\TempPath=/downloads/incomplete/

[LegalNotice]
Accepted=true

[Meta]
MigrationVersion=8

[Network]
Cookies=@Invalid()
PortForwardingEnabled=false
Proxy\HostnameLookupEnabled=false
Proxy\Profiles\BitTorrent=false
Proxy\Profiles\Misc=false
Proxy\Profiles\RSS=false

[Preferences]
Connection\PortRangeMin=6881
Connection\UPnP=true
Connection\Proxy\IP=
Connection\Proxy\Port=0
Connection\Proxy\Type=-1
Connection\Proxy\OnlyForTorrents=false
Connection\Proxy\Authentication=false
Connection\Proxy\Username=
Connection\Proxy\Password=
Connection\Proxy\PeerConnections=false
Connection\Proxy\Torrenting=false
Downloads\SavePath=/downloads/
Downloads\TempPath=/downloads/incomplete/
WebUI\Address=*
WebUI\ServerDomains=*
WebUI\CSRFProtection=false
WebUI\ClickjackingProtection=false
WebUI\HostHeaderValidation=false
WebUI\AuthSubnetWhitelistEnabled=true
WebUI\AuthSubnetWhitelist=172.16.0.0/12, 192.168.0.0/16, 10.0.0.0/8, 127.0.0.1/32
WebUI\Username=admin
WebUI\Password_PBKDF2="@ByteArray(ARQ77eY1NUZaQsuDHbIMCA==:0WMRkYTUWVT9wVvdDtHAjU9b3b7uB8NR1Gur2hmQCvCDpm39Q+PsJRJPaCU51dEiz+dTzh8qbPsL8WkFljQYFQ==)"
QBEOF

# ── 4. Permissions ────────────────────────────────────────
echo -e "⏳ Application des permissions..."
chown -R "$REAL_UID":"$REAL_GID" frenchio-stack
chmod -R 775 frenchio-stack

# ── 5. Génération du lien Stremio en base64 ───────────────
echo -e "⏳ Génération du lien HTTPS pour Stremio Web..."

CONFIG_B64=$(python3 - << 'PYEOF'
import base64, json, os
config_path = "frenchio-stack/config/config.json"
config = None
if os.path.exists(config_path):
    try:
        with open(config_path, "r", encoding="utf-8") as f:
            config = json.load(f)
    except Exception as e:
        pass

if not config:
    config = {
        "tmdb_key": "",
        "c411_apikey": "",
        "torr9_passkey": "",
        "qbittorrent": {
            "host": "http://frenchio-qbittorrent:8080",
            "username": "admin",
            "password": "adminadmin",
            "public_url": "http://localhost:8082/downloads"
        }
    }
print(base64.b64encode(json.dumps(config, separators=(',', ':')).encode()).decode())
PYEOF
)

# Extraire DOMAIN, TUNNEL_TOKEN et MANIFEST_TOKEN existants pour ne pas les perdre
DOMAIN=""
TUNNEL_TOKEN=""
MANIFEST_TOKEN=""
if [ -f frenchio-stack/.env ]; then
    DOMAIN=$(grep '^DOMAIN=' frenchio-stack/.env | cut -d'=' -f2- | tr -d '\r')
    TUNNEL_TOKEN=$(grep '^TUNNEL_TOKEN=' frenchio-stack/.env | cut -d'=' -f2- | tr -d '\r')
    MANIFEST_TOKEN=$(grep '^MANIFEST_TOKEN=' frenchio-stack/.env | cut -d'=' -f2- | tr -d '\r')
fi
DOMAIN=${DOMAIN:-yourdomain.com}
if [ -z "$MANIFEST_TOKEN" ]; then
    MANIFEST_TOKEN="03aff957d44cf5f459a70fa3112a20bb2578abd79d7aac8e44533d9dcaacaabe"
fi

# Réécrire le fichier .env de la stack pour que Docker Compose et Caddy y accèdent
cat << ENVEOF > frenchio-stack/.env
# Configuration de la stack Frenchio
DOMAIN=${DOMAIN}
TUNNEL_TOKEN=${TUNNEL_TOKEN}
CONFIG_B64=${CONFIG_B64}
MANIFEST_TOKEN=${MANIFEST_TOKEN}
ENVEOF

# Frenchio addon est maintenant servi sous /addon/
MANIFEST_URL="https://${DOMAIN}/addon/${CONFIG_B64}/manifest.json"
MANIFEST_URL_STATIC="https://${DOMAIN}/manifest/${MANIFEST_TOKEN}/manifest.json"
CONFIGURE_URL="https://${DOMAIN}/frenchio"
NUVIO_PORTAL_URL="https://${DOMAIN}/"
NUVIO_WEB_APP="https://web.nuvioapp.space/"

# ── 6. Lancement Docker ───────────────────────────────────
echo -e "⏳ Démarrage de la stack (Frenchio + qBittorrent + Caddy + Cloudflare)..."
docker compose -f frenchio-stack/docker-compose.yml up -d --remove-orphans

if [ $? -ne 0 ]; then
    echo -e "${RED}❌ Erreur Docker Compose. Vérifiez avec : docker compose -f frenchio-stack/docker-compose.yml logs${NC}"
    exit 1
fi

# ── 7. Attente que le tunnel soit actif ───────────────────
echo -e "⏳ Attente que le tunnel Cloudflare soit opérationnel..."
sleep 5

# Vérifier que le tunnel répond en HTTPS
for i in $(seq 1 15); do
    HTTP_CODE=$(curl -s -o /dev/null -w "%{http_code}" --max-time 3 "${MANIFEST_URL_STATIC}" 2>/dev/null)
    if [ "$HTTP_CODE" = "200" ]; then
        echo -e "${GREEN}✅ Tunnel Cloudflare actif — HTTPS opérationnel !${NC}"
        break
    fi
    echo -ne "   Vérification tunnel $i/15 (code HTTP: $HTTP_CODE)...\r"
    sleep 3
done

if [ "$HTTP_CODE" != "200" ]; then
    echo -e "${YELLOW}⚠️  Le tunnel n'a pas encore répondu. Patientez 30 secondes et réessayez.${NC}"
fi

# ── 8. Sauvegarde des liens ───────────────────────────────
LINK_FILE="$SCRIPT_DIR/stremio_link.txt"
cat > "$LINK_FILE" << LINKEOF
# ============================================================
# Frenchio — Liens générés le $(date)
# ============================================================

[1] PORTAL CENTRALISÉ LEFLUX. (Mot de passe: frenchio_admin_123) :
${NUVIO_PORTAL_URL}

[2] APP LECTEUR WEB NUVIO (Ajouter l'addon manifest ci-dessous) :
${NUVIO_WEB_APP}

[3] MANIFEST FRENCHIO STATIQUE (Recommandé - Ne change jamais si vos clés changent) :
${MANIFEST_URL_STATIC}

[4] MANIFEST FRENCHIO DYNAMIQUE (Contient la config encodée) :
${MANIFEST_URL}

[5] CONFIGURATION FRENCHIO :
${CONFIGURE_URL}

[6] WebUI qBittorrent (externe, accessible partout) :
https://${DOMAIN}/qbittorrent/  (admin / adminadmin)

[7] WebUI JOAL (externe, accessible partout) :
https://${DOMAIN}/joal-secret/ui/
LINKEOF
chown "$REAL_UID":"$REAL_GID" "$LINK_FILE"

# ── 9. Affichage du résumé ────────────────────────────────
echo -e "\n${GREEN}${BOLD}=========================================================="
echo -e "  ✅ PORTAL LEFLUX. OPÉRATIONNEL !"
echo -e "==========================================================${NC}\n"

echo -e "${CYAN}${BOLD}🌐 PORTAL CENTRALISÉ LEFLUX. :${NC}"
echo -e "   ${GREEN}${BOLD}https://${DOMAIN}${NC}"
echo -e "   ${YELLOW}→ Accédez à tous vos services (Nuvio Web, qBittorrent, Joal) avec le mot de passe !${NC}\n"

echo -e "${CYAN}${BOLD}🎬 ADDON FRENCHIO — URL manifest (Statique - Recommandé) :${NC}"
echo -e "   ${GREEN}${MANIFEST_URL_STATIC}${NC}"
echo -e "   ${YELLOW}→ Utilisez ce lien dans Stremio : il ne changera jamais si vous modifiez vos clés !${NC}\n"

echo -e "${CYAN}${BOLD}🎬 ADDON FRENCHIO — URL manifest (Dynamique) :${NC}"
echo -e "   ${GREEN}${MANIFEST_URL}${NC}\n"

echo -e "${CYAN}${BOLD}⚙️  Page de configuration Frenchio :${NC}"
echo -e "   ${GREEN}${CONFIGURE_URL}${NC}\n"

echo -e "${CYAN}${BOLD}🐳 WebUI qBittorrent :${NC}"
echo -e "   ${GREEN}http://localhost:8080${NC}  (Local - admin / adminadmin)"
echo -e "   ${GREEN}https://${DOMAIN}/qbittorrent/${NC}  (Externe - admin / adminadmin)\n"

echo -e "${CYAN}${BOLD}📈 WebUI JOAL (Ratio Master) :${NC}"
echo -e "   ${GREEN}https://${DOMAIN}/joal-secret/ui/${NC} (Externe)\n"

echo -e "📄 Tous les liens sauvegardés dans : ${YELLOW}stremio_link.txt${NC}\n"


echo -e "\n${CYAN}${BOLD}=========================================================="
echo -e "  🎬 Bonne séance sur Nuvio !"
echo -e "==========================================================${NC}\n"
