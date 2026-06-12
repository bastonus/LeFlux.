import aiohttp
import logging
import urllib.parse
import xml.etree.ElementTree as ET
from utils import check_season_episode

class TR4KERService:
    def __init__(self, apikey):
        self.apikey = apikey
        self.base_url = "https://tr4ker.net/torznab"

    async def search(self, params):
        if not self.apikey:
            return []

        params['apikey'] = self.apikey

        # Log request (masking apikey)
        log_params = params.copy()
        log_params['apikey'] = '***APIKEY***'
        logging.info(f"TR4KER Search: {self.base_url}?{urllib.parse.urlencode(log_params)}")

        async with aiohttp.ClientSession(trust_env=True) as session:
            try:
                async with session.get(self.base_url, params=params, timeout=20) as response:
                    if response.status == 200:
                        text = await response.text()
                        return self._parse_xml(text)
                    else:
                        logging.warning(f"TR4KER Error {response.status}")
                        body = await response.text()
                        logging.warning(f"TR4KER Body: {body[:200]}")
            except Exception as e:
                logging.error(f"TR4KER Exception: {e}")
        return []

    def _parse_xml(self, xml_text):
        """Parse Torznab XML response"""
        try:
            root = ET.fromstring(xml_text)
        except ET.ParseError as e:
            logging.error(f"TR4KER XML Parse Error: {e}")
            return []

        # Torznab namespace
        ns = {'torznab': 'http://torznab.com/schemas/2015/feed'}

        items = root.findall('.//item')
        logging.info(f"TR4KER found {len(items)} results")

        normalized = []
        for item in items:
            title = item.findtext('title', '')
            guid = item.findtext('guid', '')

            # Enclosure (download link & size)
            enclosure = item.find('enclosure')
            download_link = ''
            size_text = '0'
            if enclosure is not None:
                download_link = enclosure.get('url', '')
                size_text = enclosure.get('length', '0')

            # Torznab attributes
            info_hash = None
            seeders = 0
            leechers = 0

            for attr in item.findall('torznab:attr', ns):
                name = attr.get('name')
                value = attr.get('value')
                if name == 'infohash':
                    info_hash = value.lower() if value else None
                elif name == 'seeders':
                    seeders = int(value) if value else 0
                elif name == 'peers':
                    leechers = int(value) if value else 0
                elif name == 'size':
                    size_text = value

            # Fallback to guid as hash
            if not info_hash:
                info_hash = guid.lower() if guid else None

            result = {
                "name": title,
                "size": int(size_text) if size_text else 0,
                "tracker_name": "TR4KER",
                "info_hash": info_hash,
                "magnet": None,
                "link": download_link,
                "source": "tr4ker",
                "seeders": seeders,
                "leechers": leechers
            }
            normalized.append(result)

        return normalized

    async def search_movie(self, title, year, imdb_id=None, tmdb_id=None):
        # TR4KER does not support imdbid/tmdbid search via torznab, so we query by title + year
        q = f"{title} {year}" if year else title
        params = {"t": "search", "q": q}
        return await self.search(params)

    async def search_series(self, title, season, episode, imdb_id=None, tmdb_id=None):
        # TR4KER does not support tvsearch parameters (season/episode/imdbid), so we query by title
        params = {"t": "search", "q": title}
        return await self.search(params)
