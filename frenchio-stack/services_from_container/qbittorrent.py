import qbittorrentapi
import logging
import time
import urllib.parse
import re
import os

def parse_season_episode_from_path(filepath):
    """
    Parses season and episode from a directory path and filename.
    Returns (season, episode) as integers or (None, None).
    """
    filepath = filepath.replace('\\', '/')
    parts = [p.lower() for p in filepath.split('/') if p]
    if not parts:
        return None, None
        
    filename = parts[-1]
    directories = parts[:-1]
    
    # 1. Look for SxxExx anywhere (e.g. show_s01e02, show.s01e02, show s01e02, s01e02)
    m = re.search(r'(?:^|[^a-z0-9])s(\d{1,2})[ ._-]*e(\d{1,3})(?:$|[^a-z0-9])', filename)
    if m:
        return int(m.group(1)), int(m.group(2))
        
    # 2. Look for XxY (e.g. 1x02, 01x02)
    m = re.search(r'(?:^|[^a-z0-9])(\d{1,2})x(\d{1,3})(?:$|[^a-z0-9])', filename)
    if m:
        return int(m.group(1)), int(m.group(2))
        
    # 3. Look for explicit season in filename first, e.g. "s01", "season 1"
    season = None
    s_m = re.search(r'(?:^|[^a-z0-9])(?:season|saison|s)[ ._-]*(\d{1,2})(?:$|[^a-z0-9])', filename)
    if s_m:
        season = int(s_m.group(1))
        
    # 4. If no season in filename, check directories
    if season is None:
        for d in reversed(directories):
            s_m = re.search(r'(?:^|[^a-z0-9])(?:season|saison|s)[ ._-]*(\d{1,3})(?:$|[^a-z0-9])', d)
            if s_m:
                season = int(s_m.group(1))
                break
                
    # 5. Look for episode in filename
    episode = None
    # Check explicit Ep/E tags in filename
    ep_m = re.search(r'(?:^|[^a-z0-9])(?:episodes?|ep|e)[ ._-]*(\d{1,3})(?:$|[^a-z0-9])', filename)
    if ep_m:
        episode = int(ep_m.group(1))
        
    # 6. If no explicit episode tag, let's extract all numbers from filename
    if episode is None:
        name_no_ext = filename.rsplit('.', 1)[0] if '.' in filename else filename
        # Find all sequences of digits
        numbers = re.findall(r'\b\d+\b', re.sub(r'[^a-z0-9]', ' ', name_no_ext))
        valid_numbers = []
        for num in numbers:
            val = int(num)
            if val in [720, 1080, 2160]:
                continue
            if 1900 <= val <= 2100:
                continue
            valid_numbers.append((num, val))
            
        # If we have a 3 or 4 digit number, check if it can represent season + episode
        # e.g., 102 -> S1E2, 1205 -> S12E5
        for num_str, val in valid_numbers:
            if len(num_str) in [3, 4]:
                if len(num_str) == 3:
                    s_part = int(num_str[0])
                    e_part = int(num_str[1:])
                else: # 4
                    s_part = int(num_str[:2])
                    e_part = int(num_str[2:])
                
                # If we already have a season from filename/dir, verify it matches
                if season is not None and s_part != season:
                    continue
                return s_part, e_part
                
        # If it's a standalone number
        real_vals = [val for _, val in valid_numbers]
        if len(real_vals) == 1:
            episode = real_vals[0]
        elif len(real_vals) > 1 and season is not None:
            # If season is known, filter it out
            other_nums = [v for v in real_vals if v != season]
            if len(other_nums) == 1:
                episode = other_nums[0]
                
    return season, episode


