"""
LeFlux. - Stremio Addon
=========================

A powerful Stremio addon for searching and streaming content from multiple
French private/semi-private trackers with AllDebrid integration and qBittorrent
fallback for non-cached torrents.

Features:
    - Multi-tracker search (UNIT3D, Sharewood, YGGTorrent, ABNormal)
    - AllDebrid instant caching detection
    - qBittorrent sequential streaming for non-cached torrents
    - Intelligent episode selection in season packs
    - Parallel API requests for maximum speed
    - Automatic magnet cleanup

Author: Frenchio Contributors
License: MIT
Repository: https://github.com/aymene69/frenchio
"""

import base64
import time
import json
import os
import logging
import aiohttp
from aiohttp import web
import aiofiles
import asyncio
from urllib.parse import urlencode, urlsplit
from services.tmdb import TMDBService
from services.unit3d import Unit3DService
from services.alldebrid import AllDebridService
from services.torbox import TorBoxService
from services.debridlink import DebridLinkService
from services.realdebrid import RealDebridService
from services.sharewood import SharewoodService
from services.ygg import YggService
from services.abn import ABNService
from services.lacale import LaCaleService
from services.c411 import C411Service
from services.torr9 import Torr9Service
from services.tr4ker import TR4KERService
from services.qbittorrent import QBittorrentService, parse_season_episode_from_path
from services.torrserver import TorrServerService
from utils import format_size, parse_torrent_name, check_season_episode, check_title_match, is_video_file
import urllib.parse

# Cache for domains availability to avoid repeated connection timeouts
# Format: {hostname: (is_online, expires_at)}
SERVICE_STATUS_CACHE = {}

async def is_service_online(url_str, timeout=1.5):
    """
    Checks if a service is online by performing a quick TCP connection.
    Caches both online and offline status to avoid repeated checks on every search.
    - Cached ONLINE status expires after 5 minutes.
    - Cached OFFLINE status expires after 5 minutes.
    """
    if not url_str:
        return True
    try:
        parsed = urllib.parse.urlsplit(url_str)
        hostname = parsed.hostname
        if not hostname:
            return True
            
        now = time.time()
        if hostname in SERVICE_STATUS_CACHE:
            is_online, expires_at = SERVICE_STATUS_CACHE[hostname]
            if now < expires_at:
                if not is_online:
                    logging.info(f"[Service Check] {hostname} is cached as OFFLINE. Skipping.")
                return is_online
                
        port = parsed.port or (443 if parsed.scheme == 'https' else 80)
        
        logging.info(f"[Service Check] Checking connectivity to {hostname}:{port}...")
        try:
            reader, writer = await asyncio.wait_for(
                asyncio.open_connection(hostname, port),
                timeout=timeout
            )
            writer.close()
            await writer.wait_closed()
            logging.info(f"[Service Check] {hostname}:{port} is ONLINE.")
            SERVICE_STATUS_CACHE[hostname] = (True, now + 300)  # cache online for 5 minutes
            return True
        except Exception as e:
            SERVICE_STATUS_CACHE[hostname] = (False, now + 300)  # cache offline for 5 minutes
            logging.error(f"[Service Check] Failed for {hostname}:{port}: {e}. Caching as OFFLINE for 5 mins.")
            return False
    except Exception as e:
        logging.error(f"[Service Check] Error parsing or checking service URL {url_str}: {e}")
        return True

async def wrap_search_task(name, url, search_coro, timeout=8.0):
    """
    Wraps a search coroutine. Checks if the service is online.
    If online, executes the search coroutine with a timeout.
    If it times out or fails, caches the domain as OFFLINE to avoid repeating the timeout.
    """
    if not await is_service_online(url):
        logging.warning(f"[Search Wrapper] Skipping {name} search because it is offline.")
        try:
            search_coro.close()
        except Exception:
            pass
        return []
    try:
        return await asyncio.wait_for(search_coro, timeout=timeout)
    except asyncio.TimeoutError:
        logging.error(f"[Search Wrapper] Search for {name} timed out after {timeout}s. Caching as OFFLINE.")
        try:
            parsed = urllib.parse.urlsplit(url)
            hostname = parsed.hostname
            if hostname:
                SERVICE_STATUS_CACHE[hostname] = (False, time.time() + 300)
        except Exception:
            pass
        return []
    except Exception as e:
        logging.error(f"[Search Wrapper] Search for {name} failed: {e}. Caching as OFFLINE.")
        try:
            parsed = urllib.parse.urlsplit(url)
            hostname = parsed.hostname
            if hostname:
                SERVICE_STATUS_CACHE[hostname] = (False, time.time() + 300)
        except Exception:
            pass
        return []

# Monkey patch Unit3DService to individually check connectivity and timeout for each tracker
original_search_tracker = Unit3DService.search_tracker

async def patched_search_tracker(self, session, tracker, query_params):
    tracker_url = tracker.get('url')
    if not await is_service_online(tracker_url):
        logging.warning(f"[UNIT3D {tracker_url}] Tracker is offline, skipping.")
        return []
    try:
        return await asyncio.wait_for(
            original_search_tracker(self, session, tracker, query_params),
            timeout=4.0
        )
    except asyncio.TimeoutError:
        logging.error(f"[UNIT3D {tracker_url}] search_tracker timed out after 4s. Caching as OFFLINE.")
        try:
            parsed = urllib.parse.urlsplit(tracker_url)
            hostname = parsed.hostname
            if hostname:
                SERVICE_STATUS_CACHE[hostname] = (False, time.time() + 300)
        except Exception:
            pass
        return []
    except Exception as e:
        logging.error(f"[UNIT3D {tracker_url}] search_tracker failed: {e}. Caching as OFFLINE.")
        try:
            parsed = urllib.parse.urlsplit(tracker_url)
            hostname = parsed.hostname
            if hostname:
                SERVICE_STATUS_CACHE[hostname] = (False, time.time() + 300)
        except Exception:
            pass
        return []

Unit3DService.search_tracker = patched_search_tracker

# Global Startup Time Tracking
STARTUP_TIME = time.time()

# Configuration du logging
logging.basicConfig(
    level=logging.INFO,  # INFO pour un usage normal
    format='%(levelname)s:%(name)s:%(message)s'
)

# Configuration du proxy (HTTP_PROXY, HTTPS_PROXY)
HTTP_PROXY = os.getenv('HTTP_PROXY') or os.getenv('http_proxy')
HTTPS_PROXY = os.getenv('HTTPS_PROXY') or os.getenv('https_proxy')

if HTTP_PROXY or HTTPS_PROXY:
    logging.info(f"Proxy configuration detected:")
    if HTTP_PROXY:
        logging.info(f"  HTTP_PROXY: {HTTP_PROXY}")
    if HTTPS_PROXY:
        logging.info(f"  HTTPS_PROXY: {HTTPS_PROXY}")

# Version de l'application
APP_VERSION = "1.4.8"

DEFAULT_CONFIG = {
    "tmdb_key": "",
    "c411_apikey": "",
    "torr9_passkey": "",
    "tr4ker_apikey": "",
    "qbittorrent": {
        "host": "http://frenchio-qbittorrent:8080",
        "username": "admin",
        "password": "adminadmin",
        "public_url": "http://localhost:8082/downloads"
    }
}


# Stremio Addons Config (signature)
STREMIO_ADDONS_CONFIG = {
    "issuer": "https://stremio-addons.net",
    "signature": "eyJhbGciOiJkaXIiLCJlbmMiOiJBMTI4Q0JDLUhTMjU2In0..9l2RL_spVPK81eoy5BUkDg.efNcrE-IQ2DOoYtul30Y1bf3YuCxW8imVaKluLvX2ThwHlgi14rEajndgvRKjVDv57fazbZncm3uySZvqyi_OpQCb5tTHZJcxwD1uhdO5hXDwgSV25T-eOV8tnhnFhNd.0o5__kzn1_ygVSGX7whq3A"
}

# Configuration des fonctionnalités
QBITTORRENT_ENABLE = os.getenv('QBITTORRENT_ENABLE', 'true').lower() in ('true', '1', 'yes')
MANIFEST_TITLE_SUFFIX = os.getenv('MANIFEST_TITLE_SUFFIX', '')
MANIFEST_BLURB = os.getenv('MANIFEST_BLURB', '')

logging.info(f"qBittorrent enabled: {QBITTORRENT_ENABLE}")
if MANIFEST_TITLE_SUFFIX:
    logging.info(f"Manifest title suffix: {MANIFEST_TITLE_SUFFIX}")
if MANIFEST_BLURB:
    logging.info(f"Manifest blurb configured")

# ============================================================================
# Helpers
# ============================================================================

def estimate_episode_size(torrent_name, total_size):
    name_lower = torrent_name.lower()
    import re
    # Détecter les plages de saisons comme S01-S08, S1-S8
    season_range = re.search(r's(\d+)\s*-\s*s?(\d+)', name_lower)
    if season_range:
        try:
            s_start = int(season_range.group(1))
            s_end = int(season_range.group(2))
            num_seasons = max(1, s_end - s_start + 1)
            num_episodes = num_seasons * 10
            return int(total_size / num_episodes)
        except Exception:
            pass
            
    # Détecter intégrale de série sans plage explicite
    if "integrale" in name_lower or "complete" in name_lower or "pack" in name_lower:
        return int(total_size / 20)
        
    # Vérifier si c'est un pack de saison (ex: "saison 1" ou "s01" mais sans "e01" ou "ep1" etc.)
    has_season = re.search(r's\d+|season\s*\d+|saison\s*\d+', name_lower)
    has_episode = re.search(r'e\d+|ep\d+|episode\s*\d+|\d+x\d+|\bx\d{1,2}\b', name_lower)
    if has_season and not has_episode:
        return int(total_size / 10)
        
    return total_size

def format_stream_card(torrent, meta, size_str, source_type, provider_name, state_info=None, prog=None):
    import re as _re
    # 1. Extraire la qualité de manière robuste
    q = str(meta.get('quality', '')).lower()
    raw_name = torrent.get('name', '')
    raw_name_lower = raw_name.lower()
    
    if not q or q == 'unknown':
        if '2160' in raw_name_lower or '4k' in raw_name_lower or 'uhd' in raw_name_lower:
            q = '2160p'
        elif '1080' in raw_name_lower or 'fhd' in raw_name_lower:
            q = '1080p'
        elif '720' in raw_name_lower or 'hd' in raw_name_lower:
            q = '720p'
        else:
            q = 'SD'
            
    quality_label = q.upper()
    if quality_label == '2160P':
        quality_label = '4K'
    elif quality_label == '1080P':
        quality_label = '1080p'
    elif quality_label == '720P':
        quality_label = '720p'

    # 2. Release type (Remux / BluRay / WebDL / WebRip / HDTV / BDRip)
    release_badge = ""
    rt = str(meta.get('release_type', '')).lower()
    rn = raw_name_lower
    if _re.search(r'\bremux\b', rn) or 'remux' in rt:
        release_badge = "Remux"
    elif _re.search(r'blu[-_. ]?ray|bluray', rn) and not _re.search(r'\bremux\b', rn):
        if _re.search(r'\bbdrip\b', rn):
            release_badge = "BDRip"
        else:
            release_badge = "BluRay"
    elif _re.search(r'web[-_. ]?dl|webdl', rn):
        release_badge = "WEB-DL"
    elif _re.search(r'web[-_. ]?rip|webrip', rn):
        release_badge = "WEBRip"
    elif _re.search(r'\bhdtv\b', rn):
        release_badge = "HDTV"
    elif rt and rt not in ('unknown', ''):
        release_badge = meta.get('release_type', '')

    # 3. HDR / codec vidéo
    hdr_badge = ""
    if _re.search(r'\bdv\b|dolby.?vision', rn):
        hdr_badge = "DV"
    elif _re.search(r'hdr10\+|hdr10plus', rn):
        hdr_badge = "HDR10+"
    elif _re.search(r'\bhdr\b', rn):
        hdr_badge = "HDR"
    elif _re.search(r'\bsdr\b', rn):
        hdr_badge = "SDR"

    codec_badge = ""
    if _re.search(r'\bx265\b|hevc', rn):
        codec_badge = "HEVC"
    elif _re.search(r'\bx264\b|\bavc\b', rn):
        codec_badge = "AVC"
    elif _re.search(r'\bav1\b', rn):
        codec_badge = "AV1"

    # 4. Audio
    audio_badge = ""
    if _re.search(r'truehd|atmos', rn):
        audio_badge = "TrueHD"
    elif _re.search(r'dts[-_. ]?hd|dts[-_. ]?ma', rn):
        audio_badge = "DTS-HD"
    elif _re.search(r'\bdts\b', rn):
        audio_badge = "DTS"
    elif _re.search(r'dd\+|eac3|e-ac-3', rn):
        audio_badge = "DD+"
    elif _re.search(r'\bac3\b|dolby.?digital', rn):
        audio_badge = "DD"
    elif _re.search(r'\baac\b', rn):
        audio_badge = "AAC"
    elif _re.search(r'\bflac\b', rn):
        audio_badge = "FLAC"

    # 5. Déterminer la langue
    is_multi = 'multi' in rn or 'multi' in str(meta.get('language', '')).lower() or 'vf+vo' in rn or 'vo+vf' in rn
    is_truefrench = 'truefrench' in rn or 'vff' in rn
    is_vostfr = 'vostfr' in rn or 'subfrench' in rn or 'vosfr' in rn
    is_vf = _re.search(r'\bvf\b|\bfrench\b', rn) is not None
    lang_badge = ""
    if is_multi:
        lang_badge = "MULTI"
    elif is_truefrench:
        lang_badge = "TrueFrench"
    elif is_vostfr:
        lang_badge = "VOSTFR"
    elif is_vf:
        lang_badge = "VF"

    # 6. Statut de téléchargement
    if source_type == 'server_completed':
        status = "Téléchargé"
    elif source_type == 'debrid':
        status = "Caché"
    elif source_type == 'server_downloading' and state_info:
        status = _re.sub(r'[📥💾⚡📺📦🎞️📂🌲🐝🎬⚓📡🔥🌐🔊💬🔹⏳✅]', '', state_info).strip()
    elif source_type == 'server_other' and state_info:
        status = _re.sub(r'[📥💾⚡📺📦🎞️📂🌲🐝🎬⚓📡🔥🌐🔊💬🔹⏳✅]', '', state_info).strip()
    else:
        status = "Télécharger"

    # 7. Tracker propre
    raw_tracker = torrent.get('tracker_name', provider_name)
    if raw_tracker.startswith('http'):
        from urllib.parse import urlparse as _urlparse
        domain = _urlparse(raw_tracker).hostname or raw_tracker
        clean_tracker = domain.split('.')[0].capitalize()
    else:
        clean_tracker = raw_tracker
    if clean_tracker == 'UNIT3D':
        clean_tracker = provider_name

    # 1. Le nom du flux (name) doit contenir les badges principaux : statut textuel "Téléchargé", qualité, taille, langue, et le tracker à la fin
    name_parts = []
    if status == "Téléchargé":
        name_parts.append("Téléchargé")
    elif status and status != "Télécharger":
        name_parts.append(status)
        
    if quality_label:
        name_parts.append(quality_label)
    if size_str:
        name_parts.append(size_str)
    if lang_badge:
        name_parts.append(lang_badge)
        
    left_name = clean_tracker if clean_tracker else "LeFlux."
    name_parts.append(left_name)
    
    formatted_name = " · ".join(name_parts)

    desc_parts = []
        
    if release_badge:
        desc_parts.append(release_badge)
    if codec_badge:
        desc_parts.append(codec_badge)
    if hdr_badge:
        desc_parts.append(hdr_badge)
    if audio_badge:
        desc_parts.append(audio_badge)

    formatted_desc = " · ".join(desc_parts)

    return formatted_name, formatted_desc


# ============================================================================
# Middleware
# ============================================================================

@web.middleware
async def cors_middleware(request, handler):
    """
    CORS middleware to allow cross-origin requests from Stremio.
    
    Stremio Web requires CORS headers to communicate with external addons.
    This middleware handles OPTIONS preflight requests and adds headers to all responses,
    including HTTPExceptions (like 404 or 405 Method Not Allowed).
    """
    if request.method == 'OPTIONS':
        response = web.Response(status=204)
        response.headers['Access-Control-Allow-Origin'] = '*'
        response.headers['Access-Control-Allow-Methods'] = 'GET, POST, OPTIONS'
        response.headers['Access-Control-Allow-Headers'] = request.headers.get('Access-Control-Request-Headers', '*')
        response.headers['Access-Control-Max-Age'] = '86400'
        return response

    try:
        response = await handler(request)
    except web.HTTPException as ex:
        ex.headers['Access-Control-Allow-Origin'] = '*'
        ex.headers['Access-Control-Allow-Methods'] = 'GET, POST, OPTIONS'
        ex.headers['Access-Control-Allow-Headers'] = request.headers.get('Access-Control-Request-Headers', '*')
        raise ex

    response.headers['Access-Control-Allow-Origin'] = '*'
    response.headers['Access-Control-Allow-Methods'] = 'GET, POST, OPTIONS'
    response.headers['Access-Control-Allow-Headers'] = request.headers.get('Access-Control-Request-Headers', '*')
    return response

async def handle_login(request):
    """Serve the login page."""
    try:
        async with aiofiles.open('templates/login.html', mode='r') as f:
            content = await f.read()
        return web.Response(text=content, content_type='text/html')
    except Exception as e:
        return web.Response(text=str(e), status=500)

async def handle_portal(request):
    """Serve the portal page."""
    config_str = request.match_info.get('config', '')
    config_str = get_current_config_b64(config_str)
        
    domain = os.getenv('DOMAIN', 'yourdomain.com')
    
    try:
        async with aiofiles.open('templates/portal.html', mode='r') as f:
            content = await f.read()
            
        content = content.replace('{{CONFIG_B64}}', config_str)
        content = content.replace('{{DOMAIN}}', domain)
        content = content.replace('{{MANIFEST_TOKEN}}', os.getenv('MANIFEST_TOKEN', ''))
        return web.Response(text=content, content_type='text/html')
    except Exception as e:
        return web.Response(text=str(e), status=500)

async def handle_explorer(request):
    """Serve the explorer page."""
    config_str = request.match_info.get('config', '')
    config_str = get_current_config_b64(config_str)
        
    domain = os.getenv('DOMAIN', 'yourdomain.com')
    
    try:
        async with aiofiles.open('templates/explorer.html', mode='r') as f:
            content = await f.read()
            
        content = content.replace('{{CONFIG_B64}}', config_str)
        content = content.replace('{{DOMAIN}}', domain)
        return web.Response(text=content, content_type='text/html')
    except Exception as e:
        return web.Response(text=str(e), status=500)

async def handle_updates(request):
    """Serve the updates page."""
    config_str = request.match_info.get('config', '')
    config_str = get_current_config_b64(config_str)
        
    domain = os.getenv('DOMAIN', 'yourdomain.com')
    
    try:
        async with aiofiles.open('templates/updates.html', mode='r') as f:
            content = await f.read()
            
        content = content.replace('{{CONFIG_B64}}', config_str)
        content = content.replace('{{DOMAIN}}', domain)
        return web.Response(text=content, content_type='text/html')
    except Exception as e:
        return web.Response(text=str(e), status=500)

async def handle_configure(request):
    """
    Serve the configuration page with optional pre-filled values.
    """
    config_str = request.match_info.get('config', '')
    prefill_data = json.dumps(DEFAULT_CONFIG)

    if config_str:
        try:
            # On tente de décoder si une config est passée dans l'URL
            decoded = decode_config(config_str)
            if decoded:
                prefill_data = json.dumps(decoded)
        except:
            pass
    else:
        # Si pas de config dans l'URL, on tente d'utiliser la config sauvegardée locale si présente
        config_path = "/app/config/config.json"
        if os.path.exists(config_path):
            try:
                with open(config_path, "r", encoding="utf-8") as f:
                    prefill_data = json.dumps(json.load(f))
            except:
                pass

    try:
        async with aiofiles.open('templates/configure.html', mode='r') as f:
            content = await f.read()
        
        domain = os.getenv('DOMAIN', 'yourdomain.com')
        manifest_token = os.getenv('MANIFEST_TOKEN', '')
        if not config_str:
            config_str = get_current_config_b64()
            
        content = content.replace('{{CONFIG_B64}}', config_str)
        content = content.replace('{{DOMAIN}}', domain)
        content = content.replace('{{MANIFEST_TOKEN}}', manifest_token)
        
        # Injection de la config pré-remplie dans le JS
        content = content.replace('const prefillConfig = {};', f'const prefillConfig = {prefill_data};')
        
        # Injection de la variable QBITTORRENT_ENABLE
        qbit_enabled_js = 'true' if QBITTORRENT_ENABLE else 'false'
        content = content.replace('const qbittorrentEnabled = true;', f'const qbittorrentEnabled = {qbit_enabled_js};')
        
        # Injection du blurb personnalisé (échappé pour JavaScript)
        blurb_escaped = json.dumps(MANIFEST_BLURB) if MANIFEST_BLURB else '""'
        content = content.replace('const manifestBlurb = "";', f'const manifestBlurb = {blurb_escaped};')
        
        # Injection de la version de l'application
        content = content.replace('const appVersion = "1.1.0";', f'const appVersion = "{APP_VERSION}";')
        
        return web.Response(text=content, content_type='text/html')
    except Exception as e:
        return web.Response(text=str(e), status=500)

async def handle_p2p(request):
    """
    Serve the P2P configuration page.
    """
    config_str = request.match_info.get('config', '')
    if not config_str:
        config_str = get_current_config_b64()
    
    domain = os.getenv('DOMAIN', 'yourdomain.com')
    
    try:
        async with aiofiles.open('templates/p2p.html', mode='r') as f:
            content = await f.read()
            
        content = content.replace('{{CONFIG_B64}}', config_str)
        content = content.replace('{{DOMAIN}}', domain)
        return web.Response(text=content, content_type='text/html')
    except Exception as e:
        return web.Response(text=str(e), status=500)

async def handle_vpn_page(request):
    """
    Serve the VPN configuration page with pre-filled values.
    """
    config_str = request.match_info.get('config', '')
    prefill_data = json.dumps(DEFAULT_CONFIG)

    if config_str:
        try:
            decoded = decode_config(config_str)
            if decoded:
                prefill_data = json.dumps(decoded)
        except:
            pass
    else:
        config_path = "/app/config/config.json"
        if os.path.exists(config_path):
            try:
                with open(config_path, "r", encoding="utf-8") as f:
                    prefill_data = json.dumps(json.load(f))
            except:
                pass

    try:
        async with aiofiles.open('templates/vpn.html', mode='r') as f:
            content = await f.read()
        
        domain = os.getenv('DOMAIN', 'yourdomain.com')
        if not config_str:
            config_str = get_current_config_b64()
            
        content = content.replace('{{CONFIG_B64}}', config_str)
        content = content.replace('{{DOMAIN}}', domain)
        content = content.replace('const prefillConfig = {};', f'const prefillConfig = {prefill_data};')
        
        return web.Response(text=content, content_type='text/html')
    except Exception as e:
        return web.Response(text=str(e), status=500)

