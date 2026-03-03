import requests
import os
from dotenv import load_dotenv
from typing import List, Dict, Any, Optional

load_dotenv()

class LastFMClient:
    """
    Client for interacting with Last.fm and TheAudioDB APIs.
    """
    BASE_URL = "http://ws.audioscrobbler.com/2.0/"

    def __init__(self):
        self._api_key = os.getenv("LASTFM_API_KEY")
        if not self._api_key:
            print("[LASTFM] Warning: LASTFM_API_KEY not found in environment variables")

    def get_global_top_artists(self, limit: int = 100) -> List[Dict[str, Any]]:
        """
        Fetch global top artists from Last.fm.
        """
        if not self._api_key:
            return []

        params = {
            "method": "chart.gettopartists",
            "api_key": self._api_key,
            "format": "json",
            "limit": limit
        }

        try:
            response = requests.get(self.BASE_URL, params=params, timeout=10)
            response.raise_for_status()
            data = response.json()
            
            if "artists" in data and "artist" in data["artists"]:
                return data["artists"]["artist"]
            return []
            
        except requests.RequestException as e:
            print(f"[LASTFM] Error fetching top artists: {e}")
            return []
    
    def get_artist_image(self, artist_name: str) -> Optional[str]:
        """
        Fetch artist image from TheAudioDB API.
        """
        url = "https://www.theaudiodb.com/api/v1/json/2/search.php"
        params = {"s": artist_name}

        try:
            response = requests.get(url, params=params, timeout=5)
            response.raise_for_status()
            data = response.json()
            
            artists = data.get("artists")
            if artists and len(artists) > 0:
                # Prioritize strArtistThumb, fallback to strArtistFanart
                artist_data = artists[0]
                image_url = artist_data.get("strArtistThumb") or artist_data.get("strArtistFanart")
                return image_url
            
        except requests.RequestException as e:
            print(f"[AUDIODB] Error fetching image for {artist_name}: {e}")
            
        return None
