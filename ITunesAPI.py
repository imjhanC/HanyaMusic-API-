import requests
from typing import Optional, List, Dict
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime

class ITunes:
    ## Here is the get artist's songs by date released, song preview , thumbnail and sort by date 
    BASE_URL = "https://itunes.apple.com"

    def __init__(self, country: str = "US", timeout: int = 10):
        self.country = country
        self.timeout = timeout

    def _get(self, endpoint: str, params: Dict) -> Dict:
        url = f"{self.BASE_URL}/{endpoint}"
        try:
            response = requests.get(url, params=params, timeout=self.timeout)
            response.raise_for_status()
            return response.json()
        except requests.RequestException as e:
            print(f"Request failed: {e}")
            return {}

    def get_artist_id(self, artist_name: str) -> Optional[int]:
        params = {
            "term": artist_name,
            "entity": "musicArtist",
            "limit": 1,
            "country": self.country
        }
        data = self._get("search", params)
        results = data.get("results", [])
        if results:
            return results[0].get("artistId")
        return None

    def get_all_official_songs_by_artist(self, artist_name: str) -> List[Dict]:
        artist_id = self.get_artist_id(artist_name)
        if not artist_id:
            print(f"Artist '{artist_name}' not found.")
            return []

        # Step 1: Get all albums
        params = {"id": artist_id, "entity": "album", "limit": 200, "country": self.country}
        data = self._get("lookup", params)

        albums = [
            r for r in data.get("results", [])
            if r.get("collectionType") == "Album" and r.get("artistId") == artist_id
        ]

        # Sort albums newest-first
        albums.sort(key=lambda x: x.get("releaseDate", ""), reverse=True)

        all_songs = []

        def fetch_album_tracks(album):
            album_id = album["collectionId"]
            album_name = album["collectionName"]

            params = {"id": album_id, "entity": "song", "limit": 200, "country": self.country}
            tracks_data = self._get("lookup", params)

            tracks = []
            for t in tracks_data.get("results", []):
                if t.get("wrapperType") == "track" and t.get("artistId") == artist_id:
                    release_iso = t.get("releaseDate")
                    release_dt = datetime.fromisoformat(release_iso.replace("Z", "+00:00"))
                    tracks.append({
                        "song_name": t.get("trackName"),
                        "album_name": album_name,
                        "release_date": release_iso,
                        "release_month": release_dt.strftime("%B"),
                        "release_year": release_dt.year,
                        "preview_url": t.get("previewUrl"),
                        "track_number": t.get("trackNumber"),
                        "track_id": t.get("trackId"),
                        "thumbnail": t.get("artworkUrl100").replace("100x100bb", "600x600bb")
                    })

            # Sort tracks newest-first
            tracks.sort(key=lambda x: x["release_date"], reverse=True)
            return tracks

        # Fetch albums concurrently
        with ThreadPoolExecutor(max_workers=10) as executor:
            future_to_album = {executor.submit(fetch_album_tracks, album): album for album in albums}
            for future in as_completed(future_to_album):
                all_songs.extend(future.result())

        # Final sort newest-first
        all_songs.sort(key=lambda x: x["release_date"], reverse=True)
        return all_songs
    
    ## Get all top global artists 
    def get_top_global_artists(self, limit: int = 100) -> List[Dict]:
        """
        Get top global artists based on top songs feed (deduplicated) with high-res thumbnails.
        """
        url = f"https://itunes.apple.com/us/rss/topsongs/limit=200/json"  # fetch more to get enough unique artists
        try:
            response = requests.get(url, timeout=self.timeout)
            response.raise_for_status()
            data = response.json()
        except requests.RequestException as e:
            print(f"Request failed: {e}")
            return []

        seen_artists = set()
        artists = []

        entries = data.get("feed", {}).get("entry", [])
        for entry in entries:
            artist_info = entry.get("im:artist", {})
            name = artist_info.get("label")
            artist_link = artist_info.get("attributes", {}).get("href")
            
            if not name or name in seen_artists:
                continue

            seen_artists.add(name)

            # thumbnail
            images = entry.get("im:image", [])
            thumbnail = None
            if images:
                thumbnail = images[-1].get("label", "")
                if "100x100bb" in thumbnail:
                    thumbnail = thumbnail.replace("100x100bb", "600x600bb")
                elif "170x170bb" in thumbnail:
                    thumbnail = thumbnail.replace("170x170bb", "600x600bb")

            artists.append({
                "rank": len(artists) + 1,
                "artist_name": name,
                 #"artist_link": artist_link,
                "thumbnail": thumbnail
            })

            if len(artists) >= limit:
                break

        return artists
    
    ## Get all top global songs 
    def get_top_global_songs(self, limit: int = 200) -> List[Dict]:
        url = f"https://itunes.apple.com/us/rss/topsongs/limit={limit}/json"
        try:
            response = requests.get(url, timeout=self.timeout)
            response.raise_for_status()
            data = response.json()
        except requests.RequestException as e:
            print(f"Request failed: {e}")
            return []

        songs = []
        entries = data.get("feed", {}).get("entry", [])
        for idx, entry in enumerate(entries, start=1):
            song_name = entry.get("im:name", {}).get("label")
            artist_info = entry.get("im:artist", {})
            artist_name = artist_info.get("label")
            artist_link = artist_info.get("attributes", {}).get("href")
            song_link = entry.get("id", {}).get("label")
            thumbnail = None
            preview_url = None

            # Get highest-resolution image from feed
            images = entry.get("im:image", [])
            if images:
                thumbnail = images[-1].get("label", "")
                if "100x100bb" in thumbnail:
                    thumbnail = thumbnail.replace("100x100bb", "600x600bb")
                elif "170x170bb" in thumbnail:
                    thumbnail = thumbnail.replace("170x170bb", "600x600bb")

            # Get preview URL
            links = entry.get("link", [])
            if isinstance(links, list):
                for l in links:
                    attributes = l.get("attributes", {})
                    if attributes.get("type") == "audio/x-m4a":
                        preview_url = attributes.get("href")
                        break

            songs.append({
                "rank": idx,
                "song_name": song_name,
                "artist_name": artist_name,
                #"artist_link": artist_link,
                #"song_link": song_link,
                "thumbnail": thumbnail,
                "preview_url": preview_url
            })

        return songs

    ## Get top country songs by country code
    def get_top_country_songs(self, country_code: str = "us", limit: int = 100) -> List[Dict]:
        """
        Get today's top songs for a specific country from iTunes RSS feed.

        :param country_code: Country code (e.g., 'us', 'gb', 'jp')
        :param limit: Number of top songs to fetch (default 100)
        :return: List of top songs with rank, name, artist, links, and thumbnail
        """
        url = f"https://itunes.apple.com/{country_code}/rss/topsongs/limit={limit}/json"
        try:
            response = requests.get(url, timeout=self.timeout)
            response.raise_for_status()
            data = response.json()
        except requests.RequestException as e:
            print(f"Request failed: {e}")
            return []

        songs = []
        entries = data.get("feed", {}).get("entry", [])
        for idx, entry in enumerate(entries, start=1):
            song_name = entry.get("im:name", {}).get("label")
            artist_info = entry.get("im:artist", {})
            artist_name = artist_info.get("label")
            artist_link = artist_info.get("attributes", {}).get("href")
            song_link = entry.get("id", {}).get("label")
            thumbnail = None

            images = entry.get("im:image", [])
            if images:
                thumbnail = images[-1].get("label", "")
                if "100x100bb" in thumbnail:
                    thumbnail = thumbnail.replace("100x100bb", "600x600bb")
                elif "170x170bb" in thumbnail:
                    thumbnail = thumbnail.replace("170x170bb", "600x600bb")

            # Get preview URL
            links = entry.get("link", [])
            if isinstance(links, list):
                for l in links:
                    attributes = l.get("attributes", {})
                    if attributes.get("type") == "audio/x-m4a":
                        preview_url = attributes.get("href")
                        break

            songs.append({
                "rank": idx,
                "song_name": song_name,
                "artist_name": artist_name,
                #"artist_link": artist_link,
                #"song_link": song_link,
                "thumbnail": thumbnail,
                "preview_url": preview_url
            })

        return songs