async def handle_network_page(request):
    """
    Serve the Network configuration page with pre-filled values.
    """
    config_str = request.match_info.get('config', '')
    prefill_data = json.dumps(DEFAULT_CONFIG)

    if config_str:
        try:
            decoded = decode_config(config_str)
            if decoded:
                prefill_data = json.dumps(decoded)
        except:
            pass
    else:
        config_path = "/app/config/config.json"
        if os.path.exists(config_path):
            try:
                with open(config_path, "r", encoding="utf-8") as f:
                    prefill_data = json.dumps(json.load(f))
            except:
                pass

    try:
        async with aiofiles.open('templates/network.html', mode='r') as f:
            content = await f.read()
        
        domain = os.getenv('DOMAIN', 'yourdomain.com')
        if not config_str:
            config_str = get_current_config_b64()
            
        content = content.replace('{{CONFIG_B64}}', config_str)
        content = content.replace('{{DOMAIN}}', domain)
        content = content.replace('const prefillConfig = {};', f'const prefillConfig = {prefill_data};')
        
        return web.Response(text=content, content_type='text/html')
    except Exception as e:
        return web.Response(text=str(e), status=500)

async def handle_service_iframe(request):
    """
    Serve a service (qBittorrent, TorrServer, JOAL) inside an iframe keeping the LeFlux. header.
    """
    config_str = request.match_info.get('config', '')
    service_id = request.match_info.get('service_id', '')
    
    if not config_str:
        config_str = get_current_config_b64()
        
    service_urls = {
        "qbittorrent": "/qbittorrent/",
        "torrserver": "/torrserver/",
        "joal": "/joal-secret/ui/"
    }
    
    iframe_url = service_urls.get(service_id)
    if not iframe_url:
        return web.Response(text="Service non trouvé", status=404)
        
    try:
        async with aiofiles.open('templates/service_frame.html', mode='r') as f:
            content = await f.read()
            
        domain = os.getenv('DOMAIN', 'yourdomain.com')
        content = content.replace('{{CONFIG_B64}}', config_str)
        content = content.replace('{{DOMAIN}}', domain)
        content = content.replace('{{SERVICE_ID}}', service_id)
        content = content.replace('{{IFRAME_URL}}', iframe_url)
        
        display_names = {
            "qbittorrent": "qBittorrent",
            "torrserver": "TorrServer",
            "joal": "JOAL (Ratio)"
        }
        content = content.replace('{{SERVICE_NAME}}', display_names.get(service_id, service_id.capitalize()))
        
        return web.Response(text=content, content_type='text/html')
    except Exception as e:
        return web.Response(text=str(e), status=500)

def build_docker_compose_yml(config):
    vpn_config = config.get("vpn", {})
    vpn_enabled = vpn_config.get("enabled", False)
    vpn_provider = vpn_config.get("provider", "mullvad")
    vpn_killswitch = "on" if vpn_config.get("killswitch", True) else "off"
    vpn_private_key = vpn_config.get("private_key", "")
    vpn_addresses = vpn_config.get("addresses", "10.64.0.1/32")
    vpn_redirect_mode = vpn_config.get("redirect_mode", "qbittorrent")
    
    # Custom WireGuard parameters
    vpn_endpoint_ip = vpn_config.get("endpoint_ip", "")
    vpn_endpoint_port = vpn_config.get("endpoint_port", 51820)
    vpn_public_key = vpn_config.get("public_key", "")
    vpn_preshared_key = vpn_config.get("preshared_key", "")

    # Network Exposure settings
    net_config = config.get("network", {})
    net_mode = net_config.get("mode", "cloudflare") # cloudflare, direct
    caddy_port = net_config.get("caddy_port", 8082) or 8082

    # --- Modular services ---
    services_cfg = config.get("services", {})
    qbit_enabled = services_cfg.get("qbittorrent", True)
    torrserver_enabled = services_cfg.get("torrserver", True)
    joal_enabled = services_cfg.get("joal", True)

    route_qbit = vpn_enabled and qbit_enabled and vpn_redirect_mode in ("qbittorrent", "both", "all")
    route_torr = vpn_enabled and torrserver_enabled and vpn_redirect_mode in ("torrserver", "both", "all")
    route_all = vpn_enabled and vpn_redirect_mode == "all"

    # Header
    yml = "services:\n"

    # VPN Service (only if enabled)
    if vpn_enabled:
        vpn_ports = []
        if route_qbit:
            vpn_ports.extend(['"8080:8080"', '"6881:6881"', '"6881:6881/udp"'])
        if route_torr:
            vpn_ports.extend(['"8090:8090"'])
        if route_all:
            vpn_ports.extend([f'"{caddy_port}:80"', '"20781:20781"'])
            
        yml += "  vpn:\n"
        yml += "    image: qmcgaw/gluetun\n"
        yml += "    container_name: frenchio-vpn\n"
        yml += "    cap_add:\n"
        yml += "      - NET_ADMIN\n"
        yml += "    devices:\n"
        yml += "      - /dev/net/tun:/dev/net/tun\n"
        yml += "    environment:\n"
        yml += f"      - VPN_SERVICE_PROVIDER={vpn_provider}\n"
        yml += "      - VPN_TYPE=wireguard\n"
        yml += f"      - WIREGUARD_PRIVATE_KEY={vpn_private_key}\n"
        yml += f"      - WIREGUARD_ADDRESSES={vpn_addresses}\n"
        yml += f"      - FIREWALL={vpn_killswitch}\n"
        if vpn_provider == "custom":
            if vpn_endpoint_ip:
                yml += f"      - VPN_ENDPOINT_IP={vpn_endpoint_ip}\n"
            if vpn_endpoint_port:
                yml += f"      - VPN_ENDPOINT_PORT={vpn_endpoint_port}\n"
            if vpn_public_key:
                yml += f"      - WIREGUARD_PUBLIC_KEY={vpn_public_key}\n"
            if vpn_preshared_key:
                yml += f"      - WIREGUARD_PRESHARED_KEY={vpn_preshared_key}\n"
        if vpn_ports:
            yml += "    ports:\n"
            for p in vpn_ports:
                yml += f"      - {p}\n"
        yml += "    networks:\n"
        yml += "      default:\n"
        yml += "        aliases:\n"
        yml += "          - frenchio-vpn\n"
        if route_qbit:
            yml += "          - qbittorrent\n"
        if route_torr:
            yml += "          - torrserver\n"
        if route_all:
            yml += "          - frenchio\n"
            yml += "          - caddy\n"
            yml += "          - tunnel\n"
            yml += "          - joal\n"
        yml += "    restart: unless-stopped\n\n"

    # Frenchio service
    yml += "  frenchio:\n"
    yml += "    image: ghcr.io/aymene69/frenchio:latest\n"
    yml += "    container_name: frenchio-addon\n"
    if route_all:
        yml += "    network_mode: \"service:vpn\"\n"
    yml += "    restart: unless-stopped\n"
    yml += "    environment:\n"
    yml += "      - PORT=7777\n"
    yml += "      - CONFIG_B64=${CONFIG_B64}\n"
    yml += "      - DOMAIN=${DOMAIN}\n"
    yml += "      - MANIFEST_TOKEN=${MANIFEST_TOKEN}\n"
    yml += "    volumes:\n"
    yml += "      - ./config:/app/config\n"
    yml += "      - ./main.py:/app/main.py:ro\n"
    yml += "      - ./qbittorrent.py:/app/services/qbittorrent.py:ro\n"
    yml += "      - ./torrserver.py:/app/services/torrserver.py:ro\n"
    yml += "      - ./services/tr4ker.py:/app/services/tr4ker.py:ro\n"
    yml += "      - ./utils.py:/app/utils.py:ro\n"
    yml += "      - ./templates/login.html:/app/templates/login.html:ro\n"
    yml += "      - ./templates/portal.html:/app/templates/portal.html:ro\n"
    yml += "      - ./templates/explorer.html:/app/templates/explorer.html:ro\n"
    yml += "      - ./templates/updates.html:/app/templates/updates.html:ro\n"
    yml += "      - ./templates/configure.html:/app/templates/configure.html:ro\n"
    yml += "      - ./templates/p2p.html:/app/templates/p2p.html:ro\n"
    yml += "      - ./templates/vpn.html:/app/templates/vpn.html:ro\n"
    yml += "      - ./templates/network.html:/app/templates/network.html:ro\n"
    yml += "      - ./downloads:/downloads\n"
    yml += "      - ../frenchio-nuvio-plugin:/frenchio-nuvio-plugin:ro\n"
    yml += "      - /var/run/docker.sock:/var/run/docker.sock\n"
    yml += "      - ../.git:/app/.git\n"
    yml += "      - /home/azandikka/.gitconfig:/root/.gitconfig:ro\n"
    yml += "      - /home/azandikka/.git-credentials:/root/.git-credentials:ro\n"
    
    deps = []
    if qbit_enabled:
        deps.append("qbittorrent")
    if vpn_enabled and route_all:
        deps.insert(0, "vpn")
    if deps:
        yml += "    depends_on:\n"
        for d in deps:
            yml += f"      - {d}\n"
    yml += "\n"

    # qBittorrent service (only if enabled)
    if qbit_enabled:
        yml += "  qbittorrent:\n"
        yml += "    image: lscr.io/linuxserver/qbittorrent:latest\n"
        yml += "    container_name: frenchio-qbittorrent\n"
        if route_qbit:
            yml += "    network_mode: \"service:vpn\"\n"
        yml += "    environment:\n"
        yml += "      - PUID=1000\n"
        yml += "      - PGID=1000\n"
        yml += "      - TZ=Europe/Paris\n"
        yml += "      - WEBUI_PORT=8080\n"
        yml += "    volumes:\n"
        yml += "      - ./qb_config:/config\n"
        yml += "      - ./downloads:/downloads\n"
        if not route_qbit:
            yml += "    ports:\n"
            yml += "      - \"8080:8080\"\n"
            yml += "      - \"6881:6881\"\n"
            yml += "      - \"6881:6881/udp\"\n"
        if vpn_enabled and route_qbit:
            yml += "    depends_on:\n"
            yml += "      - vpn\n"
        yml += "    restart: unless-stopped\n\n"

    # JOAL service (only if enabled)
    if joal_enabled:
        yml += "  joal:\n"
        yml += "    image: anthonyraymond/joal:latest\n"
        yml += "    container_name: frenchio-joal\n"
        if route_all:
            yml += "    network_mode: \"service:vpn\"\n"
        yml += "    restart: unless-stopped\n"
        yml += "    volumes:\n"
        yml += "      - ./joal_data:/data\n"
        if not route_all:
            yml += "    ports:\n"
            yml += "      - \"20781:20781\"\n"
        yml += "    command:\n"
        yml += "      - --joal-conf=/data\n"
        yml += "      - --spring.main.web-environment=true\n"
        yml += "      - --server.port=20781\n"
        yml += "      - --joal.ui.path.prefix=joal-secret\n"
        yml += "      - --joal.ui.secret-token=joal-secret-key\n"
        yml += "      - --server.forward-headers-strategy=FRAMEWORK\n"
        if vpn_enabled and route_all:
            yml += "    depends_on:\n"
            yml += "      - vpn\n"
        yml += "\n"

    # Caddy service
    yml += "  caddy:\n"
    yml += "    image: caddy:alpine\n"
    yml += "    container_name: frenchio-caddy\n"
    if route_all:
        yml += "    network_mode: \"service:vpn\"\n"
    yml += "    restart: unless-stopped\n"
    yml += "    volumes:\n"
    yml += "      - ./Caddyfile:/etc/caddy/Caddyfile:ro\n"
    yml += "      - ./downloads:/downloads:ro\n"
    yml += "      - ./joal_ui:/joal_ui:ro\n"
    yml += "      - ./frenchio-badges.json:/app/frenchio-badges.json:ro\n"
    yml += "      - ./badge-telecharge.png:/app/badge-telecharge.png:ro\n"
    yml += "      - caddy_data:/data\n"
    yml += "      - caddy_config:/config\n"
    if not route_all:
        yml += "    ports:\n"
        yml += f"      - \"{caddy_port}:80\"\n"
    yml += "    environment:\n"
    yml += "      - DOMAIN=${DOMAIN}\n"
    yml += "      - CONFIG_B64=${CONFIG_B64}\n"
    yml += "      - MANIFEST_TOKEN=${MANIFEST_TOKEN}\n"
    
    caddy_deps = ["frenchio"]
    if joal_enabled:
        caddy_deps.append("joal")
    if vpn_enabled and route_all:
        caddy_deps.insert(0, "vpn")
    yml += "    depends_on:\n"
    for cd in caddy_deps:
        yml += f"      - {cd}\n"
    yml += "\n"

    # Tunnel service (only if network mode is cloudflare)
    if net_mode == "cloudflare":
        yml += "  tunnel:\n"
        yml += "    image: cloudflare/cloudflared:latest\n"
        yml += "    container_name: frenchio-tunnel\n"
        if route_all:
            yml += "    network_mode: \"service:vpn\"\n"
        yml += "    restart: unless-stopped\n"
        yml += "    command: tunnel run --token ${TUNNEL_TOKEN}\n"
        
        tunnel_deps = ["caddy"]
        if vpn_enabled and route_all:
            tunnel_deps.insert(0, "vpn")
        yml += "    depends_on:\n"
        for td in tunnel_deps:
            yml += f"      - {td}\n"
        yml += "    extra_hosts:\n"
        yml += "      - \"host.docker.internal:host-gateway\"\n\n"

    # TorrServer service (only if enabled)
    if torrserver_enabled:
        yml += "  torrserver:\n"
        yml += "    image: ghcr.io/yourok/torrserver:latest\n"
        yml += "    container_name: torrserver\n"
        if route_torr:
            yml += "    network_mode: \"service:vpn\"\n"
        yml += "    environment:\n"
        yml += "      - PUID=1000\n"
        yml += "      - PGID=1000\n"
        yml += "      - TZ=Europe/Paris\n"
        yml += "      - TS_PATH=/db\n"
        yml += "    volumes:\n"
        yml += "      - ./torrserver_db:/db\n"
        if not route_torr:
            yml += "    ports:\n"
            yml += "      - \"8090:8090\"\n"
        if vpn_enabled and route_torr:
            yml += "    depends_on:\n"
            yml += "      - vpn\n"
        yml += "    restart: unless-stopped\n\n"

    # Volumes
    yml += "volumes:\n"
    yml += "  caddy_data:\n"
    yml += "  caddy_config:\n"

    return yml

async def apply_docker_compose(yml_content, new_config_b64, domain=None, tunnel_token=None):
    connector = aiohttp.UnixConnector(path='/var/run/docker.sock')
    host_stack_path = None
    async with aiohttp.ClientSession(connector=connector) as session:
        try:
            async with session.get("http://localhost/containers/frenchio-addon/json") as resp:
                if resp.status == 200:
                    data = await resp.json()
                    mounts = data.get("Mounts", [])
                    for m in mounts:
                        if m.get("Destination") == "/app/config":
                            config_host_path = m.get("Source")
                            host_stack_path = os.path.dirname(config_host_path)
                            break
        except Exception as e:
            logging.error(f"Error inspecting frenchio-addon mount: {e}")
            
        if not host_stack_path:
            logging.error("Could not find host path for docker-compose.yml")
            return False, "Could not determine host stack directory."

        new_compose_path = "/app/config/docker-compose.yml.new"
        try:
            with open(new_compose_path, "w", encoding="utf-8") as f:
                f.write(yml_content)
        except Exception as e:
            return False, f"Failed to write temporary compose file: {e}"

        # Build sed command for updating host .env file
        env_vars = {
            "NEW_CONFIG_B64": new_config_b64
        }
        
        env_script = "sed -i 's|^CONFIG_B64=.*|CONFIG_B64='\"$NEW_CONFIG_B64\"'|g' /stack/.env"
        
        if domain is not None:
            env_vars["NEW_DOMAIN"] = domain
            env_script += " && sed -i 's|^DOMAIN=.*|DOMAIN='\"$NEW_DOMAIN\"'|g' /stack/.env"
            
        if tunnel_token is not None:
            env_vars["NEW_TUNNEL_TOKEN"] = tunnel_token
            env_script += " && sed -i 's|^TUNNEL_TOKEN=.*|TUNNEL_TOKEN='\"$NEW_TUNNEL_TOKEN\"'|g' /stack/.env"

        helper_config = {
            "Image": "docker:cli",
            "Cmd": [
                "sh", "-c",
                f"{env_script} && cp /stack/config/docker-compose.yml.new /stack/docker-compose.yml && docker compose -f /stack/docker-compose.yml up -d --remove-orphans"
            ],
            "Env": [f"{k}={v}" for k, v in env_vars.items()],
            "HostConfig": {
                "Binds": [
                    "/var/run/docker.sock:/var/run/docker.sock",
                    f"{host_stack_path}:/stack"
                ],
                "AutoRemove": True
            }
        }

        try:
            async with session.post("http://localhost/containers/create?name=frenchio-vpn-helper", json=helper_config) as resp:
                if resp.status != 201:
                    err = await resp.text()
                    logging.error(f"Failed to create helper container: {err}")
                    return False, f"Failed to create helper container: {err}"
                
                async with session.post("http://localhost/containers/frenchio-vpn-helper/start") as start_resp:
                    if start_resp.status not in (204, 200):
                        err = await start_resp.text()
                        logging.error(f"Failed to start helper container: {err}")
                        return False, f"Failed to start helper container: {err}"
                            
            logging.info("Launched frenchio-vpn-helper to recreate stack containers.")
            return True, "Stack updating in progress..."
        except Exception as e:
            logging.error(f"Error launching helper container: {e}")
            return False, str(e)

async def handle_vpn_status(request):
    config_path = "/app/config/config.json"
    vpn_config = {}
    if os.path.exists(config_path):
        try:
            with open(config_path, "r", encoding="utf-8") as f:
                config = json.load(f)
                vpn_config = config.get("vpn", {})
        except:
            pass
            
    if not vpn_config.get("enabled", False):
        return web.json_response({"enabled": False, "status": "disabled"})
        
    connector = aiohttp.UnixConnector(path='/var/run/docker.sock')
    async with aiohttp.ClientSession(connector=connector) as session:
        try:
            url = "http://localhost/containers/frenchio-vpn/json"
            async with session.get(url) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    state = data.get("State", {})
                    status = state.get("Status", "unknown")
                    running = state.get("Running", False)
                    health = state.get("Health", {}).get("Status", "none")
                else:
                    return web.json_response({"enabled": True, "status": "error", "message": "Container not found"})
        except Exception as e:
            return web.json_response({"enabled": True, "status": "error", "message": str(e)})
            
    public_ip = "Inconnu"
    vpn_provider = vpn_config.get("provider", "mullvad")
    if running:
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get("http://frenchio-vpn:8000/v1/publicip/ip", timeout=2) as ip_resp:
                    if ip_resp.status == 200:
                        ip_data = await ip_resp.json()
                        public_ip = ip_data.get("public_ip", "Inconnu")
        except Exception:
            pass
            
    return web.json_response({
        "enabled": True,
        "status": status,
        "running": running,
        "health": health,
        "provider": vpn_provider,
        "public_ip": public_ip,
        "redirect_mode": vpn_config.get("redirect_mode", "qbittorrent")
    })

async def handle_network_status(request):
    config_path = "/app/config/config.json"
    net_config = {}
    if os.path.exists(config_path):
        try:
            with open(config_path, "r", encoding="utf-8") as f:
                config = json.load(f)
                net_config = config.get("network", {})
        except:
            pass
            
    mode = net_config.get("mode", "cloudflare")
    domain = net_config.get("domain", os.getenv('DOMAIN', 'yourdomain.com'))
    caddy_port = net_config.get("caddy_port", 8082) or 8082
    
    if mode != "cloudflare":
        return web.json_response({
            "mode": mode,
            "domain": domain,
            "caddy_port": caddy_port,
            "status": "active",
            "running": True,
            "health": "none",
            "message": "Accès direct actif"
        })
        
    connector = aiohttp.UnixConnector(path='/var/run/docker.sock')
    async with aiohttp.ClientSession(connector=connector) as session:
        try:
            url = "http://localhost/containers/frenchio-tunnel/json"
            async with session.get(url) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    state = data.get("State", {})
                    status = state.get("Status", "unknown")
                    running = state.get("Running", False)
                    health = state.get("Health", {}).get("Status", "none")
                else:
                    return web.json_response({
                        "mode": mode,
                        "domain": domain,
                        "caddy_port": caddy_port,
                        "status": "error",
                        "running": False,
                        "health": "none",
                        "message": "Container tunnel non trouvé"
                    })
        except Exception as e:
            return web.json_response({
                "mode": mode,
                "domain": domain,
                "caddy_port": caddy_port,
                "status": "error",
                "running": False,
                "health": "none",
                "message": str(e)
            })
            
    return web.json_response({
        "mode": mode,
        "domain": domain,
        "caddy_port": caddy_port,
        "status": status,
        "running": running,
        "health": health,
        "message": "Tunnel Cloudflare connecté" if running else "Tunnel Cloudflare arrêté"
    })

async def handle_get_services_config(request):
    """Return enabled/disabled flags for optional services."""
    config_path = "/app/config/config.json"
    config = {}
    if os.path.exists(config_path):
        try:
            with open(config_path, "r", encoding="utf-8") as f:
                config = json.load(f)
        except Exception:
            pass
    services_cfg = config.get("services", {})
    return web.json_response({
        "qbittorrent": services_cfg.get("qbittorrent", True),
        "torrserver":  services_cfg.get("torrserver",  True),
        "joal":        services_cfg.get("joal",        True),
    })

