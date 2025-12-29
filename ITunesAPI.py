import requests
from typing import Optional, List, Dict
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
import random

class ITunes:
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
    
    def get_artist_songs_with_sample_thumbnails(self, artist_name: str) -> Dict:
        """
        Get all songs by an artist with additional sample thumbnail URLs for 3 random songs
        """
        all_songs = self.get_all_official_songs_by_artist(artist_name)
        
        if not all_songs:
            return {
                "artist": artist_name,
                "total_songs": 0,
                "albums": {},
                "sample_thumbnails": []
            }
        
        # Group songs by album
        albums_dict = {}
        for song in all_songs:
            album = song["album_name"]
            song_info = {
                "song_name": song["song_name"],
                "release_date": song["release_date"],
                "release_month": song["release_month"],
                "release_year": song["release_year"],
                "thumbnail": song.get("thumbnail"),
                "preview_url": song.get("preview_url"),
            }
            if album not in albums_dict:
                albums_dict[album] = []
            albums_dict[album].append(song_info)
        
        # Select 3 random songs for sample thumbnails
        sample_thumbnails = []
        if len(all_songs) >= 3:
            random_songs = random.sample(all_songs, 3)
            for song in random_songs:
                sample_thumbnails.append(song["thumbnail"])
        else:
            # If fewer than 3 songs, use all available
            for song in all_songs:
                sample_thumbnails.append(song["thumbnail"])
        
        return {
            "artist": artist_name,
            "total_songs": len(all_songs),
            "total_albums": len(albums_dict),
            "albums": albums_dict,
            "sample_thumbnails": sample_thumbnails
        }
    
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
                "thumbnail": thumbnail
            })

            if len(artists) >= limit:
                break

        return artists
    
    def get_top_global_artists_with_thumbnails(self, limit: int = 100) -> Dict:
        """
        Get top global artists with sample thumbnail URLs for 5 random artists
        """
        artists = self.get_top_global_artists(limit=limit)
        
        if not artists:
            return {
                "total_artists": 0,
                "artists": [],
                "sample_thumbnails": []
            }
        
        # Extract thumbnail URLs from all artists (filter out None)
        all_thumbnails = [artist["thumbnail"] for artist in artists if artist["thumbnail"]]
        
        # Select 3 random thumbnails
        sample_thumbnails = []
        if len(all_thumbnails) >= 3:
            sample_thumbnails = random.sample(all_thumbnails,3)
        else:
            sample_thumbnails = all_thumbnails
        
        return {
            "total_artists": len(artists),
            "artists": artists,
            "sample_thumbnails": sample_thumbnails
        }
    
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
                "thumbnail": thumbnail,
                "preview_url": preview_url
            })

        return songs
    
    def get_top_global_songs_with_thumbnails(self, limit: int = 100) -> Dict:
        """
        Get top global songs with sample thumbnail URLs for 5 random songs
        """
        songs = self.get_top_global_songs(limit=limit)
        
        if not songs:
            return {
                "total_songs": 0,
                "songs": [],
                "sample_thumbnails": []
            }
        
        # Extract thumbnail URLs from all songs (filter out None)
        all_thumbnails = [song["thumbnail"] for song in songs if song["thumbnail"]]
        
        # Select 5 random thumbnails
        sample_thumbnails = []
        if len(all_thumbnails) >= 3:
            sample_thumbnails = random.sample(all_thumbnails, 3)
        else:
            sample_thumbnails = all_thumbnails
        
        return {
            "total_songs": len(songs),
            "songs": songs,
            "sample_thumbnails": sample_thumbnails
        }

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
                "thumbnail": thumbnail,
                "preview_url": preview_url
            })

        return songs
    
    def get_top_country_songs_with_thumbnails(self, country_code: str = "us", limit: int = 100) -> Dict:
        """
        Get top country songs with sample thumbnail URLs for 5 random songs
        """
        songs = self.get_top_country_songs(country_code=country_code, limit=limit)
        
        if not songs:
            return {
                "country": country_code,
                "total_songs": 0,
                "songs": [],
                "sample_thumbnails": []
            }
        
        # Extract thumbnail URLs from all songs (filter out None)
        all_thumbnails = [song["thumbnail"] for song in songs if song["thumbnail"]]
        
        # Select 5 random thumbnails
        sample_thumbnails = []
        if len(all_thumbnails) >= 3:
            sample_thumbnails = random.sample(all_thumbnails, 3)
        else:
            sample_thumbnails = all_thumbnails
        
        return {
            "country": country_code,
            "total_songs": len(songs),
            "songs": songs,
            "sample_thumbnails": sample_thumbnails
        }