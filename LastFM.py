import requests
import os
from dotenv import load_dotenv
from typing import List, Dict, Any, Optional

load_dotenv()

class LastFMClient:
    BASE_URL = "http://ws.audioscrobbler.com/2.0/"

    def __init__(self):
        self.api_key = os.getenv("LASTFM_API_KEY")
        if not self.api_key:
            print("Warning: LASTFM_API_KEY not found in environment variables")

    def get_global_top_artists(self, limit: int = 100) -> List[Dict[str, Any]]:
        """
        Fetch global top artists from Last.fm
        """
        if not self.api_key:
            return []

        params = {
            "method": "chart.gettopartists",
            "api_key": self.api_key,
            "format": "json",
            "limit": limit
        }

        try:
            response = requests.get(self.BASE_URL, params=params)
            response.raise_for_status()
            data = response.json()
            
            if "artists" in data and "artist" in data["artists"]:
                return data["artists"]["artist"]
            return []
            
        except requests.RequestException as e:
            print(f"Error fetching data from Last.fm: {e}")
            return []