async def handle_post_services_toggle(request):
    """Enable or disable an optional service and rebuild docker-compose."""
    try:
        body = await request.json()
        service = body.get("service")
        enabled = body.get("enabled")

        ALLOWED = ("qbittorrent", "torrserver", "joal")
        if service not in ALLOWED:
            return web.json_response(
                {"success": False, "message": f"Service inconnu : {service}. Valeurs autorisées : {ALLOWED}"},
                status=400
            )
        if not isinstance(enabled, bool):
            return web.json_response(
                {"success": False, "message": "Le champ 'enabled' doit être un booléen (true/false)."},
                status=400
            )

        config_path = "/app/config/config.json"
        config = {}
        if os.path.exists(config_path):
            try:
                with open(config_path, "r", encoding="utf-8") as f:
                    config = json.load(f)
            except Exception:
                pass

        if "services" not in config:
            config["services"] = {}
        config["services"][service] = enabled

        os.makedirs(os.path.dirname(config_path), exist_ok=True)
        with open(config_path, "w", encoding="utf-8") as f:
            json.dump(config, f, indent=4)

        # Sync env
        config_str = json.dumps(config)
        config_b64 = base64.b64encode(config_str.encode()).decode()
        os.environ["CONFIG_B64"] = config_b64

        net_config = config.get("network", {})
        domain = net_config.get("domain")
        tunnel_token = net_config.get("tunnel_token")
        yml_content = build_docker_compose_yml(config)
        success, msg = await apply_docker_compose(yml_content, config_b64, domain=domain, tunnel_token=tunnel_token)

        label = "activé" if enabled else "désactivé"
        if not success:
            return web.json_response(
                {"success": False, "message": f"Service '{service}' {label} dans la config, mais erreur stack : {msg}"},
                status=500
            )

        return web.json_response({
            "success": True,
            "message": f"Service '{service}' {label} avec succès. La stack se met à jour..."
        })

    except Exception as e:
        return web.json_response({"success": False, "message": str(e)}, status=500)

async def handle_post_config(request):
    """Save the configuration to local config.json file."""
    try:
        config_data = await request.json()
        instance_id = config_data.get('instance_id', 'main')
        instance_name = config_data.get('instance_name', 'LeFlux. Addon (Principal)')
        
        config_path = "/app/config/config.json"
        
        # Load existing config to preserve other settings
        existing_config = {}
        if os.path.exists(config_path):
            try:
                with open(config_path, "r", encoding="utf-8") as f:
                    existing_config = json.load(f)
            except:
                pass
                
        if instance_id != 'main':
            # This is a custom instance configuration
            instances = existing_config.get('addon_instances', [])
            
            # Prepare config dict for this instance (filtering out helper fields)
            inst_config = {k: v for k, v in config_data.items() if k not in ('instance_id', 'instance_name')}
            
            found = False
            for inst in instances:
                if inst.get('id') == instance_id:
                    inst['name'] = instance_name
                    inst['config'] = inst_config
                    found = True
                    break
            if not found:
                instances.append({
                    "id": instance_id,
                    "name": instance_name,
                    "config": inst_config
                })
                
            existing_config['addon_instances'] = instances
            
            os.makedirs(os.path.dirname(config_path), exist_ok=True)
            with open(config_path, "w", encoding="utf-8") as f:
                json.dump(existing_config, f, indent=4)
                
            # For dynamic/manual use, return base64 encoded config of this instance
            inst_config_str = json.dumps(inst_config)
            inst_config_b64 = base64.b64encode(inst_config_str.encode()).decode()
            
            return web.json_response({
                "success": True, 
                "message": f"Configuration de l'instance '{instance_name}' sauvegardée !", 
                "config_b64": inst_config_b64
            })
        else:
            # Filter helper fields
            clean_config_data = {k: v for k, v in config_data.items() if k not in ('instance_id', 'instance_name')}
            
            # Merge incoming data into existing configuration
            merged_config = {**existing_config}
            for k, v in clean_config_data.items():
                if isinstance(v, dict) and k in merged_config and isinstance(merged_config[k], dict):
                    merged_config[k] = {**merged_config[k], **v}
                else:
                    merged_config[k] = v
            config_data = merged_config
            
            # Preserve p2p_links and addon_instances
            if 'p2p_links' in existing_config:
                config_data['p2p_links'] = existing_config['p2p_links']
            if 'addon_instances' in existing_config:
                config_data['addon_instances'] = existing_config['addon_instances']
                
            os.makedirs(os.path.dirname(config_path), exist_ok=True)
            with open(config_path, "w", encoding="utf-8") as f:
                json.dump(config_data, f, indent=4)
                
            # Update CONFIG_B64 dynamically in memory
            config_str = json.dumps(config_data)
            config_b64 = base64.b64encode(config_str.encode()).decode()
            os.environ['CONFIG_B64'] = config_b64
            
            # Update domain in memory if present in network config
            net_config = config_data.get("network", {})
            domain = net_config.get("domain")
            if domain:
                os.environ['DOMAIN'] = domain
                
            # Extract variables for host .env updates
            tunnel_token = net_config.get("tunnel_token")
            
            # Regenerate docker-compose.yml and update .env on host
            yml_content = build_docker_compose_yml(config_data)
            success, msg = await apply_docker_compose(yml_content, config_b64, domain=domain, tunnel_token=tunnel_token)
            
            if not success:
                return web.json_response({
                    "success": False,
                    "message": f"Configuration sauvegardée, mais erreur stack: {msg}"
                }, status=500)
                
            return web.json_response({
                "success": True, 
                "message": "Configuration sauvegardée et stack mise à jour !", 
                "config_b64": config_b64
            })
    except Exception as e:
        return web.json_response({"success": False, "message": str(e)}, status=500)

async def handle_download_config(request):
    """Download the centralized config.json file."""
    config_path = "/app/config/config.json"
    if not os.path.exists(config_path):
        return web.Response(text="Configuration non trouvée", status=404)
    try:
        with open(config_path, "r", encoding="utf-8") as f:
            content = f.read()
        return web.Response(
            text=content,
            content_type="application/json",
            headers={
                "Content-Disposition": "attachment; filename=\"leflux_config.json\""
            }
        )
    except Exception as e:
        return web.Response(text=str(e), status=500)

async def handle_upload_config(request):
    """Upload and restore the centralized config.json file, recreating the stack."""
    config_path = "/app/config/config.json"
    try:
        data = await request.json()
        
        if not isinstance(data, dict):
            return web.json_response({"success": False, "message": "Format JSON invalide"})
            
        os.makedirs(os.path.dirname(config_path), exist_ok=True)
        with open(config_path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=4)
            
        # Update in memory
        config_str = json.dumps(data)
        config_b64 = base64.b64encode(config_str.encode()).decode()
        os.environ['CONFIG_B64'] = config_b64
        
        net_config = data.get("network", {})
        domain = net_config.get("domain")
        if domain:
            os.environ['DOMAIN'] = domain
            
        # Rebuild and apply docker compose
        tunnel_token = net_config.get("tunnel_token")
        yml_content = build_docker_compose_yml(data)
        success, msg = await apply_docker_compose(yml_content, config_b64, domain=domain, tunnel_token=tunnel_token)
        
        if not success:
            return web.json_response({
                "success": False,
                "message": f"Configuration importée, mais erreur stack: {msg}"
            }, status=500)
            
        return web.json_response({
            "success": True, 
            "message": "Configuration importée et stack mise à jour avec succès !"
        })
    except Exception as e:
        return web.json_response({"success": False, "message": str(e)}, status=500)

async def handle_get_addon_instances(request):
    config_path = "/app/config/config.json"
    existing_config = {}
    if os.path.exists(config_path):
        try:
            with open(config_path, "r", encoding="utf-8") as f:
                existing_config = json.load(f)
        except:
            pass
            
    instances = existing_config.get('addon_instances', [])
    
    # Extract keys for principal configuration
    system_keys = ('p2p_links', 'vpn', 'network', 'addon_instances')
    principal_config = {k: v for k, v in existing_config.items() if k not in system_keys}
    
    all_instances = [
        {
            "id": "main",
            "name": "LeFlux. Addon (Principal)",
            "config": principal_config
        }
    ]
    
    for inst in instances:
        all_instances.append({
            "id": inst.get('id'),
            "name": inst.get('name'),
            "config": inst.get('config', {})
        })
        
    return web.json_response({
        "instances": all_instances
    })


async def handle_create_addon_instance(request):
    try:
        body = await request.json()
    except:
        body = {}
        
    name = body.get('name', '').strip()
    config_path = "/app/config/config.json"
    
    existing_config = {}
    if os.path.exists(config_path):
        try:
            with open(config_path, "r", encoding="utf-8") as f:
                existing_config = json.load(f)
        except:
            pass
            
    instances = existing_config.get('addon_instances', [])
    
    import uuid
    instance_id = f"addon-{uuid.uuid4().hex}"
    
    if not name:
        name = f"LeFlux. Addon #{len(instances) + 1}"
        
    # Clone principal config (all keys except system keys)
    system_keys = ('p2p_links', 'vpn', 'network', 'addon_instances')
    cloned_config = {k: v for k, v in existing_config.items() if k not in system_keys}
    
    new_instance = {
        "id": instance_id,
        "name": name,
        "config": cloned_config
    }
    
    instances.append(new_instance)
    existing_config['addon_instances'] = instances
    
    os.makedirs(os.path.dirname(config_path), exist_ok=True)
    with open(config_path, "w", encoding="utf-8") as f:
        json.dump(existing_config, f, indent=4)
        
    return web.json_response({
        "success": True,
        "instance": new_instance
    })


async def handle_delete_addon_instance(request):
    try:
        body = await request.json()
        instance_id = body.get('instance_id')
    except:
        return web.Response(status=400, text="Invalid JSON body")
        
    if not instance_id or instance_id == 'main':
        return web.Response(status=400, text="Cannot delete principal instance")
        
    config_path = "/app/config/config.json"
    existing_config = {}
    if os.path.exists(config_path):
        try:
            with open(config_path, "r", encoding="utf-8") as f:
                existing_config = json.load(f)
        except:
            pass
            
    instances = existing_config.get('addon_instances', [])
    filtered_instances = [inst for inst in instances if inst.get('id') != instance_id]
    
    existing_config['addon_instances'] = filtered_instances
    
    os.makedirs(os.path.dirname(config_path), exist_ok=True)
    with open(config_path, "w", encoding="utf-8") as f:
        json.dump(existing_config, f, indent=4)
        
    return web.json_response({
        "success": True,
        "message": "Instance supprimée avec succès"
    })


import json

class GeminiService:
    def __init__(self, api_token):
        self.api_token = api_token
        self.base_url = "https://gemini-tracker.org"

    async def search(self, query_params):
        if not self.api_token:
            return []
        url = f"{self.base_url}/api/torrents/filter"
        params = {
            "api_token": self.api_token,
            **query_params
        }
        
        query_string = urllib.parse.urlencode(params)
        full_url = f"{url}?{query_string}"
        
        logging.info(f"[Gemini Tracker] Requesting search")
        headers = {"Accept": "application/json"}
        async with aiohttp.ClientSession(trust_env=True) as session:
            try:
                async with session.get(full_url, headers=headers, timeout=15) as response:
                    if response.status == 200:
                        text_data = await response.text()
                        try:
                            data = json.loads(text_data)
                        except json.JSONDecodeError as e:
                            logging.error(f"[Gemini Tracker] JSON Decode Error: {e}")
                            return []

                        results = []
                        if isinstance(data, dict):
                            if 'data' in data and isinstance(data['data'], list):
                                results = data['data']
                        elif isinstance(data, list):
                            results = data

                        async def fill_info_hash(item):
                            dl_link = item.get('download_link') or item.get('attributes', {}).get('download_link')
                            ih = (item.get('info_hash') or '').lower() or None
                            if not ih and dl_link:
                                try:
                                    async with session.get(dl_link, timeout=10) as tr_resp:
                                        if tr_resp.status == 200:
                                            torrent_data = await tr_resp.read()
                                            from utils import get_info_hash
                                            extracted_hash = get_info_hash(torrent_data)
                                            if extracted_hash:
                                                item['info_hash'] = extracted_hash
                                except Exception as e:
                                    logging.error(f"[Gemini Tracker] Failed to extract info_hash: {e}")
                            return item
                        
                        if results:
                            results = await asyncio.gather(*[fill_info_hash(res) for res in results])

                        cleaned_results = []
                        for res in results:
                            item = res
                            if 'attributes' in res:
                                item = {**res, **res['attributes']}

                            item['tracker_name'] = "Gemini"
                            
                            if 'download_link' in item:
                                item['link'] = item['download_link']
                            elif 'download_link' in res.get('attributes', {}):
                                item['link'] = res['attributes']['download_link']

                            cleaned_results.append({
                                "name": item.get('name', ''),
                                "size": int(item.get('size', 0)),
                                "tracker_name": "G3mini",
                                "info_hash": (item.get('info_hash') or '').lower() or None,
                                "magnet": None,
                                "link": item.get('link', ''),
                                "source": "gemini",
                                "seeders": int(item.get('seeders', 0)),
                                "leechers": int(item.get('leechers', 0))
                            })
                        return cleaned_results
                    else:
                        logging.warning(f"[Gemini Tracker] Error Status: {response.status}")
            except Exception as e:
                logging.error(f"[Gemini Tracker] Exception: {e}")
        return []

    async def search_movie(self, title, year, tmdb_id=None, imdb_id=None):
        params = {}
        if tmdb_id:
            params['tmdbId'] = tmdb_id
        elif imdb_id:
            params['imdbId'] = imdb_id.replace('tt', '')
        else:
            return []
        return await self.search(params)

    async def search_series(self, title, season, episode, tmdb_id=None, imdb_id=None):
        params = {}
        if tmdb_id:
            params['tmdbId'] = tmdb_id
        elif imdb_id:
            params['imdbId'] = imdb_id.replace('tt', '')
        else:
            return []
        
        if season is not None:
            params['seasonNumber'] = season
            if episode is not None:
                params['episodeNumber'] = episode
        return await self.search(params)


def get_current_config_b64(url_config_str=None):
    if url_config_str and url_config_str not in ("", "configure", "manifest.json", "default", "portal", "explorer", "updates"):
        try:
            # Validate it's decodable
            base64.b64decode(url_config_str + '==')
            return url_config_str
        except Exception:
            pass

    # Load from file
    config_path = "/app/config/config.json"
    if os.path.exists(config_path):
        try:
            with open(config_path, "r", encoding="utf-8") as f:
                config_data = json.load(f)
                return base64.b64encode(json.dumps(config_data).encode()).decode()
        except Exception as e:
            logging.error(f"Error encoding config.json: {e}")

    # Fallback to env
    env_b64 = os.getenv('CONFIG_B64')
    if env_b64:
        return env_b64

    # Default fallback
    default_config = DEFAULT_CONFIG
    return base64.b64encode(json.dumps(default_config).encode()).decode()


def decode_config(config_str):
    default_config = DEFAULT_CONFIG
    if config_str and config_str.startswith('addon-'):
        config_path = "/app/config/config.json"
        if os.path.exists(config_path):
            try:
                with open(config_path, "r", encoding="utf-8") as f:
                    main_config = json.load(f)
                    instances = main_config.get('addon_instances', [])
                    for inst in instances:
                        if inst.get('id') == config_str:
                            cfg = inst.get('config', {})
                            cfg['_instance_name'] = inst.get('name', 'LeFlux. Addon')
                            return cfg
            except Exception as e:
                logging.error(f"Failed to read addon instance config for {config_str}: {e}")
        return default_config

    if not config_str or config_str in ("manifest.json", "configure", "default"):
        config_path = "/app/config/config.json"
        if os.path.exists(config_path):
            try:
                with open(config_path, "r", encoding="utf-8") as f:
                    return json.load(f)
            except Exception as e:
                logging.error(f"Failed to read config.json: {e}")
                
        env_config_b64 = os.getenv('CONFIG_B64')
        if env_config_b64:
            try:
                decoded = base64.b64decode(env_config_b64 + '==').decode('utf-8')
                return json.loads(decoded)
            except Exception as e:
                logging.error(f"Failed to decode CONFIG_B64 from environment: {e}")
        return default_config
    try:
        decoded = base64.b64decode(config_str + '==').decode('utf-8')
        return json.loads(decoded)
    except Exception as e:
        logging.error(f"Config Decode Error, falling back to default: {e}")
        return default_config


def build_mediaflow_proxy_url(stream_url, mediaflow_config, filename=None):
    """
    Construit une URL MediaFlow Proxy à partir d'une URL de stream finale.

    MediaFlow est utilisé ici comme proxy optionnel en sortie, après résolution
    par le provider de débridage ou qBittorrent.
    """
    if not stream_url or not mediaflow_config:
        return stream_url

    base_url = (mediaflow_config.get('proxy_url') or '').strip().rstrip('/')
    if not base_url:
        return stream_url

    stream_path = urlsplit(stream_url).path.lower()
    is_hls = stream_path.endswith('.m3u8') or '.m3u8' in stream_path
    endpoint = '/proxy/hls/manifest.m3u8' if is_hls else '/proxy/stream'

    params = {'d': stream_url}
    api_password = (mediaflow_config.get('api_password') or '').strip()
    if api_password:
        params['api_password'] = api_password

    if endpoint == '/proxy/stream' and filename:
        params['filename'] = filename

    proxied_url = f"{base_url}{endpoint}?{urlencode(params)}"
    logging.info(f"MediaFlow proxy enabled for endpoint {endpoint}")
    return proxied_url


def finalize_stream_url(stream_url, config, filename=None):
    """Applique les post-traitements optionnels au lien final de lecture."""
    mediaflow_config = config.get('mediaflow')
    return build_mediaflow_proxy_url(stream_url, mediaflow_config, filename=filename)

# ============================================================================
# Plugin P2P Nuvio — Static File Handler
# ============================================================================

PLUGIN_DIR = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'frenchio-nuvio-plugin'))

async def handle_get_p2p_links(request):
    config_path = "/app/config/config.json"
    p2p_links = []
    if os.path.exists(config_path):
        try:
            with open(config_path, "r", encoding="utf-8") as f:
                cfg = json.load(f)
                p2p_links = cfg.get('p2p_links', [])
        except Exception as e:
            logging.error(f"Error loading P2P links: {e}")
    return web.json_response({"links": p2p_links})

async def handle_post_p2p_links(request):
    try:
        body = await request.json()
        links = body.get('links', [])
        
        config_path = "/app/config/config.json"
        config_data = {}
        if os.path.exists(config_path):
            try:
                with open(config_path, "r", encoding="utf-8") as f:
                    config_data = json.load(f)
            except:
                pass
                
        config_data['p2p_links'] = links
        
        os.makedirs(os.path.dirname(config_path), exist_ok=True)
        with open(config_path, "w", encoding="utf-8") as f:
            json.dump(config_data, f, indent=4)
            
        return web.json_response({"success": True})
    except Exception as e:
        logging.error(f"Error saving P2P links: {e}")
        return web.Response(status=500, text=str(e))

async def handle_plugin_static(request):
    """
    Serves static files from frenchio-nuvio-plugin/ at /plugin/{token}/*.
    """
    token = request.match_info.get('token', '')
    
    # Load config to check if the token is valid
    config_path = "/app/config/config.json"
    p2p_links = []
    main_config = {}
    if os.path.exists(config_path):
        try:
            with open(config_path, "r", encoding="utf-8") as f:
                main_config = json.load(f)
                p2p_links = main_config.get('p2p_links', [])
        except:
            pass
            
    # Find matching P2P link configuration
    link_config = None
    for link in p2p_links:
        if link.get('token') == token:
            link_config = link
            break
            
    if not link_config:
        return web.Response(status=403, text='Forbidden: Invalid or expired P2P plugin token')
        
    url_path = request.match_info.get('file_path', 'manifest.json')

    # Sanitize — prevent directory traversal
    safe_path = os.path.normpath(os.path.join(PLUGIN_DIR, url_path))
    if not safe_path.startswith(os.path.normpath(PLUGIN_DIR)):
        return web.Response(status=403, text='Forbidden')

    if not os.path.isfile(safe_path):
        return web.Response(status=404, text='Plugin file not found')

    ext = os.path.splitext(safe_path)[1].lower()
    mime_map = {
        '.json': 'application/json',
        '.js':   'application/javascript',
        '.html': 'text/html',
        '.css':  'text/css',
        '.svg':  'image/svg+xml',
    }
    content_type = mime_map.get(ext, 'application/octet-stream')

    try:
        async with aiofiles.open(safe_path, mode='r', encoding='utf-8') as f:
            content = await f.read()

        if url_path == 'manifest.json':
            try:
                data = json.loads(content)
                custom_name = link_config.get('name', 'LeFlux.')
                data['name'] = custom_name
                if data.get('scrapers'):
                    data['scrapers'][0]['name'] = f"{custom_name} P2P"
                    
                # Build configuration base64
                if link_config.get('sync', True):
                    cfg = {
                        'c411_apikey': main_config.get('c411_apikey', ''),
                        'c411_passkey': main_config.get('c411_passkey', ''),
                        'torr9_passkey': main_config.get('torr9_passkey', ''),
                        'tr4ker_apikey': main_config.get('tr4ker_apikey', ''),
                        'tr4ker_passkey': main_config.get('tr4ker_passkey', ''),
                        'gemini_apikey': main_config.get('gemini_apikey', ''),
                        'gemini_passkey': main_config.get('gemini_passkey', ''),
                        'max_size_gb': float(link_config.get('max_size_gb', 50.0) or 50.0),
                        'sort_by': link_config.get('sort_by', 'seeders')
                    }
                else:
                    link_keys = link_config.get('keys', {})
                    cfg = {
                        'c411_apikey': link_keys.get('c411_apikey', ''),
                        'c411_passkey': link_keys.get('c411_passkey', ''),
                        'torr9_passkey': link_keys.get('torr9_passkey', ''),
                        'tr4ker_apikey': link_keys.get('tr4ker_apikey', ''),
                        'tr4ker_passkey': link_keys.get('tr4ker_passkey', ''),
                        'gemini_apikey': link_keys.get('gemini_apikey', ''),
                        'gemini_passkey': link_keys.get('gemini_passkey', ''),
                        'max_size_gb': float(link_config.get('max_size_gb', 50.0) or 50.0),
                        'sort_by': link_config.get('sort_by', 'seeders')
                    }
                
                cfg['proxy_base'] = f"https://{request.host}/plugin/{token}/proxy"
                
                config_str = json.dumps(cfg, separators=(',', ':'))
                config_param = base64.b64encode(config_str.encode()).decode()
                
                cb = int(time.time())
                for scraper in data.get('scrapers', []):
                    if 'filename' in scraper:
                        scraper['filename'] += f"?config={config_param}&v={cb}"
                content = json.dumps(data)
            except Exception as ex:
                logging.error(f"[Plugin Static] Error processing manifest.json: {ex}")

        elif url_path == 'providers/frenchio-p2p.js':
            try:
                config_param = request.query.get('config') or None
                config = decode_config(config_param)
                if config:
                    settings_obj = {
                        'c411_apikey': config.get('c411_apikey', ''),
                        'c411_passkey': config.get('c411_passkey', ''),
                        'torr9_passkey': config.get('torr9_passkey', ''),
                        'tr4ker_apikey': config.get('tr4ker_apikey', ''),
                        'tr4ker_passkey': config.get('tr4ker_passkey', ''),
                        'gemini_apikey': config.get('gemini_apikey', ''),
                        'gemini_passkey': config.get('gemini_passkey', ''),
                        'max_size_gb': float(config.get('max_size_gb', 50.0) or 50.0),
                        'proxy_base': f"https://{request.host}/plugin/{token}/proxy"
                    }
                    injection = f"globalThis._settings = {json.dumps(settings_obj)};\n"
                    content = injection + content
            except Exception as ex:
                logging.error(f"[Plugin Static] Error injecting config into provider: {ex}")

        resp = web.Response(text=content, content_type=content_type)
        resp.headers['Access-Control-Allow-Origin'] = '*'
        resp.headers['Access-Control-Allow-Methods'] = 'GET, OPTIONS'
        resp.headers['Access-Control-Allow-Headers'] = '*'
        resp.headers['Cache-Control'] = 'no-store, no-cache, must-revalidate, max-age=0'
        resp.headers['Pragma'] = 'no-cache'
        resp.headers['Expires'] = '0'
        return resp
    except Exception as e:
        logging.error(f"[Plugin Static] Error serving {safe_path}: {e}")
        return web.Response(status=500, text='Internal error')

