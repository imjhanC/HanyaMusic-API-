import yt_dlp
import threading
import os
import uuid
import subprocess
import tempfile
from typing import Dict, List, Optional
from fastapi import HTTPException
import requests
from concurrent.futures import ThreadPoolExecutor, as_completed
import yt_dlp
from fastapi import HTTPException

_BASE_DIR = os.path.dirname(os.path.abspath(__file__))
COOKIES_FILE = os.path.join(_BASE_DIR, 'cookies.txt')  # Get the authenticated cookies.txt file path

# Deno path configuration for yt-dlp JS challenges solver ( Security bypass )
_DENO_PATH = os.path.join(_BASE_DIR, 'venv', 'lib64', 'python3.12', 'site-packages', 'deno.exe')
if not os.path.exists(_DENO_PATH):
    _DENO_PATH = os.path.join(_BASE_DIR, 'venv', 'lib', 'python3.12', 'site-packages', 'deno.exe')
_VENV_BIN = os.path.join(_BASE_DIR, 'venv', 'bin')
if _VENV_BIN not in os.environ.get("PATH", ""):
    os.environ["PATH"] = f"{_VENV_BIN}{os.pathsep}{os.environ.get('PATH', '')}"


class SearchHelper:
    @staticmethod
    def format_duration_fast(seconds):
        if not seconds or seconds <= 0:
            return "0:00"
        hours = int(seconds // 3600)
        minutes = int((seconds % 3600) // 60)
        secs = int(seconds % 60)
        if hours > 0:
            return f"{hours}:{minutes:02d}:{secs:02d}"
        return f"{minutes}:{secs:02d}"

    @staticmethod
    def format_views_fast(view_count):
        if not view_count or view_count <= 0:
            return "0 views"
        if view_count >= 1_000_000_000:
            return f"{view_count / 1_000_000_000:.1f}B views"
        elif view_count >= 1_000_000:
            return f"{view_count / 1_000_000:.1f}M views"
        elif view_count >= 1_000:
            return f"{view_count / 1_000:.1f}K views"
        return f"{view_count:,} views"

    @staticmethod
    def get_common_headers():
        return {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
            'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
            'Accept-Language': 'en-us,en;q=0.5',
            'Sec-Fetch-Mode': 'navigate',
            'Accept-Encoding': 'gzip, deflate',
            'DNT': '1',
            'Connection': 'keep-alive',
            'Upgrade-Insecure-Requests': '1',
        }

    @staticmethod
    def _get_cookie_opts() -> dict:
        if os.path.isfile(COOKIES_FILE):
            print(f"[CookieOpts] Using cookies from: {COOKIES_FILE}")
            return {'cookiefile': COOKIES_FILE}
        else:
            print(f"[CookieOpts] WARNING: cookies.txt not found at '{COOKIES_FILE}'.")
            return {}

    @staticmethod
    def is_valid_video(entry: Dict) -> bool:
        if not entry:
            return False
        video_id = entry.get('id', '')
        url = entry.get('url', '')
        if '/shorts/' in url or 'shorts' in video_id.lower():
            return False
        if not video_id or len(video_id) != 11:
            return False
        duration = entry.get('duration')
        if duration is not None and duration < 61:
            return False
        return True

    # Perform search based on keywords and return 20 results
    @classmethod
    def perform_search(cls, query: str, limit: Optional[int] = None) -> List[Dict]:
        if not query:
            return []
        try:
            thread_name = threading.current_thread().name
            clean_query = query.strip()
            print(f"[{thread_name}] Searching for: '{clean_query}'")

            search_opts = {
                'quiet': True,
                'no_warnings': True,
                'extract_flat': 'in_playlist',
                'skip_download': True,
                'ignoreerrors': True,
                'geo_bypass': True,
                'noplaylist': True,
                'socket_timeout': 8,
                'retries': 1,
                'format': 'best',
                'http_headers': cls.get_common_headers(),
                'nocheckcertificate': True,
                'no_color': True,
                'js_runtimes': {
                    'deno': {'path': _DENO_PATH},
                    'node': {},
                },
                'extractor_args': {
                    'youtube': {'skip': ['hls', 'dash', 'translated_subs']}
                },
                **cls._get_cookie_opts(),
            }

            fetch_count = (limit * 2) if limit else 40
            with yt_dlp.YoutubeDL(search_opts) as ydl:
                search_results = ydl.extract_info(
                    f"ytsearch{fetch_count}:{clean_query}", download=False
                )

            if not search_results or 'entries' not in search_results:
                return []

            filtered = []
            seen = set()
            target_limit = limit if limit else 20

            for entry in search_results.get('entries', []):
                if not entry or not cls.is_valid_video(entry):
                    continue
                vid = entry.get('id')
                if not vid or vid in seen:
                    continue
                seen.add(vid)
                filtered.append({
                    'title':         str(entry.get('title', 'No Title'))[:100],
                    'thumbnail_url': f"https://img.youtube.com/vi/{vid}/maxresdefault.jpg",
                    'videoId':       vid,
                    'uploader':      str(entry.get('uploader', 'Unknown'))[:50],
                    'duration':      cls.format_duration_fast(entry.get('duration')) if entry.get('duration') else 'Live/Unknown',
                    'view_count':    cls.format_views_fast(entry.get('view_count')),
                    'url':           f"https://www.youtube.com/watch?v={vid}"
                })
                if len(filtered) >= target_limit:
                    break

            print(f"[{thread_name}] {len(filtered)} valid results")
            return filtered

        except Exception as e:
            print(f"[SEARCH] yt-dlp search failed: {e}")
            return []

    # Extract high quality AUDIO STREAM from a Youtube Video ID
    @classmethod
    def get_audio_stream_url(cls, video_id: str) -> Dict:
        thread_name = threading.current_thread().name
        youtube_url = f"https://www.youtube.com/watch?v={video_id}"
        print(f"[{thread_name}] Audio extract for {video_id}")

        base_opts = {
            'quiet': True,
            'no_warnings': True,
            'extractor_retries': 2,
            'fragment_retries': 2,
            'socket_timeout': 15,
            'http_headers': cls.get_common_headers(),
            'nocheckcertificate': True,
            'no_color': True,
            'js_runtimes': {
                'deno': {'path': _DENO_PATH},
                'node': {},
            },
            **cls._get_cookie_opts(),
        }

        format_strategies = [
            {
                'label': 'bestaudio (raw)',
                'opts': {**base_opts, 'format': 'bestaudio[ext=webm]/bestaudio[ext=m4a]/bestaudio'}
            },
            {
                'label': 'bestaudio → mp3',
                'opts': {
                    **base_opts,
                    'format': 'bestaudio[ext=webm]/bestaudio[ext=m4a]/bestaudio',
                    'postprocessors': [{'key': 'FFmpegExtractAudio', 'preferredcodec': 'mp3', 'preferredquality': '320'}],
                }
            },
        ]

        last_error = None
        for strategy in format_strategies:
            try:
                with yt_dlp.YoutubeDL(strategy['opts']) as ydl:
                    info = ydl.extract_info(youtube_url, download=False)
                    if info and info.get('url'):
                        quality_info = f"{info['abr']}kbps MP3" if info.get('abr') else "320kbps MP3"
                        return {
                            'stream_url':    info['url'],
                            'title':         info.get('title', 'Unknown Title'),
                            'duration':      info.get('duration', 0),
                            'thumbnail_url': f"https://img.youtube.com/vi/{video_id}/maxresdefault.jpg",
                            'format':        'mp3',
                            'quality':       quality_info,
                            'headers':       info.get('http_headers') or cls.get_common_headers(),
                        }
            except Exception as e:
                last_error = str(e)
                if any(k in last_error.lower() for k in ('sign in', 'bot', 'private', 'unavailable', 'copyright')):
                    break

        error_msg = last_error or "No audio stream could be extracted"
        if 'bot' in error_msg.lower() or 'sign in' in error_msg.lower():
            raise HTTPException(status_code=503, detail="YouTube is temporarily blocking requests.")
        elif 'private' in error_msg.lower():
            raise HTTPException(status_code=403, detail="This video is private")
        elif 'unavailable' in error_msg.lower():
            raise HTTPException(status_code=404, detail="This video is not available")
        elif 'copyright' in error_msg.lower():
            raise HTTPException(status_code=451, detail="Not available due to copyright restrictions")
        else:
            raise HTTPException(status_code=500, detail=f"Failed to get audio stream: {error_msg}")

    # Extract high-quality video (and audio) stream URLs for a given YouTube video ID
    @classmethod
    def get_video_stream_url(cls, video_id: str) -> Dict:
        try:
            thread_name = threading.current_thread().name
            youtube_url = f"https://www.youtube.com/watch?v={video_id}"
            print(f"[{thread_name}] Video extract for {video_id}")

            opts = {
                'format': (
                    'bestvideo[height>=2160][ext=mp4]+bestaudio[ext=m4a]/'
                    'bestvideo[height>=1440][ext=mp4]+bestaudio[ext=m4a]/'
                    'bestvideo[height>=1080][ext=mp4]+bestaudio[ext=m4a]/'
                    'bestvideo[height>=720][ext=mp4]+bestaudio[ext=m4a]/'
                    'bestvideo[ext=mp4]+bestaudio[ext=m4a]/'
                    'bestvideo+bestaudio[ext=m4a]/'
                    'bestvideo+bestaudio/'
                    'best[ext=mp4][height>=1080]/'
                    'best[ext=mp4][height>=720]/'
                    'best[ext=mp4]/'
                    'best/'
                ),
                'quiet': True,
                'no_warnings': True,
                'extractor_retries': 2,
                'fragment_retries': 2,
                'merge_output_format': 'mp4',
                'socket_timeout': 20,
                'http_headers': cls.get_common_headers(),
                'nocheckcertificate': True,
                'no_color': True,
                'js_runtimes': {
                    'deno': {'path': _DENO_PATH},
                    'node': {},
                },
                **cls._get_cookie_opts(),
            }

            with yt_dlp.YoutubeDL(opts) as ydl:
                info = ydl.extract_info(youtube_url, download=False)
                if info:
                    if 'requested_formats' in info and info['requested_formats']:
                        video_url = audio_url = None
                        quality = "Unknown"
                        video_format = audio_format = None
                        for fmt in info['requested_formats']:
                            if fmt.get('vcodec') != 'none' and fmt.get('acodec') == 'none':
                                video_url = fmt.get('url')
                                video_format = fmt
                                quality = f"{fmt['height']}p" if fmt.get('height') else fmt.get('format_note', 'Unknown')
                            elif fmt.get('acodec') != 'none' and fmt.get('vcodec') == 'none':
                                audio_url = fmt.get('url')
                                audio_format = fmt
                        if video_url and audio_url:
                            fps = (video_format or {}).get('fps') or 30
                            vbr = (video_format or {}).get('vbr') or 0
                            quality_detail = quality + (f"{fps}fps" if fps > 30 else "") + (f" ({vbr}kbps)" if vbr > 0 else "")
                            return {
                                'video_url': video_url, 'audio_url': audio_url,
                                'title': info.get('title', 'Unknown Title'),
                                'duration': info.get('duration', 0),
                                'thumbnail_url': f"https://img.youtube.com/vi/{video_id}/maxresdefault.jpg",
                                'quality': quality_detail, 'stream_type': 'separate'
                            }
                    if info.get('url') and info.get('vcodec') != 'none' and info.get('acodec') != 'none':
                        quality = f"{info['height']}p" if info.get('height') else info.get('format_note', 'Unknown')
                        return {
                            'video_url': info['url'],
                            'title': info.get('title', 'Unknown Title'),
                            'duration': info.get('duration', 0),
                            'thumbnail_url': f"https://img.youtube.com/vi/{video_id}/maxresdefault.jpg",
                            'quality': quality, 'stream_type': 'combined'
                        }

            raise Exception("No suitable video stream found")

        except Exception as e:
            error_msg = str(e)
            if 'bot' in error_msg.lower() or 'sign in' in error_msg.lower():
                raise HTTPException(status_code=503, detail="YouTube is temporarily blocking requests.")
            elif 'private' in error_msg.lower():
                raise HTTPException(status_code=403, detail="This video is private")
            elif 'unavailable' in error_msg.lower():
                raise HTTPException(status_code=404, detail="This video is not available")
            else:
                raise HTTPException(status_code=500, detail=f"Failed to get video stream: {error_msg}")

    @staticmethod
    def _get_reconnect_flags() -> list:
        """
        Safe ffmpeg HTTP reconnect flags that work on all standard builds.
        """
        return [
            '-reconnect',           '1',
            '-reconnect_streamed',  '1',
            '-reconnect_delay_max', '3',
        ]

    # Merge video ( No sound ) with the audio using FFMPEG
    @staticmethod
    def merge_video_audio_ffmpeg_stream(video_url: str, audio_url: str) -> subprocess.Popen:
        """
        Returns an ffmpeg Popen whose stdout is a streaming fragmented MP4.
        """
        reconnect_flags = SearchHelper._get_reconnect_flags()

        cmd = [
            'ffmpeg', '-y', '-loglevel', 'error',
            *reconnect_flags, '-i', video_url,
            *reconnect_flags, '-i', audio_url,
            '-c:v', 'copy',
            '-c:a', 'copy',
            '-threads', '0',
            '-avoid_negative_ts',     'make_zero',
            '-fflags',                '+genpts+discardcorrupt',
            '-max_muxing_queue_size', '2048',
            '-movflags', 'frag_keyframe+empty_moov+default_base_moof',
            '-f', 'mp4',
            'pipe:1',
        ]

        proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, bufsize=0)
        print(f"[FFmpeg-STREAM] pid={proc.pid} started")
        return proc

    @classmethod
    def merge_video_audio_ffmpeg_to_path(cls, video_url: str, audio_url: str, output_path: str) -> None:
        """
        Download and merge video and audio streams to a specific file using ffmpeg.
        """
        reconnect_flags = cls._get_reconnect_flags()
        cmd = [
            'ffmpeg', '-y', '-loglevel', 'error',
            '-probesize',       '10M',
            '-analyzeduration', '2000000',
            *reconnect_flags, '-thread_queue_size', '1024', '-i', video_url,
            *reconnect_flags, '-thread_queue_size', '1024', '-i', audio_url,
            '-c:v', 'copy', '-c:a', 'copy',
            '-threads', '0',
            '-avoid_negative_ts',     'make_zero',
            '-fflags',                '+genpts+discardcorrupt',
            '-max_muxing_queue_size', '2048',
            '-movflags', '+faststart',
            output_path,
        ]

        print(f"[FFmpeg-DISK] Merging → {output_path}")
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=300)

        if result.returncode != 0:
            raise RuntimeError(
                f"ffmpeg failed (code {result.returncode}): {result.stderr.strip()}"
            )
        print(f"[FFmpeg-DISK] Done → {output_path} ({os.path.getsize(output_path):,} bytes)")

    @classmethod
    def merge_video_audio_ffmpeg(cls, video_url: str, audio_url: str, output_dir: str) -> str:
        """Merge to a UUID-named file in output_dir. Returns the output path."""
        os.makedirs(output_dir, exist_ok=True)
        out_path = os.path.join(output_dir, f"{uuid.uuid4().hex}.mp4")
        cls.merge_video_audio_ffmpeg_to_path(video_url, audio_url, out_path)
        return out_path

    @classmethod
    def get_mobile_video_stream_url(cls, video_id: str, merged_dir: Optional[str] = None) -> Dict:
        """
        Extract separate video + audio stream URLs for mobile clients.
        """
        try:
            thread_name = threading.current_thread().name
            youtube_url = f"https://www.youtube.com/watch?v={video_id}"
            print(f"[{thread_name}] Mobile extract for {video_id}")

            opts = {
                'format': (
                    'bestvideo[height>=2160][ext=mp4]+bestaudio[ext=m4a]/'
                    'bestvideo[height>=1440][ext=mp4]+bestaudio[ext=m4a]/'
                    'bestvideo[height>=1080][ext=mp4]+bestaudio[ext=m4a]/'
                    'bestvideo[height>=720][ext=mp4]+bestaudio[ext=m4a]/'
                    'bestvideo[ext=mp4]+bestaudio[ext=m4a]/'
                    'bestvideo+bestaudio[ext=m4a]/'
                    'bestvideo+bestaudio'
                ),
                'quiet': True,
                'no_warnings': True,
                'extractor_retries': 2,
                'fragment_retries': 2,
                'socket_timeout': 20,
                'http_headers': cls.get_common_headers(),
                'nocheckcertificate': True,
                'no_color': True,
                'js_runtimes': {
                    'deno': {'path': _DENO_PATH},
                    'node': {},
                },
                **cls._get_cookie_opts(),
            }

            with yt_dlp.YoutubeDL(opts) as ydl:
                info = ydl.extract_info(youtube_url, download=False)

                if not info:
                    raise Exception("No info returned from yt-dlp")

                video_url = audio_url = None
                quality = "Unknown"
                video_format = audio_format = None

                if 'requested_formats' in info and info['requested_formats']:
                    for fmt in info['requested_formats']:
                        if fmt.get('vcodec') != 'none' and fmt.get('acodec') == 'none':
                            video_url = fmt.get('url')
                            video_format = fmt
                            quality = f"{fmt['height']}p" if fmt.get('height') else fmt.get('format_note', 'Unknown')
                        elif fmt.get('acodec') != 'none' and fmt.get('vcodec') == 'none':
                            audio_url = fmt.get('url')
                            audio_format = fmt

                if not video_url or not audio_url:
                    raise Exception(
                        f"Could not extract separate streams — "
                        f"video={'found' if video_url else 'MISSING'}, "
                        f"audio={'found' if audio_url else 'MISSING'}"
                    )

                fps         = (video_format or {}).get('fps') or 30
                vbr         = (video_format or {}).get('vbr') or 0
                tbr         = (video_format or {}).get('tbr') or 0
                abr         = (audio_format or {}).get('abr') or 0
                quality_kbps = vbr if vbr > 0 else tbr
                quality_detail = (
                    quality
                    + (f"{int(fps)}fps" if fps > 30 else "")
                    + (f" ({quality_kbps:.3f}kbps)" if quality_kbps > 0 else "")
                )

                print(f"[{thread_name}] Streams — Video: {quality_detail}, Audio: {abr}kbps")

                return {
                    'video_url':     video_url,
                    'audio_url':     audio_url,
                    'title':         info.get('title', 'Unknown Title'),
                    'duration':      info.get('duration', 0),
                    'thumbnail_url': f"https://img.youtube.com/vi/{video_id}/maxresdefault.jpg",
                    'quality':       quality_detail,
                    'stream_type':   'separate',
                }

        except Exception as e:
            error_msg = str(e)
            print(f"[MOBILE] Error: {error_msg}")
            if 'bot' in error_msg.lower() or 'sign in' in error_msg.lower():
                raise HTTPException(status_code=503, detail="YouTube is temporarily blocking requests.")
            elif 'private' in error_msg.lower():
                raise HTTPException(status_code=403, detail="This video is private")
            elif 'unavailable' in error_msg.lower():
                raise HTTPException(status_code=404, detail="This video is not available")
            elif 'copyright' in error_msg.lower():
                raise HTTPException(status_code=451, detail="Not available due to copyright restrictions")
            else:
                raise HTTPException(status_code=500, detail=f"Failed to get mobile video stream: {error_msg}")

    # Fast merging the audio and video and download it into a video before putting it into a proxy server       
    @classmethod
    def merge_video_audio_ytdlp_to_path(cls, video_id: str, output_path: str) -> None:
        import re

        # Strip .mp4 from output_path for outtmpl since yt-dlp adds the ext.
        outtmpl = re.sub(r'\.mp4$', '', output_path)

        opts = {
            'format': (
                'bestvideo[height>=1080][ext=mp4]+bestaudio[ext=m4a]/'
                'bestvideo[height>=720][ext=mp4]+bestaudio[ext=m4a]/'
                'bestvideo[ext=mp4]+bestaudio[ext=m4a]/'
                'bestvideo+bestaudio'
            ),
            'merge_output_format': 'mp4',
            'outtmpl': outtmpl + '.%(ext)s',
            'concurrent_fragment_downloads': 512,
            'retries': 5,
            'fragment_retries': 5,
            'noplaylist': True,
            'quiet': True,
            'no_warnings': True,
            'nocheckcertificate': True,
            'socket_timeout': 20,
            'http_headers': cls.get_common_headers(),
            'js_runtimes': {
                'deno': {'path': _DENO_PATH},
                'node': {},
            },
            'postprocessor_args': {
                'ffmpeg': ['-movflags', '+faststart']
            },
            **cls._get_cookie_opts(),
        }

        youtube_url = f"https://www.youtube.com/watch?v={video_id}"
        print(f"[YT-DLP-MERGE] Fast download → {output_path}")

        with yt_dlp.YoutubeDL(opts) as ydl:
            ret = ydl.download([youtube_url])

        if ret != 0:
            raise RuntimeError(f"yt-dlp download failed with return code {ret}")

        if not os.path.exists(output_path):
            candidate = outtmpl + '.mp4'
            if os.path.exists(candidate) and candidate != output_path:
                os.rename(candidate, output_path)
            else:
                raise RuntimeError(f"Expected output not found: {output_path}")

        print(f"[YT-DLP-MERGE] ✓ Done → {output_path} ({os.path.getsize(output_path):,} bytes)")

    @classmethod
    def perform_search_first_result(cls, query: str) -> Optional[str]:
        try:
            opts = {
                'quiet': True,
                'no_warnings': True,
                'extract_flat': 'in_playlist',
                'skip_download': True,
                'ignoreerrors': True,
                'geo_bypass': True,
                'noplaylist': True,
                'socket_timeout': 6,
                'retries': 1,
                'http_headers': cls.get_common_headers(),
                'nocheckcertificate': True,
                'no_color': True,
                'js_runtimes': {
                    'deno': {'path': _DENO_PATH},
                    'node': {},
                },
                'extractor_args': {
                    'youtube': {'skip': ['hls', 'dash', 'translated_subs']}
                },
                **cls._get_cookie_opts(),
            }
            with yt_dlp.YoutubeDL(opts) as ydl:
                results = ydl.extract_info(f"ytsearch3:{query}", download=False)
            if not results or 'entries' not in results:
                return None
            for entry in results.get('entries', []):
                if entry and cls.is_valid_video(entry):
                    return entry.get('id')
            return None
        except Exception as e:
            print(f"[FAST-SEARCH] '{query}' failed: {e}")
            return None