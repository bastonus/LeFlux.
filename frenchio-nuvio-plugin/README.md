# Frenchio P2P — Nuvio Plugin

Plugin natif pour Nuvio qui recherche sur les trackers français **(C411, Torr9, TR4KER, Gemini)** et retourne des liens magnets pour le streaming P2P intégré de Nuvio.

**Aucun serveur requis.** Tout tourne dans l'application Nuvio.

---

## Fonctionnement

```
Nuvio demande des streams (IMDB ID)
  → Plugin résout le titre via TMDB
  → Recherche parallèle sur les 4 trackers
  → Déduplique + filtre par titre / saison / taille
  → Retourne des magnets → Nuvio streame en P2P
```

## Trackers supportés

| Tracker | Type auth | Recherche par ID |
|---------|-----------|-----------------|
| C411 | `api_token` | ✅ TMDB + IMDB |
| Torr9 | `passkey` | ✅ TMDB + IMDB |
| TR4KER | `apikey` (Torznab) | Titre uniquement |
| Gemini | `api_token` | ✅ TMDB + IMDB |

## Prérequis Nuvio

Activer : **Paramètres → Lecture → Autoriser les streams P2P**

---

## Installation

### 1. Configurer

Ouvrez `configure.html` dans un navigateur, renseignez vos clés, cliquez **Générer l'URL**.

### 2. Héberger le plugin (pour les tests)

```bash
npm install
npm run build   # génère providers/frenchio-p2p.js
npm start       # serveur local sur :3000
```

Dans Nuvio : **Paramètres → Développeur → Plugin Tester** → coller `http://<votre-ip>:3000/manifest.json?config=<base64>`

### 3. Hébergement permanent (optionnel)

Les fichiers suivants peuvent être hébergés statiquement (GitHub Pages, Netlify, etc.) :
- `manifest.json`
- `providers/frenchio-p2p.js`
- `configure.html`

L'URL d'installation finale : `https://votre-host/manifest.json?config=<base64>`

---

## Développement

```bash
# Modifier src/frenchio-p2p/index.js
npm run build   # recompile pour Hermes
npm start       # teste dans Nuvio via Plugin Tester
```

## Structure

```
frenchio-nuvio-plugin/
├── manifest.json              # Registre du plugin Nuvio
├── configure.html             # Page de configuration (aucun serveur requis)
├── src/
│   └── frenchio-p2p/
│       └── index.js           # Code source (async/await)
├── providers/
│   └── frenchio-p2p.js        # Fichier compilé (Hermes-compatible)
├── build.js                   # Script de compilation esbuild
└── server.js                  # Serveur de développement local
```