async def handle_plugin_proxy(request):
    """
    Simple secure CORS proxy for the client-side P2P plugin to query trackers.
    """
    token = request.match_info.get('token', '')
    
    # Load config to check if the token is valid
    config_path = "/app/config/config.json"
    p2p_links = []
    if os.path.exists(config_path):
        try:
            with open(config_path, "r", encoding="utf-8") as f:
                p2p_links = json.load(f).get('p2p_links', [])
        except:
            pass
            
    is_valid = any(link.get('token') == token for link in p2p_links)
    if not is_valid:
        return web.Response(status=403, text='Forbidden: Invalid P2P plugin token')

    target_url = request.query.get('url')
    if not target_url:
        return web.Response(status=400, text='Missing url parameter')

    try:
        from urllib.parse import urlsplit
        parsed = urlsplit(target_url)
        allowed_domains = ['c411.org', 'torr9.net', 'tr4ker.net', 'gemini-tracker.org', 'themoviedb.org']
        host = parsed.netloc.split(':')[0]
        
        is_allowed = any(host == d or host.endswith('.' + d) for d in allowed_domains)
        if not is_allowed:
            return web.Response(status=403, text='Domain not allowed')

        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/120.0.0.0 Safari/537.36'
        }
        for h in ['Authorization', 'Accept', 'Content-Type']:
            if h in request.headers:
                headers[h] = request.headers[h]

        async with aiohttp.ClientSession() as session:
            async with session.get(target_url, headers=headers) as resp:
                body = await resp.read()
                content_type = resp.headers.get('Content-Type', 'application/json')
                if ';' in content_type:
                    content_type = content_type.split(';')[0].strip()
                
                proxy_resp = web.Response(body=body, status=resp.status, content_type=content_type)
                proxy_resp.headers['Access-Control-Allow-Origin'] = '*'
                proxy_resp.headers['Access-Control-Allow-Methods'] = 'GET, OPTIONS'
                proxy_resp.headers['Access-Control-Allow-Headers'] = '*'
                return proxy_resp
    except Exception as e:
        logging.error(f"[Plugin Proxy] Error proxying {target_url}: {e}")
        return web.Response(status=500, text=str(e))


async def handle_manifest(request):
    """Retourne le manifest de l'addon"""
    config_str = request.match_info.get('config', '')
    config = decode_config(config_str)
    
    if not config:
        return web.Response(status=400, text="Invalid Config")

    # Construction du nom de l'addon avec suffixe optionnel
    addon_name = config.get('_instance_name', 'LeFlux.')
    if not addon_name:
        addon_name = "LeFlux."
    if MANIFEST_TITLE_SUFFIX:
        addon_name += f" {MANIFEST_TITLE_SUFFIX}"
    
    # Description de base (le blurb s'affiche dans la page de config)
    description = "Stream from French Trackers (UNIT3D, Sharewood, YGG, ABN, LaCale, C411, Torr9) via AllDebrid, TorBox, DebridLink ou qBittorrent."

    manifest = {
        "id": "community.aymene69.leflux.",
        "version": APP_VERSION,
        "name": addon_name,
        "description": description,
        "icon": f"https://{request.host}/plugin/logo.svg", # Favicon F.
        "stremioAddonsConfig": STREMIO_ADDONS_CONFIG,
        "types": ["movie", "series"],
        "catalogs": [],
        "resources": ["stream"],
        "idPrefixes": ["tt"],
        "behaviorHints": {
            "configurable": True,
        },
        "beyiond_support": True
    }
    return web.json_response(manifest)

async def handle_manifest_no_config(request):
    """
    Manifest sans configuration (route /manifest.json).
    Si l'utilisateur n'a pas configuré, on utilise la configuration par défaut.
    """
    request.match_info['config'] = 'default'
    return await handle_manifest(request)

async def handle_stream_no_config(request):
    """
    Stream endpoint sans configuration (route /stream/...).
    Si l'utilisateur n'a pas configuré, on utilise la configuration par défaut.
    """
    request.match_info['config'] = 'default'
    return await handle_stream(request)

