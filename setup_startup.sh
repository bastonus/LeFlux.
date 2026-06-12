#!/bin/bash

# ==========================================================
# 🚀 Frenchio - Configuration du Lancement Automatique
# ==========================================================

# Vérification superutilisateur (sudo)
if [ "$EUID" -ne 0 ]; then
    echo -e "\033[0;31m❌ Ce script doit être lancé avec sudo.\033[0m"
    echo -e "Usage : \033[0;32msudo $0\033[0m"
    exit 1
fi

REAL_USER=${SUDO_USER:-$USER}
REAL_UID=$(id -u "$REAL_USER")
REAL_GID=$(id -g "$REAL_USER")
REAL_HOME=$(eval echo "~$REAL_USER")
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"

echo -e "\033[0;36m⚙️  Configuration automatique pour l'utilisateur : $REAL_USER (UID: $REAL_UID)\033[0m"

# 1. Création du service systemd
echo "⏳ [1/5] Création du service systemd /etc/systemd/system/frenchio.service..."
cat << EOF > /etc/systemd/system/frenchio.service
[Unit]
Description=Frenchio Stack Service
After=docker.service network-online.target
Wants=network-online.target

[Service]
Type=oneshot
RemainAfterExit=yes
WorkingDirectory=${SCRIPT_DIR}
Environment=SUDO_USER=${REAL_USER}
ExecStart=/bin/bash ${SCRIPT_DIR}/start.sh
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=multi-user.target
EOF

# 2. Recharger systemd et activer le service
echo "⏳ [2/5] Activation du service systemd..."
systemctl daemon-reload
systemctl enable frenchio.service

# 3. Règle sudoers sans mot de passe pour la gestion du service
echo "⏳ [3/5] Configuration des privilèges sudoers pour la session..."
cat << EOF > /etc/sudoers.d/frenchio
${REAL_USER} ALL=(ALL) NOPASSWD: /usr/bin/systemctl start frenchio.service, /usr/bin/systemctl stop frenchio.service, /usr/bin/systemctl restart frenchio.service, /usr/bin/systemctl status frenchio.service, ${SCRIPT_DIR}/start.sh
EOF
chmod 0440 /etc/sudoers.d/frenchio

# 4. Configuration des connexions réseau et SSH pour le démarrage à froid
echo "⏳ [4/5] Configuration du Wi-Fi et SSH pour le démarrage au boot..."
# S'assurer que SSH démarre automatiquement au boot
systemctl enable ssh &>/dev/null || systemctl enable sshd &>/dev/null

# Configurer les connexions Wi-Fi existantes pour s'activer sans session (système entier)
if command -v nmcli &>/dev/null; then
    echo "   -> Configuration des connexions Wi-Fi pour connexion automatique au boot..."
    # Récupérer toutes les connexions Wi-Fi et les rendre système
    for conn in $(nmcli -g NAME,TYPE connection show | grep :802-11-wireless | cut -d: -f1); do
        nmcli connection modify "$conn" connection.permissions "" 2>/dev/null
    done
fi

# Suppression de l'ancien autostart de session s'il existe
AUTOSTART_DIR="${REAL_HOME}/.config/autostart"
rm -f "${AUTOSTART_DIR}/frenchio.desktop"

# 5. Configuration de GDM (Écran de connexion) pour éviter la veille et activer l'auto-login
echo "⏳ [5/6] Configuration de GDM (greeter suspend & auto-login)..."
if [ -d /etc/gdm3 ]; then
    # Désactivation de la mise en veille automatique sur l'écran de connexion GDM
    if [ -f /etc/gdm3/greeter.dconf-defaults ]; then
        # On décommente ou ajoute les lignes dans la section [org/gnome/settings-daemon/plugins/power]
        sed -i '/\[org\/gnome\/settings-daemon\/plugins\/power\]/,/^$/ {
            s/^#\s*sleep-inactive-ac-timeout=.*/sleep-inactive-ac-timeout=0/
            s/^#\s*sleep-inactive-ac-type=.*/sleep-inactive-ac-type='\''nothing'\''/
            s/^#\s*sleep-inactive-battery-timeout=.*/sleep-inactive-battery-timeout=0/
            s/^#\s*sleep-inactive-battery-type=.*/sleep-inactive-battery-type='\''nothing'\''/
        }' /etc/gdm3/greeter.dconf-defaults
        
        # S'assurer qu'elles sont présentes si pas décommentées
        if ! grep -q "sleep-inactive-ac-timeout=0" /etc/gdm3/greeter.dconf-defaults; then
            echo -e "\nsleep-inactive-ac-timeout=0\nsleep-inactive-ac-type='nothing'\nsleep-inactive-battery-timeout=0\nsleep-inactive-battery-type='nothing'" >> /etc/gdm3/greeter.dconf-defaults
        fi
        echo "   -> Veille automatique de l'écran de connexion désactivée."
    fi

    # Désactivation de la connexion automatique (auto-login)
    if [ -f /etc/gdm3/daemon.conf ]; then
        sed -i "s/^\s*AutomaticLoginEnable\s*=\s*true/# AutomaticLoginEnable = true/g" /etc/gdm3/daemon.conf
        sed -i "s/^\s*AutomaticLogin\s*=\s*/# AutomaticLogin = /g" /etc/gdm3/daemon.conf
        # Désactiver Wayland pour forcer X11 (nécessaire pour Input Leap)
        sed -i "s/#\s*WaylandEnable\s*=\s*false/WaylandEnable=false/g" /etc/gdm3/daemon.conf
        echo "   -> Connexion automatique désactivée (mot de passe requis)."
    fi
    
    # Désactivation de la mise en veille au niveau système (systemd sleep/suspend targets)
    echo "⏳ Désactivation globale de la mise en veille système (sleep, suspend, hibernate, hybrid-sleep)..."
    systemctl mask sleep.target suspend.target hibernate.target hybrid-sleep.target

    # Configurer le démarrage par défaut en mode graphique (GNOME)
    echo "⏳ Configuration de la cible systemd par défaut sur graphical.target..."
    systemctl set-default graphical.target

    # Recharger dconf si nécessaire
    if command -v dconf &>/dev/null; then
        dconf update &>/dev/null
    fi
fi

# 5b. Sécurisation de SSH (Clés uniquement)
echo "⏳ [5b/6] Configuration de la sécurité SSH (connexion par clé uniquement)..."
mkdir -p /etc/ssh/sshd_config.d
cat << SSHEOF > /etc/ssh/sshd_config.d/frenchio-security.conf
PasswordAuthentication no
KbdInteractiveAuthentication no
ChallengeResponseAuthentication no
UsePAM no
SSHEOF
systemctl restart ssh
echo "   -> Sécurité SSH activée et service redémarré."

# 6. Lancement et vérification
echo "⏳ [6/6] Lancement initial du service..."
systemctl start frenchio.service

echo -e "\n\033[0;32m✅ Configuration terminée avec succès !\033[0m"
echo -e "--------------------------------------------------"
echo -e "📊 Statut du service :"
systemctl status frenchio.service --no-pager
echo -e "--------------------------------------------------"