class QBittorrentService:
    def __init__(self, host, username, password, public_url_base):
        """
        Initialise le client qBittorrent avec la librairie officielle qbittorrent-api
        Docs: https://pypi.org/project/qbittorrent-api/
        """
        # Parser l'URL pour extraire host et port
        parsed = urllib.parse.urlparse(host if host.startswith('http') else f'http://{host}')
        
        self.host = host.rstrip('/')
        self.public_url_base = public_url_base.rstrip('/')
        
        # Créer le client qBittorrent
        try:
            self.client = qbittorrentapi.Client(
                host=parsed.hostname or 'localhost',
                port=parsed.port or 8080,
                username=username,
                password=password,
                REQUESTS_ARGS={'timeout': 30}
            )
            logging.info(f"qBittorrent client created for {parsed.hostname}:{parsed.port}")
        except Exception as e:
            logging.error(f"Failed to create qBittorrent client: {e}")
            self.client = None

    def test_connection(self):
        """Test la connexion à qBittorrent"""
        if not self.client:
            return False
            
        try:
            # La librairie gère automatiquement le login lors du premier appel
            version = self.client.app.version
            api_version = self.client.app.web_api_version
            logging.info(f"✅ qBittorrent connected: v{version} (API v{api_version})")
            return True
        except qbittorrentapi.LoginFailed as e:
            logging.error(f"❌ qBittorrent Login Failed: {e}")
            return False
        except qbittorrentapi.Forbidden403Error as e:
            logging.error(f"❌ qBittorrent 403 Forbidden: {e}")
            logging.error("   → Vérifiez que le WebUI est activé et accessible")
            logging.error("   → Vérifiez les identifiants (username/password)")
            return False
        except Exception as e:
            logging.error(f"❌ qBittorrent Connection Error: {e}")
            return False

    def add_torrent(self, torrent_data, is_file=False, tracker=None, is_paused=False):
        """
        Ajoute un torrent à qBittorrent
        
        Args:
            torrent_data: Contenu binaire du .torrent ou URL magnet
            is_file: True si torrent_data est un fichier binaire
            tracker: Le nom du tracker d'origine (tag optionnel)
            is_paused: True si on veut ajouter le torrent en pause
        """
        if not self.client:
            logging.error("qBittorrent client not initialized")
            return None
            
        try:
            # Options de streaming (l'API les supporte bien)
            streaming_opts = {
                'is_paused': is_paused,
                'is_sequential_download': True,
                'is_first_last_piece_priority': True
            }
            if tracker:
                streaming_opts['tags'] = tracker
            
            if is_file:
                # Ajouter depuis un fichier .torrent
                logging.info(f"Adding .torrent file ({len(torrent_data)} bytes) with streaming options")
                result = self.client.torrents_add(
                    torrent_files=torrent_data,
                    **streaming_opts
                )
            else:
                # Ajouter depuis un magnet/URL
                logging.info("Adding magnet/URL with streaming options")
                result = self.client.torrents_add(
                    urls=torrent_data,
                    **streaming_opts
                )
            
            # La librairie retourne "Ok." en cas de succès
            if result == "Ok.":
                logging.info("✅ Torrent added successfully")
                return True
            else:
                logging.warning(f"Unexpected response from qBittorrent: {result}")
                return True  # On considère quand même que ça a fonctionné
                
        except qbittorrentapi.Conflict409Error:
            # Le torrent existe déjà, ce n'est pas une erreur critique
            logging.info("ℹ️ Torrent already exists in qBittorrent")
            return True
        except Exception as e:
            logging.error(f"❌ Failed to add torrent: {e}")
            import traceback
            logging.error(traceback.format_exc())
            return None

    def configure_sequential(self, info_hash):
        """
        Force l'activation du téléchargement séquentiel et la priorité début/fin
        
        Args:
            info_hash: Hash du torrent
        """
        if not self.client:
            logging.error("Client not initialized")
            return False
            
        try:
            h = info_hash.lower()
            
            logging.info(f"🔧 Forcing streaming options for torrent {h[:8]}...")
            
            # Récupérer l'état actuel via torrents_info
            torrents = self.client.torrents_info(torrent_hashes=h)
            if torrents:
                torrent = torrents[0]
                seq_enabled = torrent.get('seq_dl', False)
                first_last_enabled = torrent.get('f_l_piece_prio', False)
            else:
                logging.warning(f"   ⚠️ Torrent {h[:8]} not found in torrents_info, falling back to properties")
                props = self.client.torrents_properties(torrent_hash=h)
                seq_enabled = props.get('seq_dl', False) or props.get('is_sequential_download', False) or props.get('sequential_download', False)
                first_last_enabled = props.get('f_l_piece_prio', False) or props.get('is_first_last_piece_priority', False) or props.get('first_last_piece_priority', False)
            
            logging.info(f"   Current state: sequential={seq_enabled}, first_last={first_last_enabled}")
            
            # Activer le téléchargement séquentiel (toggle si pas activé)
            if not seq_enabled:
                try:
                    self.client.torrents_toggle_sequential_download(torrent_hashes=h)
                    logging.info(f"   ✅ Sequential download: OFF → ON")
                except Exception as e:
                    logging.error(f"   ❌ Failed to toggle sequential download: {e}")
                    raise
            else:
                logging.info(f"   ℹ️ Sequential download already ON")
            
            # Activer la priorité des premières et dernières pièces (toggle si pas activé)
            if not first_last_enabled:
                try:
                    self.client.torrents_toggle_first_last_piece_priority(torrent_hashes=h)
                    logging.info(f"   ✅ First/Last piece priority: OFF → ON")
                except Exception as e:
                    logging.error(f"   ❌ Failed to toggle first/last priority: {e}")
                    raise
            else:
                logging.info(f"   ℹ️ First/Last piece priority already ON")
            
            logging.info(f"✅ All streaming options configured for {h[:8]}...")
            return True
            
        except Exception as e:
            logging.error(f"❌ Failed to configure streaming options: {e}")
            import traceback
            logging.error(traceback.format_exc())
            return False

    def get_torrent_files(self, info_hash, max_retries=15, season=None, episode=None, fast_mode=False):
        """
        Récupère les fichiers d'un torrent, sélectionne le bon, et désactive les autres
        
        Args:
            info_hash: Hash du torrent
            max_retries: Nombre max de tentatives pour attendre les métadonnées
            season: Numéro de saison (pour séries)
            episode: Numéro d'épisode (pour séries)
            fast_mode: Si True, réduit les délais pour un streaming instantané
            
        Returns:
            Nom du fichier sélectionné ou None
        """
        if not self.client:
            return None
            
        h = info_hash.lower()
        
        # En mode rapide, plus de retries et délais beaucoup plus courts (100ms)
        if fast_mode:
            max_retries = 20
            retry_delay = 0.1  # 100ms entre chaque tentative pour être super réactif
        else:
            retry_delay = 1.0  # 1s entre chaque tentative
        
        # Attendre que les métadonnées soient disponibles
        logging.info(f"🔍 Looking for files in torrent (fast_mode={fast_mode})...")
        
        for retry in range(max_retries):
            try:
                files = self.client.torrents_files(torrent_hash=h)
                
                if files:
                    logging.info(f"✅ Found {len(files)} files in torrent")
                    
                    # Sélection du fichier
                    target_file = None
                    
                    if season is not None and episode is not None:
                        # Chercher le fichier correspondant à l'épisode
                        scored_files = []
                        video_exts = ['.mkv', '.mp4', '.avi', '.mov', '.wmv', '.m4v']
                        video_files = []
                        
                        for f in files:
                            if not any(f.name.lower().endswith(ext) for ext in video_exts):
                                continue
                            video_files.append(f)
                            
                            fs, fe = parse_season_episode_from_path(f.name)
                            score = 0
                            
                            if fs == season and fe == episode:
                                score = 100
                            elif fs is None and fe == episode:
                                score = 50
                            
                            if score > 0:
                                scored_files.append((score, f))
                                
                        if scored_files:
                            # Sort by score descending, then by file size descending (to avoid samples)
                            scored_files.sort(key=lambda x: (x[0], x[1].size), reverse=True)
                            target_file = scored_files[0][1].name
                            logging.info(f"✅ Selected matched episode file: {target_file} (score={scored_files[0][0]})")
                        else:
                            # Si aucun fichier ne correspond à l'épisode, et qu'il y a un seul fichier vidéo dans le torrent,
                            # on vérifie s'il n'a pas un numéro d'épisode différent (mismatch).
                            if len(video_files) == 1:
                                f = video_files[0]
                                fs, fe = parse_season_episode_from_path(f.name)
                                # Si un épisode est extrait et qu'il ne correspond pas -> rejet
                                if fe is not None and fe != episode:
                                    logging.warning(f"❌ Single video file {f.name} has mismatched episode {fe} (expected {episode}). Rejecting.")
                                    target_file = None
                                # Si une saison est extraite et qu'elle ne correspond pas -> rejet
                                elif fs is not None and fs != season:
                                    logging.warning(f"❌ Single video file {f.name} has mismatched season {fs} (expected {season}). Rejecting.")
                                    target_file = None
                                else:
                                    target_file = f.name
                                    logging.info(f"✅ Selected single video file (no mismatch): {target_file}")
                            else:
                                logging.warning(f"❌ No matching file found for S{season}E{episode} among {len(video_files)} video files. Rejecting.")
                                target_file = None
                    else:
                        # Mode film / sans saison/épisode : le plus gros fichier vidéo
                        video_exts = ['.mkv', '.mp4', '.avi', '.mov', '.wmv', '.m4v']
                        video_files = [f for f in files if any(f.name.lower().endswith(ext) for ext in video_exts)]
                        
                        if video_files:
                            largest = max(video_files, key=lambda x: x.size)
                            target_file = largest.name
                            logging.info(f"✅ Selected largest video file (fallback): {target_file} ({largest.size} bytes)")
                        else:
                            # Prendre le plus gros fichier tout court
                            largest = max(files, key=lambda x: x.size)
                            target_file = largest.name
                            logging.info(f"✅ Selected largest file (fallback): {target_file} ({largest.size} bytes)")
                    
                    # Désactiver les fichiers non ciblés pour ne télécharger QUE l'épisode demandé
                    if target_file:
                        try:
                            target_file_id = None
                            other_file_ids = []
                            for f in files:
                                if f.name == target_file:
                                    target_file_id = f.id
                                else:
                                    if f.priority != 0:
                                        other_file_ids.append(f.id)
                            
                            # Définir les priorités
                            if target_file_id is not None:
                                self.client.torrents_file_priority(
                                    torrent_hash=h,
                                    file_ids=[target_file_id],
                                    priority=1
                                )
                            if other_file_ids:
                                self.client.torrents_file_priority(
                                    torrent_hash=h,
                                    file_ids=other_file_ids,
                                    priority=0
                                )
                                logging.info(f"📥 Ignored {len(other_file_ids)} non-target files to only download: {target_file}")
                        except Exception as ex:
                            logging.error(f"Failed to set file priorities: {ex}")
                    
                    return target_file
                    
            except Exception as e:
                if retry < max_retries - 1:
                    logging.debug(f"⏳ Waiting for metadata... ({retry + 1}/{max_retries})")
                    time.sleep(retry_delay)
                else:
                    logging.error(f"Failed to get torrent files: {e}")
            
            # Pas d'exception mais pas de fichiers non plus
            if retry < max_retries - 1:
                logging.debug(f"⏳ No files yet, retrying... ({retry + 1}/{max_retries})")
                time.sleep(retry_delay)
                    
        logging.error(f"❌ Could not find files after {max_retries} retries")
        return None

    def verify_and_fix_streaming_options(self, info_hash):
        """
        Vérifie que les options de streaming sont bien activées, sinon les force à nouveau
        """
        if not self.client:
            return False
        
        try:
            h = info_hash.lower()
            
            logging.info(f"🔍 Verifying streaming options for torrent {h[:8]}...")
            
            # Récupérer l'état actuel via torrents_info
            torrents = self.client.torrents_info(torrent_hashes=h)
            if torrents:
                torrent = torrents[0]
                seq_enabled = torrent.get('seq_dl', False)
                first_last_enabled = torrent.get('f_l_piece_prio', False)
            else:
                logging.warning(f"   ⚠️ Torrent {h[:8]} not found in torrents_info, falling back to properties")
                props = self.client.torrents_properties(torrent_hash=h)
                seq_enabled = props.get('seq_dl', False) or props.get('is_sequential_download', False) or props.get('sequential_download', False)
                first_last_enabled = props.get('f_l_piece_prio', False) or props.get('is_first_last_piece_priority', False) or props.get('first_last_piece_priority', False)
            
            logging.info(f"📊 Current status (from qBittorrent):")
            logging.info(f"   props.seq_dl = {seq_enabled} {'✅ ON' if seq_enabled else '❌ OFF'}")
            logging.info(f"   props.f_l_piece_prio = {first_last_enabled} {'✅ ON' if first_last_enabled else '❌ OFF'}")
            
            # Si l'une des options n'est pas activée, on les force à nouveau
            if not seq_enabled or not first_last_enabled:
                logging.warning("⚠️ Streaming options NOT applied correctly, forcing again...")
                self.configure_sequential(info_hash)
                
                # Vérifier à nouveau
                time.sleep(0.5)
                torrents2 = self.client.torrents_info(torrent_hashes=h)
                if torrents2:
                    seq2 = torrents2[0].get('seq_dl', False)
                    first_last2 = torrents2[0].get('f_l_piece_prio', False)
                else:
                    props2 = self.client.torrents_properties(torrent_hash=h)
                    seq2 = props2.get('seq_dl', False) or props2.get('is_sequential_download', False)
                    first_last2 = props2.get('f_l_piece_prio', False) or props2.get('is_first_last_piece_priority', False)
                logging.info(f"📊 After second attempt:")
                logging.info(f"   sequential = {seq2}")
                logging.info(f"   first_last = {first_last2}")
                
                return True
            else:
                logging.info("✅ Streaming options verified: ALL ON")
            
            return True
            
        except Exception as e:
            logging.error(f"❌ Failed to verify streaming options: {e}")
            import traceback
            logging.error(traceback.format_exc())
            return False

    def manage_stream(self, torrent_data, info_hash, is_file=False, season=None, episode=None, tracker=None):
        """
        Orchestre l'ajout du torrent et retourne l'URL de streaming IMMÉDIATEMENT
        Le téléchargement se fait en arrière-plan, le player lit au fur et à mesure
        
        Args:
            torrent_data: Contenu binaire du .torrent ou URL magnet
            info_hash: Hash du torrent
            is_file: True si torrent_data est un fichier binaire
            season: Numéro de saison (pour séries)
            episode: Numéro d'épisode (pour séries)
            tracker: Le nom du tracker d'origine (tag optionnel)
            
        Returns:
            URL HTTP du fichier vidéo pour streaming (même si téléchargement en cours)
        """
        # 1. Ajouter le torrent avec les options de streaming
        # Si c'est un fichier local .torrent, on l'ajoute en pause pour configurer les fichiers sans télécharger le reste
        is_paused = is_file
        if not self.add_torrent(torrent_data, is_file, tracker=tracker, is_paused=is_paused):
            return None
            
        # S'assurer que le tag tracker est bien appliqué (même si le torrent existait déjà)
        if tracker:
            try:
                self.client.torrents_add_tags(tags=tracker, torrent_hashes=info_hash.lower())
                logging.info(f"✅ Tracker tag '{tracker}' ensured for torrent {info_hash[:8]}")
            except Exception as tag_err:
                logging.warning(f"⚠️ Could not add tracker tag: {tag_err}")
        
        logging.info("⚡ Torrent added, preparing instant stream...")
        
        # 2. Attente active très rapide que qBittorrent initialise le torrent (max 1.5s, toutes les 100ms)
        torrent_added = False
        for _ in range(15):
            try:
                torrents = self.client.torrents_info(torrent_hashes=info_hash)
                if torrents:
                    torrent_added = True
                    break
            except Exception:
                pass
            time.sleep(0.1)
        
        if not torrent_added:
            logging.warning("⚠️ Torrent not found in torrents_info after 1.5s, proceeding anyway...")
        
        # 3. FORCER les options de streaming en parallèle de l'obtention des fichiers
        # (ne pas attendre, c'est juste pour être sûr)
        logging.info("🔧 Forcing streaming options (non-blocking)...")
        self.configure_sequential(info_hash)
        
        # 4. Récupérer le fichier cible (avec retry rapide)
        target_file = self.get_torrent_files(info_hash, season=season, episode=episode, fast_mode=True)
        
        if not target_file:
            logging.error("❌ Could not identify target file")
            return None
            
        # 5. Si le torrent a été ajouté en pause, on le relance après avoir configuré les priorités
        if is_file:
            try:
                self.client.torrents_resume(torrent_hashes=info_hash)
                logging.info("✅ Resumed torrent after setting file priorities")
            except Exception as resume_err:
                logging.error(f"Failed to resume torrent: {resume_err}")
        
        # 6. Construire l'URL de streaming et la retourner IMMÉDIATEMENT
        # Le téléchargement continue en background, le player va lire au fur et à mesure
        safe_path = urllib.parse.quote(target_file)
        stream_url = f"{self.public_url_base}/{safe_path}"
        
        logging.info(f"🎬 INSTANT STREAM ready: {stream_url}")
        logging.info(f"   ⚡ Player will read file as it downloads (sequential mode)")
        logging.info(f"   📥 qBittorrent is downloading in background...")
        
        return stream_url