async def handle_stream(request):
    """Gère la recherche de streams"""
    config_str = request.match_info.get('config', '')
    config = decode_config(config_str)
    if not config:
        return web.json_response({"streams": []})

    stream_type = request.match_info.get('type')
    stream_id = request.match_info.get('id')

    # Parsing ID (tt1234567 ou tt1234567:1:2)
    imdb_id = stream_id
    season = None
    episode = None
    
    if ":" in stream_id:
        parts = stream_id.split(":")
        imdb_id = parts[0]
        season = int(parts[1])
        episode = int(parts[2])

    logging.info(f"Searching for {stream_type} {imdb_id} S{season}E{episode}")

    # Initialisation des services
    tmdb_key = config.get('tmdb_key', '').strip()
    tmdb_service = TMDBService(tmdb_key) if tmdb_key else None
    
    # Services de débridage (optionnels)
    alldebrid_service = None
    torbox_service = None
    debridlink_service = None
    realdebrid_service = None
    
    if config.get('alldebrid_key') and config['alldebrid_key'].strip():
        alldebrid_service = AllDebridService(config['alldebrid_key'])
        logging.info("AllDebrid service initialized")
    
    if config.get('torbox_key') and config['torbox_key'].strip():
        torbox_service = TorBoxService(config['torbox_key'])
        logging.info("TorBox service initialized")
    
    if config.get('debridlink_key') and config['debridlink_key'].strip():
        debridlink_service = DebridLinkService(config['debridlink_key'])
        logging.info("DebridLink service initialized")
    
    if config.get('realdebrid_key') and config['realdebrid_key'].strip():
        realdebrid_service = RealDebridService(config['realdebrid_key'])
        logging.info("Real-Debrid service initialized")
    
    if not alldebrid_service and not torbox_service and not debridlink_service and not realdebrid_service:
        logging.info("No debrid service configured, using qBittorrent fallback")
    
    # Client torrent local (qBittorrent ou TorrServer)
    client_mode = config.get('client_mode')
    if not client_mode:
        if config.get('qbittorrent'):
            client_mode = 'qbittorrent'
        elif config.get('torrserver'):
            client_mode = 'torrserver'
        else:
            client_mode = 'none'

    qbit_torrents_status = {}
    qbit_service = None
    ts_torrents_status = {}
    ts_service = None

    if client_mode == 'qbittorrent' and QBITTORRENT_ENABLE and config.get('qbittorrent'):
        qbit_config = config['qbittorrent']
        if qbit_config.get('host') and qbit_config.get('public_url'):
            qbit_service = QBittorrentService(
                host=qbit_config['host'],
                username=qbit_config.get('username', ''),
                password=qbit_config.get('password', ''),
                public_url_base=qbit_config['public_url']
            )
            logging.info("qBittorrent service initialized")
            
            # Test de connexion (synchrone avec la nouvelle librairie)
            try:
                qbit_service.test_connection()
            except Exception as e:
                logging.error(f"qBittorrent test failed: {e}")

            # Charger la liste des torrents actifs pour identifier ceux déjà téléchargés
            if qbit_service and qbit_service.client:
                try:
                    loop = asyncio.get_running_loop()
                    torrents_list = await loop.run_in_executor(None, qbit_service.client.torrents_info)
                    for t in torrents_list:
                        h = t.get('hash', '').lower()
                        if h:
                            qbit_torrents_status[h] = {
                                'progress': t.get('progress', 0),
                                'state': t.get('state', ''),
                                'name': t.get('name', ''),
                                'size': t.get('size', 0),
                                'tags': t.get('tags', '')
                            }
                    logging.info(f"Loaded {len(qbit_torrents_status)} active torrents from qBittorrent")
                except Exception as e:
                    logging.error(f"Failed to fetch qBittorrent torrents status: {e}")
        else:
            logging.warning("qBittorrent config incomplete, skipping")
    elif client_mode == 'torrserver' and config.get('torrserver'):
        ts_config = config['torrserver']
        if ts_config.get('host'):
            ts_service = TorrServerService(
                host=ts_config['host'],
                username=ts_config.get('username', ''),
                password=ts_config.get('password', '')
            )
            logging.info("TorrServer service initialized")
            
            try:
                await ts_service.test_connection()
            except Exception as e:
                logging.error(f"TorrServer test failed: {e}")

            try:
                async with aiohttp.ClientSession(trust_env=True) as session:
                    url = f"{ts_service.host}/torrents"
                    payload = {"action": "list"}
                    async with session.post(url, json=payload, auth=ts_service.auth, timeout=10) as resp:
                        if resp.status == 200:
                            torrents_list = await resp.json()
                            if isinstance(torrents_list, list):
                                for t in torrents_list:
                                    h = t.get('hash', '').lower()
                                    if h:
                                        ts_torrents_status[h] = {
                                            'name': t.get('title') or t.get('name', ''),
                                            'size': t.get('torrent_size', 0),
                                            'state': t.get('stat_string', '') or t.get('stat', ''),
                                            'raw': t
                                        }
                                logging.info(f"Loaded {len(ts_torrents_status)} active torrents from TorrServer")
            except Exception as e:
                logging.error(f"Failed to fetch TorrServer torrents status: {e}")
        else:
            logging.warning("TorrServer config incomplete, skipping")
    
    # Vérifier qu'au moins un service est configuré
    if not alldebrid_service and not torbox_service and not debridlink_service and not realdebrid_service and not qbit_service and not ts_service:
        logging.error("No debrid or torrent client configured!")
        return web.json_response({"streams": []})
    
    unit3d_results = []
    sharewood_results = []

    # 1. Info Média (pour Sharewood) et Conversion ID (pour UNIT3D)
    # On a besoin des infos textuelles pour Sharewood
    # On a besoin du TMDB ID pour UNIT3D
    
    # Étape 1 : Find by IMDB ID (nécessite une clé TMDb)
    tmdb_id = None
    if tmdb_service:
        tmdb_id = await tmdb_service.get_tmdb_id(imdb_id, stream_type)
    
    # Étape 1.5 : Récupérer Titre/Année pour les trackers qui en ont besoin
    media_info = None
    needs_media_info = True
    
    if needs_media_info and tmdb_id and tmdb_key:
        async with aiohttp.ClientSession(trust_env=True) as session:
            url = f"https://api.themoviedb.org/3/{'movie' if stream_type == 'movie' else 'tv'}/{tmdb_id}"
            params = {"api_key": tmdb_key, "language": "fr-FR"}
            async with session.get(url, params=params) as resp:
                if resp.status == 200:
                    media_info = await resp.json()

    target_title = ""
    original_title = ""
    year = ""
    if media_info:
        target_title = media_info.get('title') or media_info.get('name') or ""
        original_title = media_info.get('original_title') or media_info.get('original_name') or ""
        date = media_info.get('release_date') or media_info.get('first_air_date')
        year = date.split('-')[0] if date else ""

    # 2. Recherche Parallèle (UNIT3D + Sharewood)
    tasks = []

    # Tâche UNIT3D
    if config.get('trackers'):
        logging.info(f"Starting UNIT3D search on {len(config['trackers'])} trackers")
        unit3d_service = Unit3DService(config['trackers'])
        tasks.append(unit3d_service.search_all(
            tmdb_id=tmdb_id,
            imdb_id=imdb_id,
            type=stream_type,
            season=season,
            episode=episode
        ))
    else:
        logging.info("UNIT3D search skipped (no trackers configured)")
        async def empty(): return []
        tasks.append(empty())

    # Tâche Sharewood (Désactivée - Sharewood est mort)
    async def empty_sharewood(): return []
    tasks.append(empty_sharewood())

    # Tâche YGG (Désactivée - non utilisée)
    async def empty_ygg(): return []
    tasks.append(empty_ygg())

    # Tâche ABN
    abn_service = None
    if config.get('abn_username') and config.get('abn_password'):
        logging.info("Starting ABN search")
        abn_service = ABNService(
            username=config.get('abn_username'),
            password=config.get('abn_password')
        )
        
        title = media_info.get('title') or media_info.get('name') if media_info else ""
        original_title = media_info.get('original_title') or media_info.get('original_name') if media_info else ""
        year = ""
        if media_info:
            date = media_info.get('release_date') or media_info.get('first_air_date')
            year = date.split('-')[0] if date else ""

        if stream_type == 'movie':
            tasks.append(wrap_search_task("ABN", "https://abn.lol", abn_service.search_movie(title, year, original_title=original_title)))
        elif stream_type == 'series':
            tasks.append(wrap_search_task("ABN", "https://abn.lol", abn_service.search_series(title, season, episode, original_title=original_title)))
    else:
        async def empty(): return []
        tasks.append(empty())

    # Tâche LaCale
    lacale_key = config.get('lacale_apikey') or config.get('lacale_passkey')
    if lacale_key:
        logging.info("Starting LaCale search")
        lacale_service = LaCaleService(lacale_key)
        if stream_type == 'movie':
            tasks.append(wrap_search_task("LaCale", "https://la-cale.space", lacale_service.search_movie(target_title, year, tmdb_id=tmdb_id, imdb_id=imdb_id)))
        elif stream_type == 'series':
            tasks.append(wrap_search_task("LaCale", "https://la-cale.space", lacale_service.search_series(target_title, season, episode, tmdb_id=tmdb_id, imdb_id=imdb_id)))
    else:
        async def empty(): return []
        tasks.append(empty())

    # Tâche C411
    if config.get('c411_apikey'):
        logging.info("Starting C411 search")
        c411_service = C411Service(config.get('c411_apikey'))
        
        if stream_type == 'movie':
            tasks.append(wrap_search_task("C411", "https://c411.org", c411_service.search_movie(target_title, year, imdb_id=imdb_id, tmdb_id=tmdb_id)))
        elif stream_type == 'series':
            tasks.append(wrap_search_task("C411", "https://c411.org", c411_service.search_series(target_title, season, episode, imdb_id=imdb_id, tmdb_id=tmdb_id)))
    else:
        async def empty(): return []
        tasks.append(empty())

    # Tâche Torr9
    if config.get('torr9_passkey'):
        logging.info("Starting Torr9 search")
        torr9_service = Torr9Service(config.get('torr9_passkey'))
        
        if stream_type == 'movie':
            tasks.append(wrap_search_task("Torr9", "https://api.torr9.net", torr9_service.search_movie(target_title, year, imdb_id=imdb_id, tmdb_id=tmdb_id)))
        elif stream_type == 'series':
            tasks.append(wrap_search_task("Torr9", "https://api.torr9.net", torr9_service.search_series(target_title, season, episode, imdb_id=imdb_id, tmdb_id=tmdb_id)))
    else:
        async def empty(): return []
        tasks.append(empty())

    # Tâche Gemini (G3mini)
    if config.get('gemini_apikey'):
        logging.info("Starting Gemini search")
        gemini_service = GeminiService(config.get('gemini_apikey'))
        if stream_type == 'movie':
            tasks.append(wrap_search_task("Gemini", "https://gemini-tracker.org", gemini_service.search_movie(target_title, year, imdb_id=imdb_id, tmdb_id=tmdb_id)))
        elif stream_type == 'series':
            tasks.append(wrap_search_task("Gemini", "https://gemini-tracker.org", gemini_service.search_series(target_title, season, episode, imdb_id=imdb_id, tmdb_id=tmdb_id)))
    else:
        async def empty_gemini(): return []
        tasks.append(empty_gemini())

    # Tâche TR4KER
    if config.get('tr4ker_apikey'):
        logging.info("Starting TR4KER search")
        tr4ker_service = TR4KERService(config.get('tr4ker_apikey'))
        if stream_type == 'movie':
            tasks.append(wrap_search_task("TR4KER", "https://tr4ker.net", tr4ker_service.search_movie(target_title, year, imdb_id=imdb_id, tmdb_id=tmdb_id)))
        elif stream_type == 'series':
            tasks.append(wrap_search_task("TR4KER", "https://tr4ker.net", tr4ker_service.search_series(target_title, season, episode, imdb_id=imdb_id, tmdb_id=tmdb_id)))
    else:
        async def empty_tr4ker(): return []
        tasks.append(empty_tr4ker())

    # Exécution
    try:
        results_list = await asyncio.gather(*tasks)
        unit3d_results = results_list[0]
        for t in unit3d_results:
            t['source'] = 'unit3d'
            
        sharewood_results = results_list[1] if len(results_list) > 1 else []
        ygg_results = results_list[2] if len(results_list) > 2 else []
        abn_results = results_list[3] if len(results_list) > 3 else []
        lacale_results = results_list[4] if len(results_list) > 4 else []
        c411_results = results_list[5] if len(results_list) > 5 else []
        torr9_results = results_list[6] if len(results_list) > 6 else []
        gemini_results = results_list[7] if len(results_list) > 7 else []
        tr4ker_results = results_list[8] if len(results_list) > 8 else []
    finally:
        # Fermer la session ABN proprement
        if abn_service:
            await abn_service.close()
    
    logging.info(f"Results breakdown: UNIT3D={len(unit3d_results)}, Sharewood={len(sharewood_results)}, YGG={len(ygg_results)}, ABN={len(abn_results)}, LaCale={len(lacale_results)}, C411={len(c411_results)}, Torr9={len(torr9_results)}, Gemini={len(gemini_results)}, TR4KER={len(tr4ker_results)}")
    
    # Fusion et Déduplication
    all_torrents = unit3d_results + sharewood_results + ygg_results + abn_results + lacale_results + c411_results + torr9_results + gemini_results + tr4ker_results
    
    # Filtrage par taille si configuré
    max_size_gb = config.get('max_size', 0)
    if max_size_gb > 0:
        max_size_bytes = max_size_gb * 1024 * 1024 * 1024  # Conversion Go -> bytes
        before_filter = len(all_torrents)
        all_torrents = [t for t in all_torrents if t.get('size', 0) <= max_size_bytes]
        filtered_count = before_filter - len(all_torrents)
        if filtered_count > 0:
            logging.info(f"Filtered {filtered_count} torrents exceeding {max_size_gb} Go")
    
    
    # Filtrage par type de fichier (Vidéos uniquement)
    before_filter = len(all_torrents)
    all_torrents = [t for t in all_torrents if is_video_file(t.get('name', ''))]
    filtered_count = before_filter - len(all_torrents)
    if filtered_count > 0:
        logging.info(f"Filtered {filtered_count} non-video files")

    unique_torrents = {}
    
    for t in all_torrents:
        # Filtrage Strict pour UNIT3D (Anti-bruit ID)
        if t.get('source') == 'unit3d': # ou le nom interne utilisé dans le service
            # UNIT3D est cherché par ID, donc le résultat DOIT avoir l'ID correspondant
            # ou au moins ne pas avoir un ID contradictoire (0 ou différent)
            
            res_tmdb = t.get('tmdb_id') or t.get('tmdb')
            res_imdb = t.get('imdb_id') or t.get('imdb')
            
            # Si TMDB ID présent et non nul, il doit matcher
            if res_tmdb and str(res_tmdb) != "0" and tmdb_id and str(res_tmdb) != str(tmdb_id):
                # logging.info(f"Filtered UNIT3D (Wrong TMDB): {t.get('name')} {res_tmdb}!={tmdb_id}")
                continue
                
            # Si IMDB ID présent et non nul, il doit matcher (en ignorant 'tt')
            if res_imdb and str(res_imdb) != "0" and imdb_id:
                clean_res = str(res_imdb).replace('tt', '')
                clean_req = str(imdb_id).replace('tt', '')
                if clean_res != clean_req:
                    # logging.info(f"Filtered UNIT3D (Wrong IMDB): {t.get('name')} {res_imdb}!={imdb_id}")
                    continue
                
            # Si UNIT3D renvoie un résultat sans ID, on l'accepte quand même
            # (la vérification par titre pourrait être ajoutée ici si nécessaire)
            # if (not res_tmdb or str(res_tmdb) == "0") and (not res_imdb or str(res_imdb) == "0"):
            #      # Filtrage par titre si nécessaire
            #      pass

        # Filtrage par titre et année (Vérification systématique pour éviter les erreurs de mapping des trackers)
        if stream_type in ('movie', 'series'):
            if not check_title_match(t.get('name', ''), target_title, original_title, year=year, is_movie=(stream_type == 'movie')):
                logging.info(f"Filtered out (Title mismatch): {t.get('name')} for target {target_title} (Original: {original_title}, Year: {year}) [Tracker: {t.get('tracker_name')}]")
                continue

        # Filtrage Série (SxxExx)
        # Si c'est une série, on vérifie que le titre correspond à la saison/épisode demandé
        # pour éviter d'afficher E03 quand on veut E07 (souvent le cas avec recherche floue)
        if stream_type == 'series' and season is not None:
            if not check_season_episode(t.get('name', ''), season, episode):
                logging.info(f"Filtered out: {t.get('name')} (Wrong Season/Episode) [Tracker: {t.get('tracker_name')}]")
                continue

        # Info Hash est la clé unique (minuscule pour éviter les doublons de casse)
        ih = t.get('info_hash')
        if ih:
            ih = ih.lower()
            if ih not in unique_torrents:
                unique_torrents[ih] = t
            # Optionnel : Si on voulait fusionner les sources, on pourrait le faire ici
            # else:
            #     unique_torrents[ih]['tracker_name'] += f" / {t.get('tracker_name')}"
            
    # Liste finale des torrents uniques
    torrents = list(unique_torrents.values())
    
    # Initialisation de la liste des streams
    streams = []
    scheme = request.headers.get('X-Forwarded-Proto', request.scheme)
    host_url = f"{scheme}://{request.host}/addon"
    
    # 3. Check disponibilité sur les services de débridage (uniquement si on a des torrents des trackers)
    availability = {}
    debrid_provider = None
    
    if torrents:
        logging.info(f"Total unique torrents (UNIT3D + Sharewood + YGG + ABN + LaCale + C411 + Torr9 + Gemini + TR4KER): {len(torrents)}")
        if alldebrid_service:
            hashes = [t['info_hash'] for t in torrents if t.get('info_hash')]
            availability = await alldebrid_service.check_availability(hashes)
            debrid_provider = "alldebrid"
            logging.info(f"AllDebrid: {len([v for v in availability.values() if v])} cached torrents")
        
        elif torbox_service:
            # TorBox check (en parallèle pour la vitesse)
            hashes = [t['info_hash'] for t in torrents if t.get('info_hash')]
            results = await asyncio.gather(
                *[torbox_service.check_availability(h) for h in hashes],
                return_exceptions=True
            )
            for h, result in zip(hashes, results):
                if not isinstance(result, Exception) and result:
                    availability[h] = result
            debrid_provider = "torbox"
            logging.info(f"TorBox: {len([v for v in availability.values() if v])} cached torrents")
        
        elif debridlink_service:
            # DebridLink check (en parallèle)
            hashes = [t['info_hash'] for t in torrents if t.get('info_hash')]
            availability = await debridlink_service.check_availability(hashes)
            debrid_provider = "debridlink"
            logging.info(f"DebridLink: {len([v for v in availability.values() if v])} cached torrents")
        
        elif realdebrid_service:
            # Real-Debrid check
            hashes = [t['info_hash'] for t in torrents if t.get('info_hash')]
            availability = await realdebrid_service.check_availability(hashes)
            debrid_provider = "realdebrid"
            logging.info(f"Real-Debrid: {len([v for v in availability.values() if v])} cached torrents")

    # 4. Générer les streams
    local_torrents = []
    cached_torrents = []
    uncached_torrents = []
    
    if torrents:
        for torrent in torrents:
            info_hash = torrent.get('info_hash')
            if not info_hash:
                continue
                
            clean_hash = info_hash.lower().strip()
            
            # 1. Est-ce que le torrent est déjà dans le client torrent local ?
            if client_mode == 'qbittorrent':
                t_name = qbit_torrents_status[clean_hash].get('name', '') if clean_hash in qbit_torrents_status else ''
                files_exist = os.path.exists(os.path.join("/downloads", t_name)) if t_name else False
                if clean_hash in qbit_torrents_status and files_exist:
                    local_torrents.append((torrent, clean_hash))
                    continue
            elif client_mode == 'torrserver':
                if clean_hash in ts_torrents_status:
                    local_torrents.append((torrent, clean_hash))
                    continue
                
            # 2. Sinon, vérifier s'il est debrid-cached
            if alldebrid_service:
                clean_hash = alldebrid_service._clean_hash(info_hash)
                is_cached = availability.get(clean_hash, False)
            elif torbox_service:
                clean_hash = info_hash.lower().strip()
                is_cached = availability.get(clean_hash, False)
            elif debridlink_service:
                clean_hash = info_hash.lower().strip()
                is_cached = availability.get(clean_hash, False)
            elif realdebrid_service:
                clean_hash = info_hash.lower().strip()
                is_cached = availability.get(clean_hash, False)
            else:
                clean_hash = info_hash.lower().strip()
                is_cached = False
            
            if is_cached:
                cached_torrents.append((torrent, clean_hash))
            else:
                uncached_torrents.append((torrent, clean_hash))

    # 4d. Trouver d'autres torrents locaux déjà présents dans le client torrent mais non retournés par les trackers
    if client_mode == 'qbittorrent' and qbit_service and qbit_service.client and (target_title or original_title):
        local_hashes = {clean_hash for _, clean_hash in local_torrents}
        for clean_hash, status_info in qbit_torrents_status.items():
            if clean_hash in local_hashes:
                continue
                
            # Vérifier si les fichiers locaux existent
            torrent_name = status_info.get('name', '')
            files_exist = os.path.exists(os.path.join("/downloads", torrent_name)) if torrent_name else False
            if not files_exist:
                continue
                
            # Vérifier si le titre correspond
            if not check_title_match(status_info['name'], target_title, original_title, year=year, is_movie=(stream_type == 'movie')):
                continue
                
            # Si c'est une série, vérifier la saison et l'épisode (ou si le torrent contient le fichier)
            if stream_type == 'series' and season is not None:
                if not check_season_episode(status_info['name'], season, episode):
                    # Si le nom ne matche pas directement, vérifier le contenu du torrent (ex: pack de saison)
                    try:
                        loop = asyncio.get_running_loop()
                        target_file = await loop.run_in_executor(
                            None,
                            lambda: qbit_service.get_torrent_files(
                                clean_hash,
                                max_retries=1,
                                season=int(season) if season else None,
                                episode=int(episode) if episode else None,
                                fast_mode=True
                            )
                        )
                        if not target_file:
                            continue
                    except Exception:
                        continue
                        
            # On a trouvé un match ! On crée un objet torrent factice
            tracker_tag = status_info.get('tags', '')
            tracker_name = 'qBittorrent'
            if tracker_tag:
                possible_trackers = [tag.strip() for tag in tracker_tag.split(',') if tag.strip()]
                if possible_trackers:
                    tracker_name = possible_trackers[0]
                    
            dummy_torrent = {
                'name': status_info['name'],
                'info_hash': clean_hash,
                'size': status_info['size'],
                'tracker_name': tracker_name,
                'link': ''
            }
            local_torrents.append((dummy_torrent, clean_hash))
            local_hashes.add(clean_hash)
    elif client_mode == 'torrserver' and ts_service and (target_title or original_title):
        local_hashes = {clean_hash for _, clean_hash in local_torrents}
        for clean_hash, status_info in ts_torrents_status.items():
            if clean_hash in local_hashes:
                continue
                
            # Vérifier si le titre correspond
            if not check_title_match(status_info['name'], target_title, original_title, year=year, is_movie=(stream_type == 'movie')):
                continue
                
            # Si c'est une série, vérifier la saison et l'épisode
            if stream_type == 'series' and season is not None:
                if not check_season_episode(status_info['name'], season, episode):
                    continue
                    
            # On a trouvé un match ! On crée un objet torrent factice
            dummy_torrent = {
                'name': status_info['name'],
                'info_hash': clean_hash,
                'size': status_info['size'],
                'tracker_name': 'TorrServer',
                'link': ''
            }
            local_torrents.append((dummy_torrent, clean_hash))
            local_hashes.add(clean_hash)

    # Tri et priorisation des torrents locaux
    if client_mode == 'qbittorrent':
        def get_local_sort_key(item):
            torrent, clean_hash = item
            status_info = qbit_torrents_status.get(clean_hash, {})
            prog = status_info.get('progress', 0.0)
            state = status_info.get('state', '').lower()
            if prog >= 1.0 or any(s in state for s in ('up', 'uploading', 'seed')):
                status_pri = 1
            elif any(s in state for s in ('pause', 'check', 'checking')):
                status_pri = 2
            elif 'downloading' in state or 'download' in state or 'dl' in state:
                status_pri = 0
            else:
                status_pri = 2
            return (status_pri, torrent.get('size', 0))
        local_torrents.sort(key=get_local_sort_key)
    elif client_mode == 'torrserver':
        local_torrents.sort(key=lambda item: item[0].get('size', 0))

    # Tri personnalisé par qualité et taille pour les torrents de debrid/trackers
    def group_and_sort_torrents(torrent_list):
        group_4k = []
        group_1080p = []
        group_720p = []
        group_others = []
        
        for item in torrent_list:
            torrent, _ = item
            meta = parse_torrent_name(torrent.get('name', ''))
            
            # Utilisation de la détection robuste de qualité
            q = str(meta.get('quality', '')).lower()
            raw_name = torrent.get('name', '').lower()
            
            if q == '2160p' or '4k' in q or 'uhd' in q or '2160' in q or '2160p' in raw_name or '4k' in raw_name or 'uhd' in raw_name:
                group_4k.append(item)
            elif q == '1080p' or '1080' in q or 'fhd' in q or '1080p' in raw_name or '1080i' in raw_name or 'fhd' in raw_name:
                group_1080p.append(item)
            elif q == '720p' or '720' in q or 'hd' in q or '720p' in raw_name or '720i' in raw_name:
                group_720p.append(item)
            else:
                group_others.append(item)
                
        def sort_by_size(item):
            torrent, _ = item
            return torrent.get('size', 0)
            
        group_4k.sort(key=sort_by_size)
        group_1080p.sort(key=sort_by_size)
        group_720p.sort(key=sort_by_size)
        group_others.sort(key=sort_by_size)
        
        # Prendre le premier (le plus léger) de chaque résolution
        top_1_4k = group_4k[:1]
        top_1_1080p = group_1080p[:1]
        top_1_720p = group_720p[:1]
        top_1_others = group_others[:1]
        
        # Le reste trié par poids
        remaining_all = group_4k[1:] + group_1080p[1:] + group_720p[1:] + group_others[1:]
        remaining_all.sort(key=sort_by_size)
        
        return top_1_1080p + top_1_4k + top_1_720p + top_1_others + remaining_all

    cached_torrents = group_and_sort_torrents(cached_torrents)
    uncached_torrents = group_and_sort_torrents(uncached_torrents)

    logging.info(f"Local: {len(local_torrents)}, Cached: {len(cached_torrents)}, Uncached: {len(uncached_torrents)}")
    
    # 4a. Streams locaux (déjà dans le client)
    if client_mode == 'qbittorrent':
        for torrent, clean_hash in local_torrents:
            status_info = qbit_torrents_status.get(clean_hash, {})
            tracker_tag = status_info.get('tags', '')
            raw_tracker = torrent.get('tracker_name', 'qBittorrent')
            if tracker_tag:
                possible_trackers = [tag.strip() for tag in tracker_tag.split(',') if tag.strip()]
                if possible_trackers:
                    raw_tracker = possible_trackers[0]

            if raw_tracker.startswith('http'):
                from urllib.parse import urlparse
                domain = urlparse(raw_tracker).hostname or raw_tracker
                clean_name = domain.split('.')[0].capitalize()
            else:
                clean_name = raw_tracker

            # Taille par défaut (taille totale du torrent)
            size_bytes = torrent.get('size', 0)
            has_exact_size = False
            
            # Si c'est une série et qu'on a la saison et l'épisode, chercher la taille du fichier spécifique dans qBittorrent
            if stream_type == 'series' and season is not None and episode is not None and qbit_service and qbit_service.client:
                try:
                    # Appeler qBittorrent pour obtenir la liste des fichiers
                    files = qbit_service.client.torrents_files(torrent_hash=clean_hash)
                    if files:
                        # Chercher parmi les fichiers (triés par taille décroissante)
                        sorted_files = sorted(files, key=lambda x: x.size, reverse=True)
                        for f in sorted_files:
                            fs, fe = parse_season_episode_from_path(f.name)
                            if (fs == season and fe == episode) or (fs is None and fe == episode):
                                size_bytes = f.size
                                has_exact_size = True
                                break
                        # fallback to only episode match if not found
                        if not has_exact_size:
                            for f in sorted_files:
                                fs, fe = parse_season_episode_from_path(f.name)
                                if fe == episode:
                                    size_bytes = f.size
                                    has_exact_size = True
                                    break
                except Exception as e:
                    logging.debug(f"Could not get individual file size from qBittorrent: {e}")

            # Estimation de la taille de l'épisode si pas de taille exacte trouvée
            if not has_exact_size and stream_type == 'series' and season is not None and episode is not None:
                size_bytes = estimate_episode_size(torrent.get('name', ''), size_bytes)

            size_str = format_size(size_bytes)
            meta = parse_torrent_name(torrent.get('name', ''))
            
            status_info = qbit_torrents_status[clean_hash]
            prog = status_info['progress']
            state = status_info['state'].lower()
            
            # Vérifier si les fichiers locaux existent réellement sur le disque dans /downloads
            torrent_name = status_info.get('name', '')
            files_exist = os.path.exists(os.path.join("/downloads", torrent_name)) if torrent_name else False
            
            if (prog >= 1.0 or any(s in state for s in ('up', 'uploading', 'seed'))) and files_exist:
                source_type = 'server_completed'
                state_info = None
            elif any(s in state for s in ('pause',)) and files_exist:
                source_type = 'server_other'
                state_info = f"En pause ({prog:.1%})"
            elif any(s in state for s in ('check', 'checking')) and files_exist:
                source_type = 'server_other'
                state_info = f"Vérification ({prog:.1%})"
            elif ('downloading' in state or 'download' in state or 'dl' in state) and files_exist:
                source_type = 'server_downloading'
                state_info = f"En cours ({prog:.1%})"
            else:
                source_type = 'server_other'
                state_info = None

            left_name, full_title = format_stream_card(
                torrent, meta, size_str, source_type, clean_name, state_info
            )
            
            import urllib.parse
            download_link = torrent.get('link') or torrent.get('download_link') or ''
            encoded_link = urllib.parse.quote(download_link, safe='')
            
            resolve_url = f"{host_url}/{config_str}/resolve/qbit/{clean_hash}?link={encoded_link}&tracker={urllib.parse.quote(clean_name)}&title={urllib.parse.quote(torrent.get('name', ''), safe='')}"
            
            if season is not None and episode is not None:
                resolve_url += f"&season={season}&episode={episode}"
            elif stream_type == 'movie':
                resolve_url += "&type=movie"

            streams.append({
                "name": left_name,
                "title": full_title,
                "url": resolve_url,
                "filename": torrent.get('name', ''),
                "size": torrent.get('size', 0),
                "quality": meta.get('quality', ''),
                "codec": meta.get('codec', ''),
                "release_type": meta.get('release_type', ''),
                "language": meta.get('language', ''),
                "_source_type": source_type,
                "_progress": prog
            })
    elif client_mode == 'torrserver':
        for torrent, clean_hash in local_torrents:
            status_info = ts_torrents_status.get(clean_hash, {})
            raw_tracker = torrent.get('tracker_name', 'TorrServer')
            size_bytes = torrent.get('size', 0)
            if stream_type == 'series' and season is not None and episode is not None:
                size_bytes = estimate_episode_size(torrent.get('name', ''), size_bytes)
            size_str = format_size(size_bytes)
            meta = parse_torrent_name(torrent.get('name', ''))
            
            left_name, full_title = format_stream_card(
                torrent, meta, size_str, 'torrserver', raw_tracker
            )
            
            import urllib.parse
            download_link = torrent.get('link') or torrent.get('download_link') or ''
            encoded_link = urllib.parse.quote(download_link, safe='')
            
            resolve_url = f"{host_url}/{config_str}/resolve/torrserver/{clean_hash}?link={encoded_link}&tracker={urllib.parse.quote(raw_tracker)}&title={urllib.parse.quote(torrent.get('name', ''), safe='')}"
            if season is not None and episode is not None:
                resolve_url += f"&season={season}&episode={episode}"
            elif stream_type == 'movie':
                resolve_url += "&type=movie"

            streams.append({
                "name": left_name,
                "title": full_title,
                "url": resolve_url,
                "filename": torrent.get('name', ''),
                "size": torrent.get('size', 0),
                "quality": meta.get('quality', ''),
                "codec": meta.get('codec', ''),
                "release_type": meta.get('release_type', ''),
                "language": meta.get('language', ''),
                "_source_type": 'server_completed'
            })
        


    # 4b. Streams débridés (cachés)
    for torrent, clean_hash in cached_torrents:
        # Extraire un nom propre pour les trackers UNIT3D (tracker_name = URL)
        raw_tracker = torrent.get('tracker_name', 'UNIT3D')
        if raw_tracker.startswith('http'):
            # https://theoldschool.cc -> TheOldSchool
            from urllib.parse import urlparse
            domain = urlparse(raw_tracker).hostname or raw_tracker
            clean_name = domain.split('.')[0].capitalize()
        else:
            clean_name = raw_tracker

        size_bytes = torrent.get('size', 0)
        if stream_type == 'series' and season is not None and episode is not None:
            size_bytes = estimate_episode_size(torrent.get('name', ''), size_bytes)
            
        size_str = format_size(size_bytes)
        meta = parse_torrent_name(torrent.get('name', ''))
        
        left_name, full_title = format_stream_card(
            torrent, meta, size_str, 'debrid', clean_name
        )
        
        # URL de résolution (utilise le provider configuré)
        import urllib.parse
        encoded_torrent_name = urllib.parse.quote(torrent.get('name', ''), safe='')
        resolve_url = f"{host_url}/{config_str}/resolve/{debrid_provider}/{clean_hash}?title={encoded_torrent_name}"
        
        if season is not None and episode is not None:
            resolve_url += f"&season={season}&episode={episode}"
        elif stream_type == 'movie':
            resolve_url += "&type=movie"

        streams.append({
            "name": left_name,
            "title": full_title,
            "url": resolve_url,
            "filename": torrent.get('name', ''),
            "size": torrent.get('size', 0),
            "quality": meta.get('quality', ''),
            "codec": meta.get('codec', ''),
            "release_type": meta.get('release_type', ''),
            "language": meta.get('language', ''),
            "_source_type": 'debrid'
        })
        


    # 4c. Streams locaux non cachés (si configuré)
    # Si on a des torrents cachés ou déjà locaux, on n'affiche pas les non-cachés
    if client_mode == 'qbittorrent' and qbit_service and uncached_torrents:
        # Filtrer les torrents YGG sans passkey (ne peuvent pas être téléchargés)
        has_ygg_passkey = config.get('ygg_passkey') and config.get('ygg_passkey').strip()
        if not has_ygg_passkey:
            before_filter = len(uncached_torrents)
            uncached_torrents = [(t, h) for t, h in uncached_torrents if t.get('source') != 'ygg']
            filtered = before_filter - len(uncached_torrents)
            if filtered > 0:
                logging.info(f"qBittorrent: Filtered {filtered} YGG torrents (no passkey for download)")
        
        limit = 10 if (alldebrid_service or torbox_service or debridlink_service or realdebrid_service) else 25  # Plus de résultats si pas de debrid
        logging.info(f"qBittorrent: Processing {min(len(uncached_torrents), limit)} torrents (out of {len(uncached_torrents)} available)")
        
        qbit_added = 0
        for torrent, clean_hash in uncached_torrents[:limit]:
            download_link = torrent.get('link') or torrent.get('download_link')
            if not download_link:
                logging.debug(f"Skipping torrent without download link: {torrent.get('name')}")
                continue
            
            # Extraire un nom propre pour les trackers UNIT3D
            raw_tracker = torrent.get('tracker_name', 'UNIT3D')
            if raw_tracker.startswith('http'):
                from urllib.parse import urlparse
                domain = urlparse(raw_tracker).hostname or raw_tracker
                clean_name = domain.split('.')[0].capitalize()
            else:
                clean_name = raw_tracker

            size_bytes = torrent.get('size', 0)
            if stream_type == 'series' and season is not None and episode is not None:
                size_bytes = estimate_episode_size(torrent.get('name', ''), size_bytes)
                
            size_str = format_size(size_bytes)
            meta = parse_torrent_name(torrent.get('name', ''))
            
            left_name, full_title = format_stream_card(
                torrent, meta, size_str, 'qbit', clean_name
            )
            
            import urllib.parse
            encoded_link = urllib.parse.quote(download_link, safe='')
            
            # On passe la config encodée pour avoir accès aux credentials qBittorrent
            resolve_url = f"{host_url}/{config_str}/resolve/qbit/{clean_hash}?link={encoded_link}&tracker={urllib.parse.quote(clean_name)}&title={urllib.parse.quote(torrent.get('name', ''), safe='')}"
            
            if season is not None and episode is not None:
                resolve_url += f"&season={season}&episode={episode}"
            elif stream_type == 'movie':
                resolve_url += "&type=movie"

            streams.append({
                "name": left_name,
                "title": full_title,
                "url": resolve_url,
                "filename": torrent.get('name', ''),
                "size": torrent.get('size', 0),
                "quality": meta.get('quality', ''),
                "codec": meta.get('codec', ''),
                "release_type": meta.get('release_type', ''),
                "language": meta.get('language', ''),
                "_source_type": 'qbit'
            })
            
            qbit_added += 1
        
        logging.info(f"qBittorrent: Added {qbit_added} streams")
        
    elif client_mode == 'torrserver' and ts_service and uncached_torrents:
        # Filtrer les torrents YGG sans passkey (ne peuvent pas être téléchargés)
        has_ygg_passkey = config.get('ygg_passkey') and config.get('ygg_passkey').strip()
        if not has_ygg_passkey:
            before_filter = len(uncached_torrents)
            uncached_torrents = [(t, h) for t, h in uncached_torrents if t.get('source') != 'ygg']
            filtered = before_filter - len(uncached_torrents)
            if filtered > 0:
                logging.info(f"TorrServer: Filtered {filtered} YGG torrents (no passkey for download)")
        
        limit = 10 if (alldebrid_service or torbox_service or debridlink_service or realdebrid_service) else 25  # Plus de résultats si pas de debrid
        logging.info(f"TorrServer: Processing {min(len(uncached_torrents), limit)} torrents (out of {len(uncached_torrents)} available)")
        
        ts_added = 0
        for torrent, clean_hash in uncached_torrents[:limit]:
            download_link = torrent.get('link') or torrent.get('download_link')
            if not download_link:
                logging.debug(f"Skipping torrent without download link: {torrent.get('name')}")
                continue
            
            # Extraire un nom propre pour les trackers UNIT3D
            raw_tracker = torrent.get('tracker_name', 'UNIT3D')
            if raw_tracker.startswith('http'):
                from urllib.parse import urlparse
                domain = urlparse(raw_tracker).hostname or raw_tracker
                clean_name = domain.split('.')[0].capitalize()
            else:
                clean_name = raw_tracker

            size_bytes = torrent.get('size', 0)
            if stream_type == 'series' and season is not None and episode is not None:
                size_bytes = estimate_episode_size(torrent.get('name', ''), size_bytes)
                
            size_str = format_size(size_bytes)
            meta = parse_torrent_name(torrent.get('name', ''))
            
            left_name, full_title = format_stream_card(
                torrent, meta, size_str, 'torrserver', clean_name
            )
            
            import urllib.parse
            encoded_link = urllib.parse.quote(download_link, safe='')
            
            # On passe la config encodée pour avoir accès aux credentials TorrServer
            resolve_url = f"{host_url}/{config_str}/resolve/torrserver/{clean_hash}?link={encoded_link}&tracker={urllib.parse.quote(clean_name)}&title={urllib.parse.quote(torrent.get('name', ''), safe='')}"
            
            if season is not None and episode is not None:
                resolve_url += f"&season={season}&episode={episode}"
            elif stream_type == 'movie':
                resolve_url += "&type=movie"

            streams.append({
                "name": left_name,
                "title": full_title,
                "url": resolve_url,
                "filename": torrent.get('name', ''),
                "size": torrent.get('size', 0),
                "quality": meta.get('quality', ''),
                "codec": meta.get('codec', ''),
                "release_type": meta.get('release_type', ''),
                "language": meta.get('language', ''),
                "_source_type": 'server_completed'
            })
            
            ts_added += 1
            
        logging.info(f"TorrServer: Added {ts_added} streams")

    # ── Tri personnalisé final de la liste des streams ────────────────
    # Règles :
    # 1. Les streams téléchargés (server_completed) vont TOUJOURS en haut de la liste.
    # 2. On s'assure d'avoir au moins un 1080p, un 4K et un 720p au début de la liste (les téléchargés comptent pour leur résolution).
    # 3. Les compléments pour atteindre ce set {1080p, 4K, 720p} sont les plus légers de chaque résolution manquante.
    # 4. Le reste des streams suit ensuite.
    
    remaining = list(streams)
    
    # 1. Identifier tous les téléchargés (server_completed)
    downloaded_streams = [s for s in remaining if s.get('_source_type') == 'server_completed']
    for s in downloaded_streams:
        remaining.remove(s)
        
    # 1b. Identifier tous les téléchargements en cours (server_downloading), triés par % décroissant
    downloading_streams = [s for s in remaining if s.get('_source_type') == 'server_downloading']
    downloading_streams.sort(key=lambda x: x.get('_progress', 0.0), reverse=True)
    for s in downloading_streams:
        remaining.remove(s)
        
    # Helper pour déterminer la qualité d'un stream
    def is_q_value(s, q_value):
        q = str(s.get('quality', '')).lower()
        fname = str(s.get('filename', '')).lower()
        if q_value == '1080p':
            return (q == '1080p' or '1080' in q or 'fhd' in q or '1080p' in fname or '1080i' in fname or 'fhd' in fname)
        elif q_value == '4k':
            return (q == '2160p' or '4k' in q or 'uhd' in q or '2160' in q or '2160p' in fname or '4k' in fname or 'uhd' in fname)
        elif q_value == '720p':
            return (q == '720p' or '720' in q or 'hd' in q or '720p' in fname or '720i' in fname)
        return False

    # 2. Vérifier quelles résolutions sont déjà couvertes par les téléchargés et en cours
    has_1080p = any(is_q_value(s, '1080p') for s in downloaded_streams + downloading_streams)
    has_4k = any(is_q_value(s, '4k') for s in downloaded_streams + downloading_streams)
    has_720p = any(is_q_value(s, '720p') for s in downloaded_streams + downloading_streams)

    # Helper pour extraire le plus léger d'une résolution dans remaining
    def get_lightest_for_quality(rem_list, q_value):
        candidates = [s for s in rem_list if is_q_value(s, q_value)]
        if candidates:
            lightest = min(candidates, key=lambda x: x.get('size', 0))
            return lightest
        return None

    # 3. Sélectionner les complémentaires (les plus légers) dans le bon ordre (1080p, puis 4k, puis 720p)
    top_fillers = []
    
    if not has_1080p:
        lightest_1080p = get_lightest_for_quality(remaining, '1080p')
        if lightest_1080p:
            top_fillers.append(lightest_1080p)
            remaining.remove(lightest_1080p)
            
    if not has_4k:
        lightest_4k = get_lightest_for_quality(remaining, '4k')
        if lightest_4k:
            top_fillers.append(lightest_4k)
            remaining.remove(lightest_4k)
            
    if not has_720p:
        lightest_720p = get_lightest_for_quality(remaining, '720p')
        if lightest_720p:
            top_fillers.append(lightest_720p)
            remaining.remove(lightest_720p)

    # 4. Assembler la liste finale : téléchargés en premier, puis en cours (%), puis fillers, puis le reste trié par poids
    remaining.sort(key=lambda x: x.get('size', 0))
    sorted_streams = downloaded_streams + downloading_streams + top_fillers + remaining

    # Nettoyer les clés temporaires
    for s in sorted_streams:
        s.pop('_source_type', None)
        s.pop('_progress', None)

    streams = sorted_streams

    logging.info(f"Returning {len(streams)} streams to Stremio")
    return web.json_response({"streams": streams})

