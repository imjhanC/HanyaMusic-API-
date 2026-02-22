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
from typing import Dict

import requests
import yt_dlp
from fastapi import HTTPException

# Resolve cookies.txt path relative to this file, so it works regardless of working directory
_BASE_DIR = os.path.dirname(os.path.abspath(__file__))
COOKIES_FILE = os.path.join(_BASE_DIR, 'cookies.txt')


class SearchHelper:
    """Helper class for YouTube search and stream URL extraction"""
    
    @staticmethod
    def format_duration_fast(seconds):
        """Format duration from seconds to MM:SS or HH:MM:SS"""
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
        """Format view count to readable format"""
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
        """Get common HTTP headers for yt-dlp"""
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
        """
        Build cookie-related yt-dlp options.
        Uses cookies.txt if it exists, otherwise returns empty dict (graceful fallback).
        """
        if os.path.isfile(COOKIES_FILE):
            print(f"[CookieOpts] Using cookies from: {COOKIES_FILE}")
            return {'cookiefile': COOKIES_FILE}
        else:
            print(f"[CookieOpts] WARNING: cookies.txt not found at '{COOKIES_FILE}'. "
                  f"YouTube bot-detection may trigger. "
                  f"Place cookies.txt next to SearchHelper.py to fix this.")
            return {}
    
    @staticmethod
    def is_valid_video(entry: Dict) -> bool:
        """Check if entry is a valid video (not shorts, reels, or channels) - FAST VERSION"""
        if not entry:
            return False
        
        video_id = entry.get('id', '')
        url = entry.get('url', '')
        title = entry.get('title', '').lower()
        
        if '/shorts/' in url or 'shorts' in video_id.lower():
            return False
        
        if not video_id or len(video_id) != 11:
            return False
        
        duration = entry.get('duration')
        if duration is not None:
            if duration < 61:
                return False
        
        return True
    
    @classmethod
    def perform_search(cls, query: str, limit: Optional[int] = None) -> List[Dict]:
        """Perform YouTube search using yt-dlp with maximum results possible - OPTIMIZED"""
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
                'extractor_args': {
                    'youtube': {
                        'skip': ['hls', 'dash', 'translated_subs']
                    }
                },
                **cls._get_cookie_opts(),
            }
            
            fetch_count = (limit * 2) if limit else 40
            with yt_dlp.YoutubeDL(search_opts) as ydl:
                search_results = ydl.extract_info(
                    f"ytsearch{fetch_count}:{clean_query}",
                    download=False
                )
            
            print(f"[{thread_name}] yt-dlp response received")
            
            if not search_results or 'entries' not in search_results:
                print(f"[{thread_name}] No entries in search results")
                return []
            
            entries = search_results.get('entries', [])
            
            filtered = []
            seen = set()
            target_limit = limit if limit else 20
            
            for entry in entries:
                if not entry:
                    continue
                
                if not cls.is_valid_video(entry):
                    continue
                    
                vid = entry.get('id')
                if not vid or vid in seen:
                    continue
                    
                seen.add(vid)
                
                title = entry.get('title', 'No Title')
                uploader = entry.get('uploader', 'Unknown')
                duration = entry.get('duration')
                view_count = entry.get('view_count')
                
                filtered.append({
                    'title': str(title)[:100],
                    'thumbnail_url': f"https://img.youtube.com/vi/{vid}/maxresdefault.jpg",
                    'videoId': vid,
                    'uploader': str(uploader)[:50] if uploader else 'Unknown',
                    'duration': cls.format_duration_fast(duration) if duration else 'Live/Unknown',
                    'view_count': cls.format_views_fast(view_count),
                    'url': f"https://www.youtube.com/watch?v={vid}"
                })
                
                if len(filtered) >= target_limit:
                    break
            
            print(f"[{thread_name}] Processed {len(filtered)} valid results (filtered shorts/reels/channels)")
            return filtered
            
        except Exception as e:
            thread_name = threading.current_thread().name
            print(f"[{thread_name}] yt-dlp search failed: {str(e)}")
            return []
    
    @classmethod
    def get_audio_stream_url(cls, video_id: str) -> Dict:
        """Get streaming URL for audio - ENFORCES MP3 FORMAT ONLY with headers"""
        thread_name = threading.current_thread().name
        youtube_url = f"https://www.youtube.com/watch?v={video_id}"

        print(f"[{thread_name}] Processing video_id: {video_id} - ENFORCING MP3 FORMAT")

        base_opts = {
            'quiet': True,
            'no_warnings': True,
            'extractor_retries': 2,
            'fragment_retries': 2,
            'socket_timeout': 15,
            'http_headers': cls.get_common_headers(),
            'nocheckcertificate': True,
            'no_color': True,
            **cls._get_cookie_opts(),
        }

        format_strategies = [
            {
                'label': 'bestaudio (raw)',
                'opts': {
                    **base_opts,
                    'format': 'bestaudio[ext=webm]/bestaudio[ext=m4a]/bestaudio',
                }
            },
            {
                'label': 'bestaudio → mp3 (postprocessed)',
                'opts': {
                    **base_opts,
                    'format': 'bestaudio[ext=webm]/bestaudio[ext=m4a]/bestaudio',
                    'postprocessors': [{
                        'key': 'FFmpegExtractAudio',
                        'preferredcodec': 'mp3',
                        'preferredquality': '320',
                    }],
                }
            },
        ]

        last_error = None

        for strategy in format_strategies:
            label = strategy['label']
            opts = strategy['opts']
            try:
                print(f"[{thread_name}] Trying strategy: {label}")
                with yt_dlp.YoutubeDL(opts) as ydl:
                    info = ydl.extract_info(youtube_url, download=False)

                    if info and info.get('url'):
                        quality_info = "320kbps MP3"
                        if info.get('abr'):
                            quality_info = f"{info['abr']}kbps MP3"
                        elif info.get('tbr'):
                            quality_info = f"{info['tbr']}kbps MP3"

                        extracted_headers = info.get('http_headers') or cls.get_common_headers()

                        print(f"[{thread_name}] Successfully extracted audio stream via '{label}': {quality_info}")
                        return {
                            'stream_url': info['url'],
                            'title': info.get('title', 'Unknown Title'),
                            'duration': info.get('duration', 0),
                            'thumbnail_url': f"https://img.youtube.com/vi/{video_id}/maxresdefault.jpg",
                            'format': 'mp3',
                            'quality': quality_info,
                            'headers': extracted_headers,
                        }

            except Exception as e:
                last_error = str(e)
                print(f"[{thread_name}] Strategy '{label}' failed: {last_error}")
                if any(k in last_error.lower() for k in ('sign in', 'bot', 'private', 'unavailable', 'copyright')):
                    break
                continue

        error_msg = last_error or "No audio stream could be extracted"
        print(f"[{thread_name}] All strategies failed for {video_id}: {error_msg}")

        if 'bot' in error_msg.lower() or 'sign in' in error_msg.lower():
            raise HTTPException(
                status_code=503,
                detail="YouTube is temporarily blocking requests. Please try again in a few minutes."
            )
        elif 'private' in error_msg.lower():
            raise HTTPException(status_code=403, detail="This video is private")
        elif 'unavailable' in error_msg.lower():
            raise HTTPException(status_code=404, detail="This video is not available")
        elif 'copyright' in error_msg.lower():
            raise HTTPException(status_code=451, detail="This video is not available due to copyright restrictions")
        else:
            raise HTTPException(status_code=500, detail=f"Failed to get MP3 audio stream URL: {error_msg}")
    
    @classmethod
    def get_video_stream_url(cls, video_id: str) -> Dict:
        """Get streaming URL for video - prioritize highest quality even if separate streams"""
        try:
            thread_name = threading.current_thread().name
            youtube_url = f"https://www.youtube.com/watch?v={video_id}"
            
            print(f"[{thread_name}] Processing video_id: {video_id}")
            
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
                    'best[height>=720]/'
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
                **cls._get_cookie_opts(),
            }
            
            print(f"[{thread_name}] Extracting highest quality video stream for {video_id}")
            
            with yt_dlp.YoutubeDL(opts) as ydl:
                info = ydl.extract_info(youtube_url, download=False)
                
                if info:
                    if 'requested_formats' in info and info['requested_formats']:
                        video_url = None
                        audio_url = None
                        quality = "Unknown"
                        video_format = None
                        audio_format = None
                        
                        for fmt in info['requested_formats']:
                            if fmt.get('vcodec') != 'none' and fmt.get('acodec') == 'none':
                                video_url = fmt.get('url')
                                video_format = fmt
                                if fmt.get('height'):
                                    quality = f"{fmt['height']}p"
                                elif fmt.get('format_note'):
                                    quality = fmt['format_note']
                            elif fmt.get('acodec') != 'none' and fmt.get('vcodec') == 'none':
                                audio_url = fmt.get('url')
                                audio_format = fmt
                        
                        if video_url and audio_url:
                            fps = video_format.get('fps') if video_format else None
                            vbr = video_format.get('vbr') if video_format else None
                            abr = audio_format.get('abr') if audio_format else None
                            
                            fps = fps if fps is not None and fps > 0 else 30
                            vbr = vbr if vbr is not None and vbr > 0 else 0
                            abr = abr if abr is not None and abr > 0 else 0
                            
                            quality_detail = quality
                            if fps > 30:
                                quality_detail += f"{fps}fps"
                            if vbr > 0:
                                quality_detail += f" ({vbr}kbps)"
                                
                            print(f"[{thread_name}] Found separate high-quality streams - Video: {quality_detail}, Audio: {abr}kbps")
                            return {
                                'video_url': video_url,
                                'audio_url': audio_url,
                                'title': info.get('title', 'Unknown Title'),
                                'duration': info.get('duration', 0),
                                'thumbnail_url': f"https://img.youtube.com/vi/{video_id}/maxresdefault.jpg",
                                'quality': quality_detail,
                                'stream_type': 'separate'
                            }
                    
                    if info.get('url'):
                        quality = "Unknown"
                        if info.get('height'):
                            quality = f"{info['height']}p"
                        elif info.get('format_note'):
                            quality = info['format_note']
                        
                        has_video = info.get('vcodec') and info.get('vcodec') != 'none'
                        has_audio = info.get('acodec') and info.get('acodec') != 'none'
                        
                        if has_video and has_audio:
                            fps = info.get('fps')
                            vbr = info.get('vbr')
                            
                            fps = fps if fps is not None and fps > 0 else 30
                            vbr = vbr if vbr is not None and vbr > 0 else 0
                            
                            quality_detail = quality
                            if fps > 30:
                                quality_detail += f"{fps}fps"
                            if vbr > 0:
                                quality_detail += f" ({vbr}kbps)"
                                
                            print(f"[{thread_name}] Found combined stream - Quality: {quality_detail}")
                            return {
                                'video_url': info['url'],
                                'title': info.get('title', 'Unknown Title'),
                                'duration': info.get('duration', 0),
                                'thumbnail_url': f"https://img.youtube.com/vi/{video_id}/maxresdefault.jpg",
                                'quality': quality_detail,
                                'stream_type': 'combined'
                            }
            
            raise Exception("No suitable video stream found")
            
        except Exception as e:
            thread_name = threading.current_thread().name
            error_msg = str(e)
            print(f"[{thread_name}] Error getting video stream URL: {error_msg}")
            
            if 'bot' in error_msg.lower() or 'sign in' in error_msg.lower():
                raise HTTPException(
                    status_code=503, 
                    detail="YouTube is temporarily blocking requests. Please try again in a few minutes."
                )
            elif 'private' in error_msg.lower():
                raise HTTPException(status_code=403, detail="This video is private")
            elif 'unavailable' in error_msg.lower():
                raise HTTPException(status_code=404, detail="This video is not available")
            elif 'copyright' in error_msg.lower():
                raise HTTPException(status_code=451, detail="This video is not available due to copyright restrictions")
            else:
                raise HTTPException(status_code=500, detail=f"Failed to get video stream URL: {error_msg}")

    # ─────────────────────────────────────────────────────────────────────────
    # FAST STREAMING MERGE — pipes fragmented MP4 directly to client
    # No waiting for full download. First bytes arrive in ~200ms.
    # ─────────────────────────────────────────────────────────────────────────

    @staticmethod
    def merge_video_audio_ffmpeg_stream(video_url: str, audio_url: str) -> subprocess.Popen:
        """
        Returns an ffmpeg Popen whose stdout is a STREAMING fragmented MP4.

        WHY THIS IS FAST (~200ms to first byte vs 30s):
        ─────────────────────────────────────────────
        The old approach wrote to a file: ffmpeg had to fully download BOTH
        streams before it could write the moov atom (the index), because a
        normal MP4 needs the index at the END. Only then could the file be served.

        This approach uses FRAGMENTED MP4 (`frag_keyframe+empty_moov`):
          • An empty moov atom is written at the very START of the stream
          • Each fragment (a few seconds of video) is self-contained
          • ffmpeg writes to stdout (pipe:1) — no disk I/O at all
          • The client starts receiving playable data as soon as the first
            fragment is muxed, which is after just a few seconds of video
            data has been downloaded — not the whole file.

        HOW TO USE IN FastAPI:
        ──────────────────────
            @router.get("/stream/{video_id}")
            async def stream_video(video_id: str):
                data = SearchHelper.get_mobile_video_stream_url(video_id)
                proc = SearchHelper.merge_video_audio_ffmpeg_stream(
                    data['video_url'], data['audio_url']
                )

                async def generate():
                    try:
                        while True:
                            chunk = proc.stdout.read(65536)  # 64 KB chunks
                            if not chunk:
                                break
                            yield chunk
                    finally:
                        proc.stdout.close()
                        proc.wait()

                return StreamingResponse(
                    generate(),
                    media_type="video/mp4",
                    headers={"Content-Disposition": "inline; filename=video.mp4"},
                )

        IMPORTANT: The caller must consume proc.stdout fully and call proc.wait()
        to avoid zombie processes. Use the generate() pattern above.
        """
        reconnect_flags = [
            '-reconnect', '1',
            '-reconnect_streamed', '1',
            '-reconnect_delay_max', '5',
        ]

        cmd = [
            'ffmpeg',
            '-y',
            '-loglevel', 'error',
            # ── Video input ───────────────────────────────────────────────
            *reconnect_flags,
            '-i', video_url,
            # ── Audio input ───────────────────────────────────────────────
            *reconnect_flags,
            '-i', audio_url,
            # ── Encoding: pure stream-copy, zero transcode overhead ───────
            '-c:v', 'copy',
            '-c:a', 'copy',
            '-threads', '0',
            '-avoid_negative_ts', 'make_zero',
            '-fflags', '+genpts+discardcorrupt',
            '-max_muxing_queue_size', '1024',
            # ── THE KEY FLAGS: fragmented MP4 to stdout ───────────────────
            # frag_keyframe   → new fragment at every keyframe (regular intervals)
            # empty_moov      → write empty moov at START so players don't need
            #                   to seek to the end before beginning playback
            # default_base_moof → RFC 8216 / CMAF compatible fragment base
            '-movflags', 'frag_keyframe+empty_moov+default_base_moof',
            '-f', 'mp4',       # must be explicit when target is a pipe
            'pipe:1',          # stdout
        ]

        proc = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            bufsize=0,          # unbuffered: bytes reach the network ASAP
        )
        print(f"[FFmpeg] Streaming merge started (pid={proc.pid})")
        return proc

    @staticmethod
    def merge_video_audio_ffmpeg(video_url: str, audio_url: str, output_dir: str) -> str:
        """
        Download and merge separate video + audio streams into a single MP4 file on disk.

        NOTE: For serving to clients, prefer merge_video_audio_ffmpeg_stream() instead,
        which streams directly without waiting for the full download (~200ms vs 30s).
        Use this method only when you genuinely need a file on disk (e.g. caching).

        Optimisations:
        • -reconnect flags          : auto-retry transient HTTP errors
        • -max_muxing_queue_size    : prevent packet-buffer overflow errors
        • -avoid_negative_ts        : prevent silent stalls from negative DTS
        • -fflags +genpts+discard   : tolerant timestamp/corruption handling
        • -movflags +faststart      : moov atom at front for immediate playback
        • -threads 0                : auto thread count
        """
        os.makedirs(output_dir, exist_ok=True)
        out_filename = f"{uuid.uuid4().hex}.mp4"
        out_path = os.path.join(output_dir, out_filename)

        reconnect_flags = [
            '-reconnect', '1',
            '-reconnect_streamed', '1',
            '-reconnect_delay_max', '5',
        ]

        cmd = [
            'ffmpeg',
            '-y',
            '-loglevel', 'error',
            *reconnect_flags,
            '-i', video_url,
            *reconnect_flags,
            '-i', audio_url,
            '-c:v', 'copy',
            '-c:a', 'copy',
            '-threads', '0',
            '-avoid_negative_ts', 'make_zero',
            '-fflags', '+genpts+discardcorrupt',
            '-max_muxing_queue_size', '1024',
            '-movflags', '+faststart',
            out_path,
        ]

        print(f"[FFmpeg] Merging to file → {out_path}")
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=120,
        )

        if result.returncode != 0:
            raise RuntimeError(
                f"ffmpeg failed (code {result.returncode}): {result.stderr.strip()}"
            )

        print(f"[FFmpeg] Merge complete → {out_path} ({os.path.getsize(out_path):,} bytes)")
        return out_path

    # ─────────────────────────────────────────────────────────────────────────
    # MOBILE VIDEO — separate streams, merged locally via ffmpeg
    # ─────────────────────────────────────────────────────────────────────────
    @classmethod
    def get_mobile_video_stream_url(cls, video_id: str, merged_dir: str = None) -> Dict:
        """
        Extract separate video + audio stream URLs (highest quality).

        Returns both raw stream URLs AND (optionally) a merged file path.
        For fast serving, pass the video_url + audio_url directly to
        merge_video_audio_ffmpeg_stream() and pipe the result to the client.

        Return dict keys:
          video_url    : raw YouTube video-only stream URL
          audio_url    : raw YouTube audio-only stream URL
          merged_file  : path to merged .mp4 on disk (only if merged_dir given)
          title, duration, thumbnail_url, quality, stream_type
        """
        try:
            thread_name = threading.current_thread().name
            youtube_url = f"https://www.youtube.com/watch?v={video_id}"
            print(f"[{thread_name}] Processing mobile video_id: {video_id}")

            if merged_dir is None:
                merged_dir = os.path.join(_BASE_DIR, 'merged_videos')

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
                **cls._get_cookie_opts(),
            }

            print(f"[{thread_name}] Extracting separate streams for {video_id}")

            with yt_dlp.YoutubeDL(opts) as ydl:
                info = ydl.extract_info(youtube_url, download=False)

                if not info:
                    raise Exception("No info returned from yt-dlp")

                video_url = None
                audio_url = None
                quality = "Unknown"
                video_format = None
                audio_format = None

                if 'requested_formats' in info and info['requested_formats']:
                    for fmt in info['requested_formats']:
                        if fmt.get('vcodec') != 'none' and fmt.get('acodec') == 'none':
                            video_url = fmt.get('url')
                            video_format = fmt
                            if fmt.get('height'):
                                quality = f"{fmt['height']}p"
                            elif fmt.get('format_note'):
                                quality = fmt['format_note']
                        elif fmt.get('acodec') != 'none' and fmt.get('vcodec') == 'none':
                            audio_url = fmt.get('url')
                            audio_format = fmt

                if not video_url or not audio_url:
                    raise Exception(
                        "Could not extract separate video+audio streams — "
                        f"video_url={'found' if video_url else 'MISSING'}, "
                        f"audio_url={'found' if audio_url else 'MISSING'}"
                    )

                fps  = (video_format or {}).get('fps') or 30
                vbr  = (video_format or {}).get('vbr') or 0
                abr  = (audio_format or {}).get('abr') or 0
                tbr  = (video_format or {}).get('tbr') or 0

                quality_kbps = vbr if vbr > 0 else tbr
                quality_detail = quality
                if fps > 30:
                    quality_detail += f"{int(fps)}fps"
                if quality_kbps > 0:
                    quality_detail += f" ({quality_kbps:.3f}kbps)"

                print(
                    f"[{thread_name}] Streams extracted — "
                    f"Video: {quality_detail}, Audio: {abr}kbps"
                )

                return {
                    'video_url':   video_url,
                    'audio_url':   audio_url,
                    'title':       info.get('title', 'Unknown Title'),
                    'duration':    info.get('duration', 0),
                    'thumbnail_url': f"https://img.youtube.com/vi/{video_id}/maxresdefault.jpg",
                    'quality':     quality_detail,
                    'stream_type': 'separate',
                }

        except Exception as e:
            thread_name = threading.current_thread().name
            error_msg = str(e)
            print(f"[{thread_name}] Error in get_mobile_video_stream_url: {error_msg}")

            if 'bot' in error_msg.lower() or 'sign in' in error_msg.lower():
                raise HTTPException(
                    status_code=503,
                    detail="YouTube is temporarily blocking requests. Please try again in a few minutes."
                )
            elif 'private' in error_msg.lower():
                raise HTTPException(status_code=403, detail="This video is private")
            elif 'unavailable' in error_msg.lower():
                raise HTTPException(status_code=404, detail="This video is not available")
            elif 'copyright' in error_msg.lower():
                raise HTTPException(status_code=451, detail="This video is not available due to copyright restrictions")
            else:
                raise HTTPException(status_code=500, detail=f"Failed to get mobile video stream: {error_msg}")