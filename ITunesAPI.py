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