import aiohttp
import logging
import asyncio
from services.qbittorrent import parse_season_episode_from_path

class TorrServerService:
    def __init__(self, host, username=None, password=None):
        self.host = host.rstrip('/')
        self.username = username
        self.password = password
        self.auth = aiohttp.BasicAuth(username, password) if username and password else None

    async def test_connection(self):
        """Test connection to TorrServer"""
        url = f"{self.host}/echo"
        try:
            async with aiohttp.ClientSession(trust_env=True) as session:
                async with session.get(url, auth=self.auth, timeout=10) as resp:
                    if resp.status == 200:
                        text = await resp.text()
                        logging.info(f"✅ TorrServer connected: {text}")
                        return True
                    else:
                        logging.error(f"❌ TorrServer returned status: {resp.status}")
                        return False
        except Exception as e:
            logging.error(f"❌ TorrServer connection error: {e}")
            return False

    async def add_torrent(self, torrent_data, is_file=False, title=None):
        """
        Adds a torrent to TorrServer
        Args:
            torrent_data: magnet link/URL or binary torrent file data
            is_file: True if torrent_data is binary torrent data
            title: Optional display title
        """
        async with aiohttp.ClientSession(trust_env=True) as session:
            if is_file:
                # Add via multipart/form-data upload
                url = f"{self.host}/torrent/upload"
                data = aiohttp.FormData()
                data.add_field('file', torrent_data, filename='file.torrent')
                if title:
                    data.add_field('title', title)
                data.add_field('save', 'true')
                try:
                    async with session.post(url, data=data, auth=self.auth, timeout=30) as resp:
                        if resp.status in (200, 201):
                            res_json = await resp.json()
                            logging.info(f"✅ TorrServer: File torrent uploaded successfully: {res_json.get('hash')}")
                            return True
                        else:
                            logging.error(f"❌ TorrServer upload failed: status={resp.status}")
                            return False
                except Exception as e:
                    logging.error(f"❌ TorrServer upload error: {e}")
                    return False
            else:
                # Add magnet/hash link
                url = f"{self.host}/torrents"
                payload = {
                    "action": "add",
                    "link": torrent_data,
                    "save_to_db": True
                }
                if title:
                    payload["title"] = title
                try:
                    async with session.post(url, json=payload, auth=self.auth, timeout=30) as resp:
                        if resp.status in (200, 201):
                            logging.info("✅ TorrServer: Torrent added successfully via JSON API")
                            return True
                        else:
                            logging.error(f"❌ TorrServer add torrent failed: status={resp.status}")
                            return False
                except Exception as e:
                    logging.error(f"❌ TorrServer add torrent error: {e}")
                    return False

    async def get_torrent_files(self, info_hash):
        """
        Gets the list of files in a torrent
        Returns:
            list of dicts containing 'id', 'path', 'length'
        """
        url = f"{self.host}/torrents"
        payload = {
            "action": "get",
            "hash": info_hash.lower()
        }
        try:
            async with aiohttp.ClientSession(trust_env=True) as session:
                async with session.post(url, json=payload, auth=self.auth, timeout=15) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        files = data.get('file_stats') or data.get('FileStats') or []
                        return files
                    else:
                        logging.warning(f"TorrServer get files failed: status={resp.status}")
                        return []
        except Exception as e:
            logging.error(f"TorrServer get files error: {e}")
            return []

    async def manage_stream(self, torrent_data, info_hash, is_file=False, season=None, episode=None, title=None):
        """
        Orchestrates adding torrent and finding target file index.
        Returns:
            target file index (int) or None
        """
        # First check if the torrent is already present in TorrServer
        files = await self.get_torrent_files(info_hash)
        if not files:
            # Not present, so add it
            if not await self.add_torrent(torrent_data, is_file, title):
                # Try getting files anyway in case it was added in a parallel request or returned an error code but succeeded
                files = await self.get_torrent_files(info_hash)
                if not files:
                    return None

        # Wait active for metadata initialization (max 10 seconds, check every 0.5s)
        logging.info("⚡ Torrent added to TorrServer, checking file stats...")
        for check in range(20):
            files = await self.get_torrent_files(info_hash)
            if files:
                target_file_id = None
                
                # Filter to video files only
                video_exts = ('.mkv', '.mp4', '.avi', '.mov', '.wmv', '.m4v', '.mpg', '.mpeg', '.ts')
                video_files = [f for f in files if f.get('path', '').lower().endswith(video_exts)]
                if not video_files:
                    video_files = files # fallback to all files if no video extensions match
                
                if season is not None and episode is not None:
                    # Episode selection logic
                    scored_files = []
                    for f in video_files:
                        path = f.get('path', '')
                        fs, fe = parse_season_episode_from_path(path)
                        score = 0
                        if fs == season and fe == episode:
                            score = 100
                        elif fs is None and fe == episode:
                            score = 50
                        
                        if score > 0:
                            scored_files.append((score, f))
                            
                    if scored_files:
                        # Sort by score desc, then size desc
                        scored_files.sort(key=lambda x: (x[0], x[1].get('length', 0)), reverse=True)
                        target_file_id = scored_files[0][1].get('id')
                else:
                    # Movie logic: select largest video file
                    if video_files:
                        largest = max(video_files, key=lambda x: x.get('length', 0))
                        target_file_id = largest.get('id')

                if target_file_id is not None:
                    logging.info(f"🎯 TorrServer Selected target file ID: {target_file_id}")
                    return target_file_id

            await asyncio.sleep(0.5)

        logging.error("❌ TorrServer failed to get metadata / target file index in time.")
        return None