async def wait_for_buffering(qbit_service, info_hash, target_progress=None, max_wait_seconds=12, target_file=None):
    """Attend que le téléchargement atteigne le seuil spécifié et que les headers/footers soient écrits"""
    try:
        loop = asyncio.get_running_loop()
        start_time = time.time()
        
        logging.info(f"⏳ qBittorrent: Checking progress and pieces before redirection for hash {info_hash[:8]}...")
        
        while time.time() - start_time < max_wait_seconds:
            info = await loop.run_in_executor(
                None,
                lambda: qbit_service.client.torrents_info(torrent_hashes=info_hash)
            )
            
            if not info:
                break
                
            torrent_info = info[0]
            progress = torrent_info.get('progress', 0.0)
            state = torrent_info.get('state', '')
            dl_speed = torrent_info.get('dlspeed', 0)  # en octets/s
            total_size = torrent_info.get('size', 0)
            downloaded = progress * total_size
            
            # Déterminer le target_progress dynamiquement:
            # L'utilisateur souhaite un cache assez intéressant mais pas trop fort (ex: 3%).
            # On cap à 15 Mo pour ne pas attendre éternellement sur les gros fichiers.
            curr_target = target_progress
            if curr_target is None:
                # 3% du fichier, mais limité à 15 Mo maximum pour démarrer vite
                target_bytes = min(total_size * 0.03, 15 * 1024 * 1024)
                curr_target = target_bytes / total_size if total_size > 0 else 0.01

            # 1. Trouver la plage de pièces du fichier cible
            start_piece = None
            end_piece = None
            try:
                files_info = await loop.run_in_executor(
                    None,
                    lambda: qbit_service.client.torrents_files(torrent_hash=info_hash)
                )
                if files_info and target_file:
                    for f in files_info:
                        if f.get('name') == target_file:
                            piece_range = f.get('piece_range')
                            if piece_range and len(piece_range) >= 2:
                                start_piece = piece_range[0]
                                end_piece = piece_range[1]
                            break
            except Exception as e:
                logging.debug(f"Error querying torrent files: {e}")

            # 2. Obtenir l'état des pièces du torrent
            piece_states = None
            try:
                piece_states = await loop.run_in_executor(
                    None,
                    lambda: qbit_service.client.torrents_piece_states(torrent_hash=info_hash)
                )
            except Exception as e:
                logging.debug(f"Error querying piece states: {e}")

            # 3. Vérifier si les premières pièces du fichier sont prêtes (état >= 2)
            # On ne vérifie plus la fin (last_pieces) car ça bloque le lancement trop longtemps
            headers_ready = False
            if piece_states:
                if start_piece is not None and end_piece is not None:
                    # Les 5 premières pièces du fichier (environ 5 Mo)
                    first_pieces = range(start_piece, min(start_piece + 5, end_piece + 1))
                    headers_ready = all(piece_states[p] >= 2 for p in first_pieces if p < len(piece_states))
                else:
                    # Fallback sur les pièces globales du torrent
                    if len(piece_states) > 0:
                        headers_ready = all(piece_states[p] >= 2 for p in range(min(5, len(piece_states))))
            else:
                # Si l'API des pièces échoue, on considère les headers prêts après 5 Mo
                if downloaded >= 5 * 1024 * 1024:
                    headers_ready = True
            
            # Critère de redirection :
            # - Les headers doivent être prêts (headers_ready) pour éviter que le player lise des zéros
            # - Et la progression doit être suffisante (curr_target)
            if (headers_ready and progress >= curr_target) or progress >= 1.0 or any(s in state for s in ('up', 'UP', 'uploading', 'seed')):
                logging.info(f"✅ qBittorrent: Ready! Headers OK. Progress: {progress:.3%} ({downloaded / (1024*1024):.1f} MB). Redirecting...")
                break
                
            progress_remaining = curr_target - progress
            bytes_remaining = progress_remaining * total_size
            
            speed_mb = dl_speed / (1024 * 1024)
            
            if dl_speed > 0:
                eta_seconds = bytes_remaining / dl_speed
                eta_str = f"{int(eta_seconds)}s" if eta_seconds < 60 else f"{int(eta_seconds // 60)}m {int(eta_seconds % 60)}s"
            else:
                eta_str = "Infini (en attente de pairs)"
                
            logging.info(
                f"📥 qBittorrent: {progress:.3%} / {curr_target:.3%} "
                f"({speed_mb:.2f} MB/s) - Headers ready: {headers_ready} - ETA: {eta_str}..."
            )
            
            await asyncio.sleep(0.5)
    except Exception as e:
        logging.error(f"Error during download buffering wait: {e}")

