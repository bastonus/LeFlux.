#!/bin/bash

# ==========================================================
# 🚀 Installateur Automatique Debian 13 : Stremio + Frenchio + qBittorrent
# ==========================================================

# Couleurs pour l'affichage
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[0;33m'
CYAN='\033[0;36m'
NC='\033[0m' # Pas de couleur

echo -e "${CYAN}==========================================================${NC}"
echo -e "${CYAN}🚀 Installateur Frenchio Stack (Debian 13) - Sans Débrid${NC}"
echo -e "${CYAN}==========================================================${NC}\n"

# 1. Vérification de l'utilisateur root/sudo
if [ "$EUID" -ne 0 ]; then
    echo -e "${YELLOW}ℹ️ Ce script nécessite des privilèges superutilisateur (sudo) pour installer Docker et configurer les dossiers.${NC}"
    echo -e "${YELLOW}Veuillez exécuter avec : sudo $0${NC}"
    exit 1
fi

# Récupérer l'utilisateur non-root qui a lancé sudo pour les permissions UID/GID
REAL_USER=${SUDO_USER:-$USER}
REAL_UID=$(id -u "$REAL_USER")
REAL_GID=$(id -g "$REAL_USER")

# S'assurer que le script tourne dans le dossier contenant le script
cd "$(dirname "$0")"

# 2. Vérification / Installation de Docker
if ! command -v docker &> /dev/null; then
    echo -e "${YELLOW}⏳ Docker n'est pas détecté. Installation de Docker et Docker Compose...${NC}"
    apt-get update
    apt-get install -y ca-certificates curl gnupg docker.io docker-compose-v2
    if [ $? -eq 0 ]; then
        echo -e "${GREEN}✅ Docker et Docker Compose installés avec succès !${NC}"
    else
        echo -e "${RED}❌ Erreur : Impossible d'installer Docker automatiquement.${NC}"
        echo -e "Installez docker manuellement (apt install docker.io docker-compose-v2) et relancez le script."
        exit 1
    fi
else
    echo -e "${GREEN}✅ Docker est déjà installé.${NC}"
fi

# S'assurer que le service Docker tourne
systemctl enable --now docker

# 3. Paramétrage par défaut (Lancement local par défaut)
PORT=${PORT:-"8082"}
DOMAIN=${DOMAIN:-"localhost:$PORT"}
TUNNEL_TOKEN=${TUNNEL_TOKEN:-""}

# 4. Création de la structure des dossiers
echo -e "\n${YELLOW}⏳ Création de l'environnement de travail...${NC}"
INSTALL_DIR="frenchio-stack"
mkdir -p "$INSTALL_DIR/config"
mkdir -p "$INSTALL_DIR/qb_config"
mkdir -p "$INSTALL_DIR/downloads"

# Définition des permissions (important pour qBittorrent et Caddy)
echo -e "⏳ Configuration des permissions pour l'utilisateur ${CYAN}$REAL_USER${NC} (UID: $REAL_UID, GID: $REAL_GID)..."
chown -R "$REAL_UID":"$REAL_GID" "$INSTALL_DIR"
chmod -R 775 "$INSTALL_DIR"

# 5. Génération du fichier d'environnement local .env
echo -e "⏳ Génération du fichier .env..."
cat << EOF > "$INSTALL_DIR/.env"
# Machine-specific environment configuration
DOMAIN=$DOMAIN
TUNNEL_TOKEN=$TUNNEL_TOKEN
EOF

# 6. Génération du Caddyfile
echo -e "⏳ Génération du Caddyfile..."
cat << EOF > "$INSTALL_DIR/Caddyfile"
# ==========================================================
# 🚀 Caddy Configuration : Frenchio + qBittorrent Stack
# ==========================================================

# Le domaine est lu à partir de la variable d'environnement DOMAIN
{\$DOMAIN} {
    log {
        output stdout
        format console
    }

    # CORS Headers pour Stremio
    header {
        Access-Control-Allow-Origin "*"
        Access-Control-Allow-Methods "GET, POST, OPTIONS"
        Access-Control-Allow-Headers "DNT,User-Agent,X-Requested-With,If-Modified-Since,Cache-Control,Content-Type,Range"
        Access-Control-Expose-Headers "Content-Length,Content-Range"
    }

    # Serveur de fichiers de téléchargement
    handle_path /downloads* {
        root * /downloads
        file_server browse
    }

    # Redirection vers l'addon Frenchio
    handle {
        reverse_proxy frenchio:7777
    }
}
EOF

# 7. Génération du docker-compose.yml
echo -e "⏳ Génération du docker-compose.yml..."
cat << EOF > "$INSTALL_DIR/docker-compose.yml"
services:
  frenchio:
    image: ghcr.io/aymene69/frenchio:latest
    container_name: frenchio-addon
    restart: unless-stopped
    environment:
      - PORT=7777
    volumes:
      - ./config:/app/config
    depends_on:
      - qbittorrent

  qbittorrent:
    image: lscr.io/linuxserver/qbittorrent:latest
    container_name: frenchio-qbittorrent
    environment:
      - PUID=$REAL_UID
      - PGID=$REAL_GID
      - TZ=Europe/Paris
      - WEBUI_PORT=8080
    volumes:
      - ./qb_config:/config
      - ./downloads:/downloads
    ports:
      - "8080:8080"
      - "6881:6881"
      - "6881:6881/udp"
    restart: unless-stopped

  caddy:
    image: caddy:alpine
    container_name: frenchio-caddy
    restart: unless-stopped
    ports:
      - "$PORT:$PORT"
      - "80:80"
      - "443:443"
    environment:
      - DOMAIN=\${DOMAIN:-localhost:7777}
    volumes:
      - ./Caddyfile:/etc/caddy/Caddyfile:ro
      - ./downloads:/downloads:ro
      - caddy_data:/data
      - caddy_config:/config
    depends_on:
      - frenchio

  tunnel:
    image: cloudflare/cloudflared:latest
    container_name: frenchio-tunnel
    restart: unless-stopped
    command: tunnel run --token \${TUNNEL_TOKEN}
    depends_on:
      - caddy

volumes:
  caddy_data:
  caddy_config:
EOF

# Réajuster les permissions sur les fichiers créés
chown -R "$REAL_UID":"$REAL_GID" "$INSTALL_DIR/.env" "$INSTALL_DIR/Caddyfile" "$INSTALL_DIR/docker-compose.yml"

# 8. Lancement de Docker Compose
echo -e "\n${GREEN}🚀 Démarrage de la stack Docker Frenchio...${NC}"
cd "$INSTALL_DIR"

# Supprimer le service tunnel si aucun token n'est fourni
if [ -z "$TUNNEL_TOKEN" ]; then
    grep -v 'tunnel:' docker-compose.yml | \
    awk '/image: cloudflare\/cloudflared/{found=1} found && /^  [a-z]/{found=0} !found' > docker-compose.yml.tmp && \
    mv docker-compose.yml.tmp docker-compose.yml
fi

# Lancer compose en tant qu'utilisateur réel (le plugin compose vit dans son $HOME)
sudo -u "$REAL_USER" docker compose down &> /dev/null
sudo -u "$REAL_USER" docker compose up -d --remove-orphans

if [ $? -eq 0 ]; then
    # Récupérer le protocole et l'URL publique
    if [[ "$DOMAIN" == *":"* || "$DOMAIN" == "localhost" ]]; then
        PROTO="http"
    else
        PROTO="https"
    fi
    PUBLIC_BASE_URL="$PROTO://$DOMAIN"
    
    echo -e "\n=========================================================="
    echo -e "${GREEN}✅ INSTALLATION RÉUSSIE SUR DEBIAN 13 !${NC}"
    echo -e "==========================================================\n"
    echo -e "L'addon et les services sont maintenant démarrés.\n"
    
    # Récupérer le mot de passe temporaire qBittorrent généré par linuxserver
    echo -e "${YELLOW}🔑 Récupération du mot de passe qBittorrent WebUI :${NC}"
    echo -e "Veuillez patienter 5 secondes que le conteneur démarre pour lire les logs..."
    sleep 5
    QBIT_LOG_PASS=$(sudo -u "$REAL_USER" docker logs frenchio-qbittorrent 2>&1 | grep "password is:")
    if [ -n "$QBIT_LOG_PASS" ]; then
        echo -e "${GREEN}👉 $QBIT_LOG_PASS${NC}"
    else
        echo -e "${YELLOW}👉 Si c'est la première installation ou s'il y a déjà un fichier config, connectez-vous avec vos identifiants existants.${NC}"
        echo -e "   Le nom d'utilisateur par défaut est : admin (si réinitialisé, le mot de passe est : adminadmin)${NC}"
    fi
    echo -e ""
    
    echo -e "${CYAN}🌍 ADRESSES D'ACCÈS DEPUIS CETTE MACHINE :${NC}"
    echo -e "1. Interface Frenchio (Configuration) : ${GREEN}$PUBLIC_BASE_URL/configure${NC}"
    echo -e "2. WebUI qBittorrent (Gestion)        : ${GREEN}http://localhost:8080${NC}"
    echo -e "3. Serveur de fichiers direct         : ${GREEN}$PUBLIC_BASE_URL/downloads/${NC}"
    echo -e "\n=========================================================="
    echo -e "${YELLOW}👉 ÉTAPES DE CONFIGURATION DANS L'INTERFACE DU NAVIGATEUR :${NC}"
    echo -e "=========================================================="
    echo -e "1. Ouvrez l'interface Frenchio : $PUBLIC_BASE_URL/configure"
    echo -e "2. Activez l'option ${GREEN}qBittorrent${NC} et remplissez :"
    echo -e "   - Host / URL : ${CYAN}http://frenchio-qbittorrent:8080${NC}"
    echo -e "   - Username   : ${CYAN}admin${NC}"
    echo -e "   - Password   : ${CYAN}[Votre mot de passe qBittorrent (ex: adminadmin)]${NC}"
    echo -e "   - Public URL : ${CYAN}$PUBLIC_BASE_URL/downloads${NC}"
    echo -e "3. Désactivez ou laissez vide la section Débrideur (RealDebrid, Alldebrid)."
    echo -e "4. Renseignez vos identifiants/passkeys pour les sources (C411, Torr9, etc.)."
    echo -e "5. Cliquez sur ${GREEN}Install${NC} ou copiez le lien généré dans Stremio."
    echo -e "=========================================================="
else
    echo -e "${RED}❌ Erreur : Échec lors du lancement de la stack Docker.${NC}"
    exit 1
fi
EOF
