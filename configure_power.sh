#!/bin/bash
# Script de configuration pour verrouiller la session lors de l'appui sur le bouton Power.

echo "Modification de /etc/systemd/logind.conf..."
sudo sed -i 's/#HandlePowerKey=poweroff/HandlePowerKey=lock/' /etc/systemd/logind.conf
sudo sed -i 's/HandlePowerKey=poweroff/HandlePowerKey=lock/' /etc/systemd/logind.conf

echo "Redémarrage du service systemd-logind pour appliquer les changements..."
sudo systemctl restart systemd-logind

echo "Fait ! Le bouton Power verrouillera désormais l'écran sans suspendre ou éteindre la machine."