async def handle_qbit_stream(request):
    config_str = request.match_info.get('config', '')
    config = decode_config(config_str)
    if not config:
        return web.Response(status=400, text="Invalid config")
        
    info_hash = request.match_info.get('hash')
    file_path = request.match_info.get('file_path')
    
    # Sécurité anti-traversée
    local_path = os.path.abspath(os.path.join('/downloads', file_path))
    if not local_path.startswith('/downloads'):
        return web.Response(status=403, text="Access denied")
        
    if not os.path.exists(local_path):
        return web.Response(status=404, text="File not found")
        
    file_size = os.path.getsize(local_path)
    
    # Analyser l'en-tête de plage (Range)
    range_header = request.headers.get('Range')
    start = 0
    end = file_size - 1
    is_partial = False
    
    if range_header:
        try:
            kind, range_val = range_header.strip().split('=')
            if kind == 'bytes':
                start_str, end_str = range_val.split('-')
                start = int(start_str) if start_str else 0
                if end_str:
                    end = int(end_str)
                is_partial = True
        except Exception:
            pass
            
    end = min(end, file_size - 1)
    if start > end:
        return web.Response(status=416, headers={"Content-Range": f"bytes */{file_size}"})
        
    chunk_size = end - start + 1
    
    # Récupérer la config qBittorrent
    qbit_config = config.get('qbittorrent')
    if not qbit_config:
        return web.Response(status=400, text="qBittorrent not configured")
        
    qbit_service = QBittorrentService(
        host=qbit_config['host'],
        username=qbit_config.get('username', ''),
        password=qbit_config.get('password', ''),
        public_url_base=qbit_config['public_url']
    )
    
    loop = asyncio.get_running_loop()
    
    # 1. Récupérer les propriétés pour avoir la taille des pièces (piece_size)
    piece_size = None
    try:
        properties = await loop.run_in_executor(
            None,
            lambda: qbit_service.client.torrents_properties(torrent_hash=info_hash)
        )
        if properties:
            piece_size = properties.get('piece_size')
    except Exception as e:
        logging.debug(f"Stream Proxy: Error getting torrent properties: {e}")
        
    # 2. Récupérer la plage de pièces du fichier cible
    start_piece = None
    try:
        files_info = await loop.run_in_executor(
            None,
            lambda: qbit_service.client.torrents_files(torrent_hash=info_hash)
        )
        if files_info:
            for f in files_info:
                if f.get('name') == file_path:
                    piece_range = f.get('piece_range')
                    if piece_range and len(piece_range) >= 2:
                        start_piece = piece_range[0]
                    break
    except Exception as e:
        logging.debug(f"Stream Proxy: Error getting torrent files: {e}")
        
    # 3. Attendre que la pièce contenant le début du Range soit prête
    if piece_size and start_piece is not None:
        first_needed_piece = start_piece + (start // piece_size)
        
        logging.debug(f"Stream Proxy: Client requested byte offset {start}. Waiting for piece {first_needed_piece}...")
        
        start_wait = time.time()
        # On attend au maximum 45 secondes que la pièce soit téléchargée
        while time.time() - start_wait < 45:
            try:
                piece_states = await loop.run_in_executor(
                    None,
                    lambda: qbit_service.client.torrents_piece_states(torrent_hash=info_hash)
                )
                if piece_states and first_needed_piece < len(piece_states):
                    if piece_states[first_needed_piece] >= 2:
                        logging.debug(f"Stream Proxy: Piece {first_needed_piece} is ready after {time.time() - start_wait:.1f}s.")
                        break
            except Exception:
                pass
            await asyncio.sleep(0.1)
            
    # Deviner le mime type
    import mimetypes
    mime_type, _ = mimetypes.guess_type(local_path)
    if not mime_type:
        mime_type = 'video/mp4'

    # Servir le flux
    response = web.StreamResponse(
        status=206 if is_partial else 200,
        reason="Partial Content" if is_partial else "OK",
        headers={
            "Accept-Ranges": "bytes",
            "Content-Type": mime_type,
            "Access-Control-Allow-Origin": "*",
            "Access-Control-Allow-Methods": "GET, OPTIONS",
            "Access-Control-Allow-Headers": "Range, Content-Type",
        }
    )
    
    if is_partial:
        response.headers["Content-Range"] = f"bytes {start}-{end}/{file_size}"
        
    response.content_length = chunk_size
    await response.prepare(request)
    
    try:
        # Lire le fichier depuis le disque et l'envoyer
        with open(local_path, "rb") as f:
            f.seek(start)
            bytes_to_send = chunk_size
            while bytes_to_send > 0:
                chunk = f.read(min(bytes_to_send, 128 * 1024))
                if not chunk:
                    break
                await response.write(chunk)
                bytes_to_send -= len(chunk)
    except Exception as e:
        logging.debug(f"Stream Proxy: connection closed during download streaming: {e}")
        
    return response

async def handle_torrserver_stream(request):
    config_str = request.match_info.get('config', '')
    config = decode_config(config_str)
    if not config:
        return web.Response(status=400, text="Invalid config")
        
    info_hash = request.match_info.get('hash')
    file_index = request.match_info.get('index')
    
    # Retrieve torrserver config
    ts_config = config.get('torrserver')
    if not ts_config:
        return web.Response(status=400, text="TorrServer not configured")
        
    ts_host = ts_config.get('host', 'http://torrserver:8090').rstrip('/')
    
    # Forward headers like Range, User-Agent, Accept-Encoding, etc.
    headers = {}
    if 'Range' in request.headers:
        headers['Range'] = request.headers['Range']
    if 'User-Agent' in request.headers:
        headers['User-Agent'] = request.headers['User-Agent']
        
    # Standard basic auth if configured
    username = ts_config.get('username', '')
    password = ts_config.get('password', '')
    auth = None
    if username and password:
        auth = aiohttp.BasicAuth(username, password)
        
    # Request stream from TorrServer
    url = f"{ts_host}/stream/fname?link={info_hash}&index={file_index}&play"
    
    # We will pipe the request using chunked streaming
    session = aiohttp.ClientSession(trust_env=True)
    try:
        async with session.get(url, headers=headers, auth=auth, timeout=None) as resp:
            response = web.StreamResponse(
                status=resp.status,
                reason=resp.reason,
                headers={
                    "Accept-Ranges": "bytes",
                    "Content-Type": resp.headers.get("Content-Type", "video/mp4"),
                    "Access-Control-Allow-Origin": "*",
                    "Access-Control-Allow-Methods": "GET, OPTIONS",
                    "Access-Control-Allow-Headers": "Range, Content-Type",
                }
            )
            if "Content-Range" in resp.headers:
                response.headers["Content-Range"] = resp.headers["Content-Range"]
            if "Content-Length" in resp.headers:
                response.content_length = int(resp.headers["Content-Length"])
                
            await response.prepare(request)
            
            async for chunk in resp.content.iter_chunked(128 * 1024):
                await response.write(chunk)
                
            return response
    except Exception as e:
        logging.error(f"TorrServer Stream Proxy Error: {e}")
        return web.Response(status=500, text=f"Stream proxy error: {e}")
    finally:
        await session.close()

async def handle_resolve(request):
    """Résout le lien Debrid ou qBittorrent au moment de la lecture"""
    # Récupérer la config depuis l'URL (/{config}/resolve/...)
    config_str = request.match_info.get('config', '')
    config = decode_config(config_str)
    
    if not config:
        return web.Response(status=400, text="Invalid config")
    
    service_name = request.match_info.get('service', 'alldebrid')
    info_hash = request.match_info.get('hash')
    
    # Récupération des paramètres optionnels
    season = request.query.get('season')
    episode = request.query.get('episode')
    media_type = request.query.get('type')
    
    # Logging playback history
    try:
        title_param = request.query.get('title')
        if title_param:
            import datetime
            import urllib.parse
            clean_title = urllib.parse.unquote(title_param)
            log_line = f"[{datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] Resolved: {clean_title} (Hash: {info_hash}, Service: {service_name})\n"
            history_path = "/app/config/history.log"
            os.makedirs(os.path.dirname(history_path), exist_ok=True)
            with open(history_path, "a", encoding="utf-8") as hf:
                hf.write(log_line)
            logging.info(f"📋 Playback logged: {clean_title}")
    except Exception as le:
        logging.debug(f"Failed to log playback history: {le}")
    
    # === MODE TorrServer ===
    if service_name == 'torrserver':
        ts_config = config.get('torrserver')
        if not ts_config:
            return web.Response(status=400, text="TorrServer not configured")
            
        ts_service = TorrServerService(
            host=ts_config['host'],
            username=ts_config.get('username', ''),
            password=ts_config.get('password', '')
        )
        
        download_link = request.query.get('link')
        if not download_link:
            return web.Response(status=400, text="Missing download link")
            
        import urllib.parse
        download_link = urllib.parse.unquote(download_link)
        
        torrent_data = None
        if 'abn.lol' in download_link or 'abnormal.ws' in download_link:
            if config.get('abn_username') and config.get('abn_password'):
                abn_service = ABNService(
                    username=config.get('abn_username'),
                    password=config.get('abn_password')
                )
                try:
                    torrent_data = await abn_service.download_torrent(download_link)
                finally:
                    await abn_service.close()
        
        if not torrent_data:
            if download_link.startswith('magnet:'):
                torrent_data = download_link
            else:
                async with aiohttp.ClientSession(trust_env=True) as session:
                    async with session.get(download_link) as resp:
                        if resp.status == 200:
                            torrent_data = await resp.read()
                            
        if not torrent_data:
            return web.Response(status=502, text="Failed to retrieve torrent link or data")
            
        title = request.query.get('tracker', 'TorrServer')
        file_index = await ts_service.manage_stream(
            torrent_data,
            info_hash,
            is_file=(not isinstance(torrent_data, str) or not torrent_data.startswith('magnet:')),
            season=int(season) if season else None,
            episode=int(episode) if episode else None,
            title=title
        )
        
        if file_index is not None:
            proto = request.headers.get('X-Forwarded-Proto', 'https')
            host = request.headers.get('X-Forwarded-Host', request.host)
            stream_url = f"{proto}://{host}/addon/{config_str}/stream/torrserver/{info_hash}/{file_index}"
            logging.info(f"TorrServer resolve: Redirecting to local stream proxy: {stream_url}")
            raise web.HTTPFound(finalize_stream_url(stream_url, config))
        else:
            return web.Response(status=404, text="Could not resolve TorrServer stream file index")

    # === MODE qBittorrent ===
    if service_name == 'qbit':
        # Récupérer la config qBittorrent
        qbit_config = config.get('qbittorrent')
        if not qbit_config:
            return web.Response(status=400, text="qBittorrent not configured")
        
        qbit_service = QBittorrentService(
            host=qbit_config['host'],
            username=qbit_config.get('username', ''),
            password=qbit_config.get('password', ''),
            public_url_base=qbit_config['public_url']
        )
        
        # Tenter d'abord une résolution directe si le torrent est déjà dans qBittorrent
        try:
            loop = asyncio.get_running_loop()
            target_file = await loop.run_in_executor(
                None,
                lambda: qbit_service.get_torrent_files(
                    info_hash,
                    max_retries=1,  # 1 tentative
                    season=int(season) if season else None,
                    episode=int(episode) if episode else None,
                    fast_mode=True
                )
            )
            # Vérifier si les fichiers physiques du torrent existent toujours sur le disque
            if target_file and os.path.exists(os.path.join("/downloads", target_file)):
                # Forcer le téléchargement séquentiel et reprendre si en pause
                await loop.run_in_executor(None, qbit_service.configure_sequential, info_hash)
                try:
                    await loop.run_in_executor(None, qbit_service.client.torrents_resume, torrent_hashes=info_hash)
                except Exception as resume_err:
                    logging.debug(f"Could not resume torrent: {resume_err}")
                
                import urllib.parse
                safe_path = urllib.parse.quote(target_file)
                # Rediriger vers notre proxy de streaming intelligent
                proto = request.headers.get('X-Forwarded-Proto', 'https')
                host = request.headers.get('X-Forwarded-Host', request.host)
                stream_url = f"{proto}://{host}/addon/{config_str}/stream/qbit/{info_hash}/{safe_path}"
                logging.info(f"qBittorrent: Direct local resolution succeeded for {info_hash[:8]}: {stream_url}")
                # Attendre le buffer
                await wait_for_buffering(qbit_service, info_hash, target_file=target_file)
                raise web.HTTPFound(finalize_stream_url(stream_url, config))
            elif target_file:
                # Le torrent est toujours enregistré dans qBittorrent mais ses fichiers locaux ont été supprimés.
                # On force qBittorrent à revérifier (recheck) et reprendre (resume) pour retélécharger.
                logging.warning(f"qBittorrent: Local file {target_file} is missing from disk. Forcing recheck & resume.")
                await loop.run_in_executor(None, qbit_service.client.torrents_recheck, torrent_hashes=info_hash)
                await loop.run_in_executor(None, qbit_service.client.torrents_resume, torrent_hashes=info_hash)
                # Attendre le buffer
                await wait_for_buffering(qbit_service, info_hash, target_file=target_file)
                import urllib.parse
                safe_path = urllib.parse.quote(target_file)
                # Rediriger vers notre proxy de streaming intelligent
                proto = request.headers.get('X-Forwarded-Proto', 'https')
                host = request.headers.get('X-Forwarded-Host', request.host)
                stream_url = f"{proto}://{host}/addon/{config_str}/stream/qbit/{info_hash}/{safe_path}"
                raise web.HTTPFound(finalize_stream_url(stream_url, config))
        except web.HTTPFound:
            raise
        except Exception as e:
            logging.debug(f"Direct resolution skipped: {e}")

        download_link = request.query.get('link')
        if not download_link:
            return web.Response(status=400, text="Missing download link")
        
        # Décoder le lien
        import urllib.parse
        download_link = urllib.parse.unquote(download_link)
        
        # Télécharger le .torrent
        logging.info(f"Downloading torrent from: {download_link[:100]}...")
        
        # Vérifier si c'est un lien ABN qui nécessite une authentification
        if 'abn.lol' in download_link or 'abnormal.ws' in download_link:
            if config.get('abn_username') and config.get('abn_password'):
                abn_service = ABNService(
                    username=config.get('abn_username'),
                    password=config.get('abn_password')
                )
                try:
                    torrent_data = await abn_service.download_torrent(download_link)
                    if not torrent_data:
                        logging.error("Failed to download .torrent from ABN")
                        return web.Response(status=502, text="Failed to download torrent file from ABN")
                finally:
                    await abn_service.close()
            else:
                logging.error("ABN credentials not configured")
                return web.Response(status=400, text="ABN credentials required")
        else:
            # Téléchargement standard
            async with aiohttp.ClientSession(trust_env=True) as session:
                async with session.get(download_link) as resp:
                    if resp.status != 200:
                        logging.error(f"Failed to download .torrent: {resp.status}")
                        return web.Response(status=502, text="Failed to download torrent file")
                    torrent_data = await resp.read()
        
        logging.info(f"Downloaded {len(torrent_data)} bytes, adding to qBittorrent...")
        
        # Ajouter et configurer dans qBittorrent (dans un exécuteur pour ne pas bloquer l'event loop)
        tracker = request.query.get('tracker', 'qBittorrent')
        loop = asyncio.get_running_loop()
        stream_url = await loop.run_in_executor(
            None,
            lambda: qbit_service.manage_stream(
                torrent_data, 
                info_hash, 
                is_file=True,
                season=int(season) if season else None,
                episode=int(episode) if episode else None,
                tracker=tracker
            )
        )
        
        if stream_url:
            logging.info(f"qBittorrent stream ready: {stream_url}")
            # Extraire le nom de fichier à partir de stream_url
            target_file_name = None
            try:
                import urllib.parse
                base_url = qbit_service.public_url_base.rstrip('/')
                if stream_url.startswith(base_url):
                    path_part = stream_url[len(base_url):].lstrip('/')
                    target_file_name = urllib.parse.unquote(path_part)
            except Exception:
                pass
            
            # Rediriger vers notre proxy de streaming intelligent
            if target_file_name:
                import urllib.parse
                proto = request.headers.get('X-Forwarded-Proto', 'https')
                host = request.headers.get('X-Forwarded-Host', request.host)
                safe_path = urllib.parse.quote(target_file_name)
                stream_url = f"{proto}://{host}/addon/{config_str}/stream/qbit/{info_hash}/{safe_path}"
                logging.info(f"qBittorrent: Routing stream through local proxy: {stream_url}")

            # Attendre le buffer
            await wait_for_buffering(qbit_service, info_hash, target_file=target_file_name)
            raise web.HTTPFound(finalize_stream_url(stream_url, config))
        else:
            return web.Response(status=404, text="Could not start qBittorrent stream")
    
    # === MODE AllDebrid ===
    elif service_name == 'alldebrid':
        alldebrid_key = config.get('alldebrid_key')
        if not alldebrid_key:
            return web.Response(status=400, text="AllDebrid not configured")
        
        debrid_service = AllDebridService(alldebrid_key)
        
        stream_url = await debrid_service.unlock_magnet(
            info_hash, 
            season=int(season) if season else None, 
            episode=int(episode) if episode else None,
            media_type=media_type
        )
        
        if stream_url:
            raise web.HTTPFound(finalize_stream_url(stream_url, config))
        else:
            return web.Response(status=404, text="Could not resolve stream or file not found in torrent")
    
    # === MODE TorBox ===
    elif service_name == 'torbox':
        logging.info(f"TorBox resolve: Starting with hash={info_hash}, season={season}, episode={episode}")
        
        torbox_key = config.get('torbox_key')
        if not torbox_key:
            return web.Response(status=400, text="TorBox not configured")
        
        debrid_service = TorBoxService(torbox_key)
        
        # Construire le magnet à partir du hash
        magnet_link = f"magnet:?xt=urn:btih:{info_hash}"
        
        # Déterminer le type de stream
        if season and episode:
            stream_type = "series"
        else:
            stream_type = "movie"
        
        stream_url = await debrid_service.get_stream_link(
            magnet_link,
            stream_type,
            season=int(season) if season else None,
            episode=int(episode) if episode else None
        )
        
        if stream_url:
            logging.info(f"TorBox resolve: Redirecting to: {stream_url}")
            raise web.HTTPFound(finalize_stream_url(stream_url, config))
        else:
            logging.error(f"TorBox resolve: Failed to get stream URL for hash {info_hash}")
            return web.Response(status=404, text="Could not resolve TorBox stream")
    
    # === MODE DebridLink ===
    elif service_name == 'debridlink':
        logging.info(f"DebridLink resolve: Starting with hash={info_hash}, season={season}, episode={episode}")
        
        debridlink_key = config.get('debridlink_key')
        if not debridlink_key:
            return web.Response(status=400, text="DebridLink not configured")
        
        debrid_service = DebridLinkService(debridlink_key)
        
        stream_url = await debrid_service.unlock_magnet(
            info_hash,
            season=int(season) if season else None,
            episode=int(episode) if episode else None,
            media_type=media_type
        )
        
        if stream_url:
            logging.info(f"DebridLink resolve: Redirecting to: {stream_url}")
            raise web.HTTPFound(finalize_stream_url(stream_url, config))
        else:
            logging.error(f"DebridLink resolve: Failed to get stream URL for hash {info_hash}")
            return web.Response(status=404, text="Could not resolve DebridLink stream")
    
    # === MODE Real-Debrid ===
    elif service_name == 'realdebrid':
        logging.info(f"Real-Debrid resolve: Starting with hash={info_hash}, season={season}, episode={episode}")
        
        realdebrid_key = config.get('realdebrid_key')
        if not realdebrid_key:
            return web.Response(status=400, text="Real-Debrid not configured")
        
        debrid_service = RealDebridService(realdebrid_key)
        
        stream_url = await debrid_service.unlock_magnet(
            info_hash,
            season=int(season) if season else None,
            episode=int(episode) if episode else None,
            media_type=media_type
        )
        
        if stream_url:
            logging.info(f"Real-Debrid resolve: Redirecting to: {stream_url}")
            raise web.HTTPFound(finalize_stream_url(stream_url, config))
        else:
            logging.error(f"Real-Debrid resolve: Failed to get stream URL for hash {info_hash}")
            return web.Response(status=404, text="Could not resolve Real-Debrid stream")
    
    else:
        return web.Response(status=400, text=f"Unknown service: {service_name}")

async def handle_get_downloads(request):
    downloads_dir = "/downloads"
    if not os.path.exists(downloads_dir):
        return web.json_response({
            "files": [],
            "current_path": "",
            "parent_path": "",
            "disk_usage": {"total": 0, "used": 0, "free": 0, "percent": 0}
        })
    
    subpath = request.query.get('path', '').strip()
    target_dir = os.path.abspath(os.path.join(downloads_dir, subpath))
    if not target_dir.startswith(os.path.abspath(downloads_dir)):
        return web.Response(status=403, text="Access denied")
    
    if not os.path.exists(target_dir):
        return web.Response(status=404, text="Directory not found")
        
    def list_dir():
        items = []
        try:
            for entry in os.scandir(target_dir):
                if entry.name == 'incomplete':
                    continue
                try:
                    stat = entry.stat()
                    rel_path = os.path.relpath(entry.path, downloads_dir)
                    if entry.is_dir():
                        items.append({
                            "name": entry.name,
                            "path": rel_path,
                            "size": 0,
                            "mtime": stat.st_mtime,
                            "is_dir": True
                        })
                    else:
                        items.append({
                            "name": entry.name,
                            "path": rel_path,
                            "size": stat.st_size,
                            "mtime": stat.st_mtime,
                            "is_dir": False
                        })
                except Exception:
                    pass
        except Exception as e:
            logging.error(f"Error listing downloads: {e}")
        return items

    loop = asyncio.get_running_loop()
    files_list = await loop.run_in_executor(None, list_dir)
    
    import shutil
    try:
        total, used, free = shutil.disk_usage(downloads_dir)
        percent = round((used / total) * 100, 1) if total > 0 else 0
        disk_usage = {
            "total": total,
            "used": used,
            "free": free,
            "percent": percent
        }
    except Exception as e:
        logging.error(f"Error getting disk usage: {e}")
        disk_usage = {"total": 0, "used": 0, "free": 0, "percent": 0}

    rel_current = os.path.relpath(target_dir, downloads_dir)
    if rel_current == ".":
        rel_current = ""
        
    parent_path = ""
    if rel_current:
        parent_path = os.path.dirname(rel_current)
        if parent_path == ".":
            parent_path = ""

    return web.json_response({
        "files": files_list,
        "current_path": rel_current,
        "parent_path": parent_path,
        "disk_usage": disk_usage
    })

async def handle_delete_download(request):
    downloads_dir = "/downloads"
    
    # Bulk delete via JSON payload of paths
    if request.has_body:
        try:
            body = await request.json()
            paths = body.get('paths', [])
            if paths:
                def delete_paths():
                    count = 0
                    for path in paths:
                        target_path = os.path.abspath(os.path.join(downloads_dir, path))
                        if not target_path.startswith(os.path.abspath(downloads_dir)):
                            continue
                        if 'incomplete' in target_path.split(os.sep):
                            continue
                        try:
                            if os.path.isdir(target_path):
                                import shutil
                                shutil.rmtree(target_path)
                                count += 1
                            elif os.path.exists(target_path):
                                os.remove(target_path)
                                count += 1
                        except Exception:
                            pass
                    # Delete empty directories
                    for root, dirs, files in os.walk(downloads_dir, topdown=False):
                        if 'incomplete' in root.split(os.sep) or root == downloads_dir:
                            continue
                        try:
                            if not os.listdir(root):
                                os.rmdir(root)
                        except Exception:
                            pass
                    return count

                loop = asyncio.get_running_loop()
                deleted_count = await loop.run_in_executor(None, delete_paths)
                return web.json_response({"success": True, "deleted_count": deleted_count})
        except Exception as e:
            logging.error(f"Error parsing JSON bulk delete body: {e}")
            return web.Response(status=400, text=f"Invalid JSON body: {e}")
    
    # Bulk delete files older than days
    older_than_days = request.query.get('older_than_days')
    if older_than_days:
        try:
            days = float(older_than_days)
            cutoff = time.time() - (days * 24 * 3600)
            
            def bulk_delete():
                count = 0
                for root, dirs, files in os.walk(downloads_dir):
                    if 'incomplete' in root.split(os.sep):
                        continue
                    for file in files:
                        full_path = os.path.join(root, file)
                        try:
                            stat = os.stat(full_path)
                            if stat.st_mtime < cutoff:
                                os.remove(full_path)
                                count += 1
                        except Exception:
                            pass
                # Delete empty directories
                for root, dirs, files in os.walk(downloads_dir, topdown=False):
                    if 'incomplete' in root.split(os.sep) or root == downloads_dir:
                        continue
                    try:
                        if not os.listdir(root):
                            os.rmdir(root)
                    except Exception:
                        pass
                return count

            loop = asyncio.get_running_loop()
            deleted_count = await loop.run_in_executor(None, bulk_delete)
            return web.json_response({"success": True, "deleted_count": deleted_count})
        except ValueError:
            return web.Response(status=400, text="Invalid older_than_days parameter")
        except Exception as e:
            return web.Response(status=500, text=str(e))
            
    # Delete single file/folder
    path = request.query.get('path')
    if not path:
        return web.Response(status=400, text="Missing path parameter")
    
    # Prevent directory traversal
    target_path = os.path.abspath(os.path.join(downloads_dir, path))
    if not target_path.startswith(os.path.abspath(downloads_dir)):
        return web.Response(status=403, text="Access denied")
    
    if 'incomplete' in target_path.split(os.sep):
        return web.Response(status=403, text="Cannot delete temporary downloads")

    def do_delete():
        if os.path.isdir(target_path):
            import shutil
            shutil.rmtree(target_path)
        elif os.path.exists(target_path):
            os.remove(target_path)

    try:
        loop = asyncio.get_running_loop()
        await loop.run_in_executor(None, do_delete)
        return web.json_response({"success": True})
    except Exception as e:
        return web.Response(status=500, text=str(e))

async def handle_stack_status(request):
    config_path = "/app/config/config.json"
    vpn_enabled = False
    tunnel_enabled = True
    
    if os.path.exists(config_path):
        try:
            with open(config_path, "r", encoding="utf-8") as f:
                cfg = json.load(f)
                vpn_enabled = cfg.get("vpn", {}).get("enabled", False)
                net_mode = cfg.get("network", {}).get("mode", "cloudflare")
                tunnel_enabled = (net_mode == "cloudflare")
        except:
            pass
            
    services_cfg = cfg.get("services", {}) if os.path.exists(config_path) else {}
    qbit_svc = services_cfg.get("qbittorrent", True)
    torr_svc = services_cfg.get("torrserver", True)
    joal_svc = services_cfg.get("joal", True)

    containers = ["frenchio-caddy", "frenchio-addon"]
    if qbit_svc:
        containers.append("frenchio-qbittorrent")
    if torr_svc:
        containers.append("torrserver")
    if joal_svc:
        containers.append("frenchio-joal")
    if vpn_enabled:
        containers.append("frenchio-vpn")
    if tunnel_enabled:
        containers.append("frenchio-tunnel")

    container_statuses = {}
    all_running = True
    
    try:
        connector = aiohttp.UnixConnector(path='/var/run/docker.sock')
        async with aiohttp.ClientSession(connector=connector) as session:
            for container in containers:
                try:
                    url = f"http://localhost/containers/{container}/json"
                    async with session.get(url) as resp:
                        if resp.status == 200:
                            data = await resp.json()
                            state = data.get("State", {})
                            status = state.get("Status", "unknown")
                            running = state.get("Running", False)
                            health = state.get("Health", {}).get("Status", "none")
                            
                            container_statuses[container] = {
                                "status": status,
                                "running": running,
                                "health": health
                            }
                            if not running:
                                all_running = False
                            if health in ("starting", "unhealthy"):
                                all_running = False
                        else:
                            container_statuses[container] = {"status": "error", "running": False}
                            all_running = False
                except Exception as e:
                    container_statuses[container] = {"status": "error", "running": False, "error": str(e)}
                    all_running = False
    except Exception as e:
        logging.error(f"Error querying docker socket: {e}")
        all_running = False

    return web.json_response({
        "startup_time": STARTUP_TIME,
        "all_running": all_running,
        "containers": container_statuses
    })

async def handle_stack_restart(request):
    config_path = "/app/config/config.json"
    vpn_enabled = False
    tunnel_enabled = True
    
    if os.path.exists(config_path):
        try:
            with open(config_path, "r", encoding="utf-8") as f:
                cfg = json.load(f)
                vpn_enabled = cfg.get("vpn", {}).get("enabled", False)
                net_mode = cfg.get("network", {}).get("mode", "cloudflare")
                tunnel_enabled = (net_mode == "cloudflare")
        except:
            pass
            
    services_cfg_r = cfg.get("services", {}) if os.path.exists(config_path) else {}
    qbit_svc_r = services_cfg_r.get("qbittorrent", True)
    torr_svc_r = services_cfg_r.get("torrserver", True)
    joal_svc_r = services_cfg_r.get("joal", True)

    containers = ["frenchio-caddy", "frenchio-addon"]
    if qbit_svc_r:
        containers.append("frenchio-qbittorrent")
    if torr_svc_r:
        containers.append("torrserver")
    if joal_svc_r:
        containers.append("frenchio-joal")
    if vpn_enabled:
        containers.append("frenchio-vpn")
    if tunnel_enabled:
        containers.append("frenchio-tunnel")
    
    async def perform_restart():
        await asyncio.sleep(1)
        connector = aiohttp.UnixConnector(path='/var/run/docker.sock')
        async with aiohttp.ClientSession(connector=connector) as session:
            for container in containers:
                try:
                    logging.info(f"Stack restart: Restarting {container}")
                    url = f"http://localhost/containers/{container}/restart"
                    async with session.post(url) as resp:
                        if resp.status not in (204, 200):
                            err = await resp.text()
                            logging.error(f"Failed to restart {container}: {err}")
                except Exception as e:
                    logging.error(f"Error restarting {container}: {e}")
                    
    asyncio.create_task(perform_restart())
    return web.json_response({"success": True, "message": "Stack restart initiated", "startup_time": STARTUP_TIME})

def parse_docker_logs(raw_data: bytes) -> str:
    """Parses Docker multiplexed logs (when TTY is false)."""
    try:
        offset = 0
        decoded_parts = []
        while offset < len(raw_data):
            if offset + 8 > len(raw_data):
                break
            stream_type = raw_data[offset]
            if stream_type not in (0, 1, 2):
                return raw_data.decode('utf-8', errors='replace')
            
            size = int.from_bytes(raw_data[offset+4:offset+8], byteorder='big')
            if offset + 8 + size > len(raw_data):
                return raw_data.decode('utf-8', errors='replace')
            
            payload = raw_data[offset+8:offset+8+size]
            decoded_parts.append(payload.decode('utf-8', errors='replace'))
            offset += 8 + size
            
        if decoded_parts:
            return "".join(decoded_parts)
        else:
            return raw_data.decode('utf-8', errors='replace')
    except Exception:
        return raw_data.decode('utf-8', errors='replace')

async def handle_get_logs(request):
    """Fetch logs from Docker container for a given service."""
    service_id = request.match_info.get('service_id')
    
    mapping = {
        "caddy": "frenchio-caddy",
        "frenchio": "frenchio-addon",
        "qbittorrent": "frenchio-qbittorrent",
        "torrserver": "torrserver",
        "joal": "frenchio-joal",
        "vpn": "frenchio-vpn",
        "tunnel": "frenchio-tunnel"
    }
    
    container_name = mapping.get(service_id)
    if not container_name:
        return web.json_response({"success": False, "message": f"Service inconnu: {service_id}"}, status=400)
        
    tail = request.query.get('tail', '200')
    try:
        tail = str(int(tail))
    except ValueError:
        tail = '200'
        
    try:
        connector = aiohttp.UnixConnector(path='/var/run/docker.sock')
        async with aiohttp.ClientSession(connector=connector) as session:
            url = f"http://localhost/containers/{container_name}/logs?stdout=true&stderr=true&tail={tail}&timestamps=false"
            async with session.get(url) as resp:
                if resp.status == 200:
                    raw_data = await resp.read()
                    logs_str = parse_docker_logs(raw_data)
                    return web.json_response({"success": True, "logs": logs_str})
                else:
                    err_msg = await resp.text()
                    return web.json_response({"success": False, "message": f"Erreur Docker ({resp.status}): {err_msg}"}, status=500)
    except Exception as e:
        return web.json_response({"success": False, "message": f"Erreur de communication Docker: {str(e)}"}, status=500)

# --- UPDATE SYSTEM BACKGROUND TASK & HANDLERS ---
UPDATE_STATUS = {
    "last_check": None,
    "status": "idle",
    "frenchio": {
        "up_to_date": True,
        "modified_files": {}, # file_key: patch_text
        "version": "Indéterminé"
    },
    "joal": {
        "up_to_date": True,
        "local_digest": "",
        "registry_digest": "",
        "version": "Indéterminé"
    },
    "qbittorrent": {
        "up_to_date": True,
        "local_digest": "",
        "registry_digest": "",
        "version": "Indéterminé"
    },
    "torrserver": {
        "up_to_date": True,
        "local_digest": "",
        "registry_digest": "",
        "version": "Indéterminé"
    },
    "vpn": {
        "up_to_date": True,
        "local_digest": "",
        "registry_digest": "",
        "version": "Indéterminé"
    }
}

def load_update_status():
    global UPDATE_STATUS
    path = "/app/config/update_status.json"
    if os.path.exists(path):
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
                # Structure checks
                for key in ["last_check", "frenchio", "joal", "qbittorrent", "torrserver", "vpn"]:
                    if key in data and key in UPDATE_STATUS:
                        if isinstance(data[key], dict) and isinstance(UPDATE_STATUS[key], dict):
                            UPDATE_STATUS[key].update(data[key])
                        else:
                            UPDATE_STATUS[key] = data[key]
        except Exception as e:
            logging.debug(f"Error loading update_status.json: {e}")

def save_update_status():
    path = "/app/config/update_status.json"
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(UPDATE_STATUS, f, indent=4)
    except Exception as e:
        logging.debug(f"Error saving update_status.json: {e}")

load_update_status()

def get_frenchio_version():
    version_str = APP_VERSION
    try:
        import subprocess
        # 1. Exact tag match
        res = subprocess.run(
            ["git", "-C", "/app", "describe", "--tags", "--exact-match"],
            capture_output=True, text=True
        )
        if res.returncode == 0 and res.stdout.strip():
            return res.stdout.strip()
            
        # 2. Describe tags
        res = subprocess.run(
            ["git", "-C", "/app", "describe", "--tags"],
            capture_output=True, text=True
        )
        if res.returncode == 0 and res.stdout.strip():
            return f"{version_str} ({res.stdout.strip()})"
            
        # 3. Rev parse short HEAD
        res = subprocess.run(
            ["git", "-C", "/app", "rev-parse", "--short", "HEAD"],
            capture_output=True, text=True
        )
        if res.returncode == 0 and res.stdout.strip():
            return f"{version_str} (dev-{res.stdout.strip()})"
    except Exception:
        pass
    return version_str

async def get_container_version(container_name):
    try:
        connector = aiohttp.UnixConnector(path='/var/run/docker.sock')
        async with aiohttp.ClientSession(connector=connector) as session:
            url = f"http://localhost/containers/{container_name}/json"
            async with session.get(url) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    config = data.get("Config", {})
                    labels = config.get("Labels", {})
                    version = labels.get("org.opencontainers.image.version") or labels.get("version")
                    if not version:
                        env = config.get("Env", [])
                        for item in env:
                            if item.startswith("VERSION="):
                                version = item.split("=")[1]
                                break
                    return version or "latest"
    except Exception:
        pass
    return "Indéterminé"

def get_git_remote_repo():
    try:
        import configparser
        config_path = "/app/.git/config"
        if os.path.exists(config_path):
            config = configparser.ConfigParser()
            config.read(config_path)
            if 'remote "origin"' in config and 'url' in config['remote "origin"']:
                url = config['remote "origin"']['url']
                if url.endswith(".git"):
                    url = url[:-4]
                parts = url.replace(":", "/").split("/")
                if len(parts) >= 2:
                    return f"{parts[-2]}/{parts[-1]}"
    except Exception as e:
        logging.debug(f"Updater: Error parsing .git/config: {e}")
    return "bastonus/Frenchio"

async def get_local_image_digest(image_name):
    try:
        connector = aiohttp.UnixConnector(path='/var/run/docker.sock')
        async with aiohttp.ClientSession(connector=connector) as session:
            url = f"http://localhost/images/{image_name}/json"
            async with session.get(url) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    repo_digests = data.get("RepoDigests", [])
                    if repo_digests:
                        return repo_digests[0].split("@")[-1]
                    return data.get("Id", "")
    except Exception as e:
        logging.debug(f"Updater: Error getting local image digest for {image_name}: {e}")
    return ""

async def get_registry_image_digest(repo_name):
    try:
        if repo_name.startswith("ghcr.io/"):
            path = repo_name.replace("ghcr.io/", "", 1)
            token_url = f"https://ghcr.io/token?service=ghcr.io&scope=repository:{path}:pull"
            async with aiohttp.ClientSession() as session:
                async with session.get(token_url) as token_resp:
                    if token_resp.status == 200:
                        token_data = await token_resp.json()
                        token = token_data.get("token")
                        if token:
                            manifest_url = f"https://ghcr.io/v2/{path}/manifests/latest"
                            headers = {
                                "Authorization": f"Bearer {token}",
                                "Accept": "application/vnd.docker.distribution.manifest.v2+json, application/vnd.oci.image.index.v1+json, application/vnd.oci.image.manifest.v1+json"
                            }
                            async with session.get(manifest_url, headers=headers) as manifest_resp:
                                if manifest_resp.status == 200:
                                    digest = manifest_resp.headers.get("docker-content-digest")
                                    if not digest:
                                        etag = manifest_resp.headers.get("etag")
                                        if etag and etag.startswith('"') and etag.endswith('"'):
                                            digest = etag.strip('"')
                                        elif etag:
                                            digest = etag.strip()
                                    return digest or ""
            return ""

        url = f"https://registry.hub.docker.com/v2/repositories/{repo_name}/tags/latest"
        async with aiohttp.ClientSession() as session:
            async with session.get(url) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    digest = data.get("digest")
                    if not digest:
                        images = data.get("images", [])
                        if images:
                            digest = images[0].get("digest")
                    return digest or ""
    except Exception as e:
        logging.debug(f"Updater: Error getting registry digest for {repo_name}: {e}")
    return ""

async def run_update_check():
    global UPDATE_STATUS
    UPDATE_STATUS["status"] = "checking"
    try:
        if not await ensure_git_installed():
            logging.error("Updater: Git is not installed and could not be installed.")
            UPDATE_STATUS["status"] = "error"
            return
            
        # 1. Fetch remote tags and branch main
        proc = await asyncio.create_subprocess_exec(
            "git", "-C", "/app", "fetch", "origin", "--tags",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE
        )
        await proc.communicate()
        
        proc = await asyncio.create_subprocess_exec(
            "git", "-C", "/app", "fetch", "origin", "main",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE
        )
        await proc.communicate()
        
        # 2. Get latest remote tag
        proc = await asyncio.create_subprocess_exec(
            "git", "-C", "/app", "tag", "-l", "--sort=-v:refname",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE
        )
        stdout, _ = await proc.communicate()
        tags = [t.strip() for t in stdout.decode().strip().split('\n') if t.strip()]
        latest_tag = tags[0] if tags else ""
        
        current_version = get_frenchio_version()
        
        if latest_tag:
            up_to_date = (current_version == latest_tag)
            diff_target = latest_tag
        else:
            # Fallback to main branch checks if no tags/releases exist yet
            proc = await asyncio.create_subprocess_exec(
                "git", "-C", "/app", "log", "HEAD..origin/main", "--oneline",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE
            )
            stdout, _ = await proc.communicate()
            has_remote_updates = bool(stdout.strip())
            up_to_date = not has_remote_updates
            diff_target = "origin/main"
            
        # 3. Check Frenchio files diff
        frenchio_files = {
            "main.py": "frenchio-stack/main.py",
            "utils.py": "frenchio-stack/utils.py",
            "services/qbittorrent.py": "frenchio-stack/services/qbittorrent.py",
            "services/torrserver.py": "frenchio-stack/services/torrserver.py",
            "templates/portal.html": "frenchio-stack/templates/portal.html",
            "templates/login.html": "frenchio-stack/templates/login.html",
            "templates/explorer.html": "frenchio-stack/templates/explorer.html"
        }
        
        modified = {}
        
        # Also check for local uncommitted modifications
        proc = await asyncio.create_subprocess_exec(
            "git", "-C", "/app", "status", "--porcelain",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE
        )
        status_out, _ = await proc.communicate()
        has_local_changes = bool(status_out.strip())
        
        for file_key, repo_path in frenchio_files.items():
            # Run diff against target (release tag or origin/main) to see what is different
            proc = await asyncio.create_subprocess_exec(
                "git", "-C", "/app", "diff", diff_target, "--", repo_path,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE
            )
            diff_stdout, _ = await proc.communicate()
            diff_text = diff_stdout.decode('utf-8', errors='ignore')
            if diff_text.strip():
                modified[file_key] = diff_text
                
                # Save patch file to config/patches/file_key.patch
                patch_dir = "/app/config/patches"
                os.makedirs(patch_dir, exist_ok=True)
                patch_filename = file_key.replace("/", "_") + ".patch"
                patch_path = os.path.join(patch_dir, patch_filename)
                with open(patch_path, "w", encoding="utf-8") as pf:
                    pf.write(diff_text)
                    
        UPDATE_STATUS["frenchio"] = {
            "up_to_date": up_to_date,
            "modified_files": modified,
            "version": current_version
        }
        
        # 3. Joal Update Check
        local_joal = await get_local_image_digest("anthonyraymond/joal:latest")
        reg_joal = await get_registry_image_digest("anthonyraymond/joal")
        UPDATE_STATUS["joal"] = {
            "up_to_date": (local_joal == reg_joal) if (local_joal and reg_joal) else True,
            "local_digest": local_joal,
            "registry_digest": reg_joal,
            "version": await get_container_version("frenchio-joal")
        }
        
        # 4. qBittorrent Update Check
        local_qbit = await get_local_image_digest("lscr.io/linuxserver/qbittorrent:latest")
        reg_qbit = await get_registry_image_digest("linuxserver/qbittorrent")
        UPDATE_STATUS["qbittorrent"] = {
            "up_to_date": (local_qbit == reg_qbit) if (local_qbit and reg_qbit) else True,
            "local_digest": local_qbit,
            "registry_digest": reg_qbit,
            "version": await get_container_version("frenchio-qbittorrent")
        }

        # 5. TorrServer Update Check
        local_ts = await get_local_image_digest("ghcr.io/yourok/torrserver:latest")
        reg_ts = await get_registry_image_digest("ghcr.io/yourok/torrserver")
        UPDATE_STATUS["torrserver"] = {
            "up_to_date": (local_ts == reg_ts) if (local_ts and reg_ts) else True,
            "local_digest": local_ts,
            "registry_digest": reg_ts,
            "version": await get_container_version("torrserver")
        }

        # 6. VPN (Gluetun) Update Check
        local_vpn = await get_local_image_digest("qmcgaw/gluetun:latest")
        reg_vpn = await get_registry_image_digest("qmcgaw/gluetun")
        UPDATE_STATUS["vpn"] = {
            "up_to_date": (local_vpn == reg_vpn) if (local_vpn and reg_vpn) else True,
            "local_digest": local_vpn,
            "registry_digest": reg_vpn,
            "version": await get_container_version("frenchio-vpn")
        }
        
        UPDATE_STATUS["last_check"] = time.strftime("%Y-%m-%d %H:%M:%S")
        UPDATE_STATUS["status"] = "idle"
        save_update_status()
        logging.info(f"✅ Update check completed. Frenchio up-to-date: {up_to_date}, Joal up-to-date: {UPDATE_STATUS['joal']['up_to_date']}, qBittorrent up-to-date: {UPDATE_STATUS['qbittorrent']['up_to_date']}, TorrServer up-to-date: {UPDATE_STATUS['torrserver']['up_to_date']}, VPN up-to-date: {UPDATE_STATUS['vpn']['up_to_date']}")
    except Exception as e:
        UPDATE_STATUS["status"] = "error"
        save_update_status()
        logging.error(f"Error checking updates: {e}")

async def check_updates_loop(app):
    await asyncio.sleep(10)
    while True:
        await run_update_check()
        await asyncio.sleep(24 * 3600)

async def ensure_git_installed():
    try:
        proc = await asyncio.create_subprocess_exec("git", "--version", stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
        await proc.communicate()
        if proc.returncode == 0:
            return True
    except Exception:
        pass
    
    try:
        logging.info("Installing git inside container...")
        proc = await asyncio.create_subprocess_shell(
            "apt-get update && apt-get install -y git",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE
        )
        await proc.communicate()
        return proc.returncode == 0
    except Exception as e:
        logging.error(f"Failed to install git: {e}")
    return False

async def run_git_backup():
    try:
        if not await ensure_git_installed():
            logging.error("Updater: Git is not installed and could not be installed.")
            return False
            
        logging.info("Updater: Running git backup before update...")
        # Check git status to see if there are any changes
        proc = await asyncio.create_subprocess_exec(
            "git", "-C", "/app", "status", "--porcelain",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE
        )
        stdout, _ = await proc.communicate()
        if not stdout.strip():
            logging.info("Updater: No local changes to backup.")
            return True
            
        # Run git add .
        proc = await asyncio.create_subprocess_exec(
            "git", "-C", "/app", "add", ".",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE
        )
        await proc.communicate()
        if proc.returncode != 0:
            logging.error("Updater: git add failed")
            return False
            
        # Configure user if not set
        await asyncio.create_subprocess_shell("git config --global user.email 'updater@frenchio.local' && git config --global user.name 'Frenchio Updater'")
            
        # Run git commit
        commit_msg = f"Auto backup before update - {time.strftime('%Y-%m-%d %H:%M:%S')}"
        proc = await asyncio.create_subprocess_exec(
            "git", "-C", "/app", "commit", "-m", commit_msg,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE
        )
        await proc.communicate()
        
        # Run git push (HEAD:main is safe even if in a detached HEAD tag state)
        proc = await asyncio.create_subprocess_exec(
            "git", "-C", "/app", "push", "origin", "HEAD:main",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE
        )
        stdout, stderr = await proc.communicate()
        if proc.returncode != 0:
            logging.error(f"Updater: git push failed: {stderr.decode()}")
            return False
            
        logging.info("Updater: git backup push succeeded.")
        return True
    except Exception as e:
        logging.error(f"Updater: Error running git backup: {e}")
    return False

async def apply_frenchio_updates():
    # 1. Run Git Backup first!
    if not await run_git_backup():
        return False, "Git backup push failed. Update cancelled to prevent losing modifications."
        
    # 2. Fetch tags and checkout the latest tag (or pull if no tags exist)
    try:
        proc = await asyncio.create_subprocess_exec(
            "git", "-C", "/app", "fetch", "origin", "--tags",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE
        )
        await proc.communicate()
        
        proc = await asyncio.create_subprocess_exec(
            "git", "-C", "/app", "tag", "-l", "--sort=-v:refname",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE
        )
        stdout, _ = await proc.communicate()
        tags = [t.strip() for t in stdout.decode().strip().split('\n') if t.strip()]
        latest_tag = tags[0] if tags else ""
        
        if latest_tag:
            proc = await asyncio.create_subprocess_exec(
                "git", "-C", "/app", "checkout", latest_tag,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE
            )
            stdout, stderr = await proc.communicate()
            if proc.returncode != 0:
                err_msg = stderr.decode()
                logging.error(f"Updater: checkout tag failed: {err_msg}")
                return False, f"Checkout tag {latest_tag} failed: {err_msg}."
            logging.info(f"Updater: checked out release tag {latest_tag}.")
        else:
            proc = await asyncio.create_subprocess_exec(
                "git", "-C", "/app", "pull", "origin", "main",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE
            )
            stdout, stderr = await proc.communicate()
            if proc.returncode != 0:
                err_msg = stderr.decode()
                logging.error(f"Updater: git pull failed: {err_msg}")
                return False, f"Git pull failed: {err_msg}."
            logging.info("Updater: git pull main succeeded.")
    except Exception as e:
        return False, f"Error running update: {e}"
                
    # 3. Restart frenchio-addon container in background
    async def restart_addon():
        await asyncio.sleep(2)
        connector = aiohttp.UnixConnector(path='/var/run/docker.sock')
        async with aiohttp.ClientSession(connector=connector) as session:
            logging.info("Restarting frenchio-addon container...")
            await session.post("http://localhost/containers/frenchio-addon/restart")
            
    asyncio.create_task(restart_addon())
    return True, "Frenchio files updated and merged successfully. Addon container is restarting..."

async def apply_container_updates(container_name, image_name):
    try:
        connector = aiohttp.UnixConnector(path='/var/run/docker.sock')
        async with aiohttp.ClientSession(connector=connector) as session:
            # 1. Pull the latest image
            logging.info(f"Pulling latest image for {image_name}...")
            url = f"http://localhost/images/create?fromImage={image_name}"
            async with session.post(url) as resp:
                if resp.status != 200:
                    return False, f"Failed to pull image {image_name}: HTTP {resp.status}"
                await resp.read() # Consume response
                
            # 2. Restart the container to apply the new image
            logging.info(f"Restarting container {container_name}...")
            url = f"http://localhost/containers/{container_name}/restart"
            async with session.post(url) as resp:
                if resp.status not in (200, 204):
                    return False, f"Failed to restart container {container_name}"
                    
            return True, f"Container {container_name} updated successfully to the latest image."
    except Exception as e:
        return False, str(e)

async def handle_post_apply_updates(request):
    try:
        body = await request.json()
        component = body.get("component")
        if not component:
            return web.Response(status=400, text="Missing component parameter")
            
        if component == "frenchio":
            success, msg = await apply_frenchio_updates()
        elif component == "joal":
            success, msg = await apply_container_updates("frenchio-joal", "anthonyraymond/joal:latest")
        elif component == "qbittorrent":
            success, msg = await apply_container_updates("frenchio-qbittorrent", "lscr.io/linuxserver/qbittorrent:latest")
        elif component == "torrserver":
            success, msg = await apply_container_updates("torrserver", "ghcr.io/yourok/torrserver:latest")
        elif component == "vpn":
            success, msg = await apply_container_updates("frenchio-vpn", "qmcgaw/gluetun:latest")
            # After gluetun update, restart network-dependent containers
            if success:
                async def restart_vpn_deps():
                    await asyncio.sleep(6)  # Let gluetun fully reconnect first
                    _cfg = {}
                    _cp = "/app/config/config.json"
                    if os.path.exists(_cp):
                        try:
                            with open(_cp, "r") as _f:
                                _cfg = json.load(_f)
                        except Exception:
                            pass
                    _svc = _cfg.get("services", {})
                    _deps = []
                    if _svc.get("qbittorrent", True): _deps.append("frenchio-qbittorrent")
                    if _svc.get("torrserver",  True): _deps.append("torrserver")
                    if _svc.get("joal",        True): _deps.append("frenchio-joal")
                    _conn = aiohttp.UnixConnector(path='/var/run/docker.sock')
                    async with aiohttp.ClientSession(connector=_conn) as _sess:
                        for _c in _deps:
                            try:
                                await _sess.post(f"http://localhost/containers/{_c}/restart")
                                logging.info(f"VPN update: restarted {_c}")
                            except Exception as _e:
                                logging.warning(f"VPN update: could not restart {_c}: {_e}")
                asyncio.create_task(restart_vpn_deps())
                msg += " Les services réseau dépendants (qBittorrent/TorrServer/JOAL actifs) redémarrent automatiquement après reconnexion VPN."
        else:
            return web.Response(status=400, text=f"Unknown component: {component}")
            
        return web.json_response({"success": success, "message": msg})
    except Exception as e:
        return web.Response(status=500, text=str(e))

async def handle_get_updates_status(request):
    return web.json_response(UPDATE_STATUS)

async def handle_post_check_updates(request):
    asyncio.create_task(run_update_check())
    return web.json_response({"success": True, "message": "Check updates initiated"})

def get_oldest_download(downloads_dir):
    try:
        entries = []
        for name in os.listdir(downloads_dir):
            if name == 'incomplete':
                continue
            path = os.path.join(downloads_dir, name)
            try:
                if os.path.isdir(path):
                    mtimes = []
                    for root, _, files in os.walk(path):
                        for f in files:
                            try:
                                mtimes.append(os.path.getmtime(os.path.join(root, f)))
                            except Exception:
                                pass
                    if mtimes:
                        mtime = min(mtimes)
                    else:
                        mtime = os.path.getmtime(path)
                else:
                    mtime = os.path.getmtime(path)
                entries.append((path, mtime))
            except Exception:
                pass
        
        if entries:
            entries.sort(key=lambda x: x[1])
            return entries[0][0]
    except Exception as e:
        logging.error(f"Error finding oldest download: {e}")
    return None

async def check_disk_space_loop(app):
    downloads_dir = "/downloads"
    while True:
        try:
            if os.path.exists(downloads_dir):
                import shutil
                total, used, free = shutil.disk_usage(downloads_dir)
                percent = (used / total) * 100 if total > 0 else 0
                if percent >= 95.0:
                    logging.warning(f"⚠️ Disk space usage is at {percent:.1f}%, which is >= 95%. Triggering automatic cleanup...")
                    deleted_any = False
                    while percent >= 95.0:
                        oldest = get_oldest_download(downloads_dir)
                        if oldest:
                            logging.info(f"♻️ Auto-cleanup: Disk usage {percent:.1f}% >= 95%. Deleting oldest download: {oldest}")
                            loop = asyncio.get_running_loop()
                            def delete_action():
                                if os.path.isdir(oldest):
                                    shutil.rmtree(oldest)
                                elif os.path.exists(oldest):
                                    os.remove(oldest)
                                for root, dirs, files in os.walk(downloads_dir, topdown=False):
                                    if 'incomplete' in root.split(os.sep) or root == downloads_dir:
                                        continue
                                    try:
                                        if not os.listdir(root):
                                            os.rmdir(root)
                                    except Exception:
                                        pass
                            await loop.run_in_executor(None, delete_action)
                            deleted_any = True
                            total, used, free = shutil.disk_usage(downloads_dir)
                            percent = (used / total) * 100 if total > 0 else 0
                        else:
                            logging.info("♻️ Auto-cleanup: No more files to delete under /downloads.")
                            break
                    if deleted_any:
                        logging.info(f"✅ Auto-cleanup completed. Current disk usage is {percent:.1f}%")
        except Exception as e:
            logging.error(f"Error in check_disk_space_loop: {e}")
        await asyncio.sleep(60)

async def auto_cleanup_ctx(app):
    task = asyncio.create_task(check_disk_space_loop(app))
    yield
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass

async def auto_update_ctx(app):
    task = asyncio.create_task(check_updates_loop(app))
    yield
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass

async def get_app():
    app = web.Application(middlewares=[cors_middleware])
    app.cleanup_ctx.append(auto_cleanup_ctx)
    app.cleanup_ctx.append(auto_update_ctx)
    
    # API endpoints
    app.router.add_get('/api/downloads', handle_get_downloads)
    app.router.add_delete('/api/downloads', handle_delete_download)
    app.router.add_post('/api/stack/restart', handle_stack_restart)
    app.router.add_get('/api/stack/status', handle_stack_status)
    app.router.add_get('/api/updates/status', handle_get_updates_status)
    app.router.add_post('/api/updates/check', handle_post_check_updates)
    app.router.add_post('/api/updates/apply', handle_post_apply_updates)
    app.router.add_post('/api/config', handle_post_config)
    app.router.add_get('/api/config/download', handle_download_config)
    app.router.add_post('/api/config/upload', handle_upload_config)
    app.router.add_get('/api/vpn/status', handle_vpn_status)
    app.router.add_get('/api/network/status', handle_network_status)
    app.router.add_get('/api/p2p/links', handle_get_p2p_links)
    app.router.add_post('/api/p2p/links', handle_post_p2p_links)
    app.router.add_get('/api/addon/instances', handle_get_addon_instances)
    app.router.add_post('/api/addon/instances/create', handle_create_addon_instance)
    app.router.add_post('/api/addon/instances/delete', handle_delete_addon_instance)
    app.router.add_get('/api/services/config', handle_get_services_config)
    app.router.add_post('/api/services/toggle', handle_post_services_toggle)
    app.router.add_get('/api/logs/{service_id}', handle_get_logs)
    
    app.router.add_get('/', handle_portal)
    app.router.add_get('/login', handle_login)
    app.router.add_get('/configure', handle_configure)
    app.router.add_get('/p2p-configure', handle_p2p)
    app.router.add_get('/vpn', handle_vpn_page)
    app.router.add_get('/network', handle_network_page)
    
    manifest_token = os.getenv('MANIFEST_TOKEN', '')
    if manifest_token:
        app.router.add_get(f'/manifest/{manifest_token}/manifest.json', handle_manifest_no_config)
        app.router.add_get(f'/manifest/{manifest_token}/stream/{{type}}/{{id}}.json', handle_stream_no_config)
    else:
        app.router.add_get('/manifest.json', handle_manifest_no_config)
        app.router.add_get('/stream/{type}/{id}.json', handle_stream_no_config)
        
    # Plugin P2P Nuvio — Fichiers statiques avec token
    app.router.add_get('/plugin/{token}/manifest.json', handle_plugin_static, name='plugin_manifest')
    app.router.add_get('/plugin/{token}/proxy', handle_plugin_proxy, name='plugin_proxy')
    app.router.add_get('/plugin/{token}/{file_path:.*}', handle_plugin_static, name='plugin_static')
 
    app.router.add_get('/{config}/', handle_portal) # Nouvelle route pour le portal
    app.router.add_get('/explorer', handle_explorer)
    app.router.add_get('/{config}/explorer', handle_explorer)
    app.router.add_get('/updates', handle_updates)
    app.router.add_get('/{config}/updates', handle_updates)
    app.router.add_get('/{config}/configure', handle_configure) # Nouvelle route pour config pré-remplie
    app.router.add_get('/{config}/p2p-configure', handle_p2p)
    app.router.add_get('/{config}/vpn', handle_vpn_page)
    app.router.add_get('/{config}/network', handle_network_page)
    app.router.add_get('/{config}/manifest.json', handle_manifest)
    app.router.add_get('/{config}/stream/{type}/{id}.json', handle_stream)
    
    # Service views routes
    app.router.add_get('/service-view/{service_id}', handle_service_iframe)
    app.router.add_get('/{config}/service-view/{service_id}', handle_service_iframe)
    
    # Routes de résolution (avec config)
    app.router.add_get('/{config}/resolve/{service}/{hash}', handle_resolve)
    app.router.add_get('/{config}/stream/qbit/{hash}/{file_path:.*}', handle_qbit_stream)
    app.router.add_get('/{config}/stream/torrserver/{hash}/{index}', handle_torrserver_stream)
    
    # Anciennes routes (compatibilité)
    app.router.add_get('/resolve/{service}/{api_key}/{hash}', handle_resolve)
    app.router.add_get('/resolve/{api_key}/{hash}', handle_resolve)
    
    return app

if __name__ == '__main__':
    web.run_app(
        get_app(),
        host='0.0.0.0',
        port=7777
    )
