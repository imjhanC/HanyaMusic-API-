from fastapi import FastAPI, HTTPException, Query, BackgroundTasks
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
import uvicorn
from typing import List, Dict, Optional, Tuple
import asyncio
import concurrent.futures
from pydantic import BaseModel
import hashlib
from datetime import datetime
import gc
import subprocess 
from datetime import datetime, time

# Importing other classes
from AdvancedCache import AdvancedCache
from RequestDeduplicator import RequestDeduplicator
from LoadBalancer import LoadBalancer
from SearchHelper import SearchHelper
from LastFM import LastFMClient
from ITunesAPI import ITunes

app = FastAPI(title="HanyaMusic Music Streaming API", version="3.0.0")

# Enable CORS for React Native
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

class SearchResult(BaseModel):
    title: str
    thumbnail_url: str
    videoId: str
    uploader: str
    duration: str
    view_count: str
    url: str

class StreamResponse(BaseModel):
    stream_url: str
    title: str
    duration: int
    thumbnail_url: str
    format: str  
    quality: str 
    cached: Optional[bool] = False

class VideoStreamResponse(BaseModel):
    video_url: str
    audio_url: Optional[str] = None
    title: str
    duration: int
    thumbnail_url: str
    quality: str
    stream_type: str
    cached: Optional[bool] = False

# Global caches for each endpoint
search_cache = AdvancedCache(max_size=500, ttl_minutes=15)
audio_cache = AdvancedCache(max_size=1000, ttl_minutes=60)
video_cache = AdvancedCache(max_size=800, ttl_minutes=45)

# REQUEST DEDUPLICATION SYSTEM
request_deduplicator = RequestDeduplicator()
load_balancer = LoadBalancer()
lastfm_client = LastFMClient()
itunes_client = ITunes()

def create_cache_key(func_name: str, *args, **kwargs) -> str:
    """Create a consistent cache key"""
    key_data = f"{func_name}:{str(args)}:{str(sorted(kwargs.items()))}"
    return hashlib.md5(key_data.encode()).hexdigest()

# MULTIPLE THREAD POOLS FOR MAXIMUM CONCURRENCY
search_executors = [
    concurrent.futures.ThreadPoolExecutor(max_workers=4, thread_name_prefix=f"Search-Pool{i}")
    for i in range(3)
]

audio_executors = [
    concurrent.futures.ThreadPoolExecutor(max_workers=4, thread_name_prefix=f"Audio-Pool{i}")
    for i in range(3)
]

video_executors = [
    concurrent.futures.ThreadPoolExecutor(max_workers=4, thread_name_prefix=f"Video-Pool{i}")
    for i in range(3)
]

async def run_yt_dlp_update():
    """Helper function to run yt-dlp update"""
    print("[YT-DLP] Running yt-dlp update...")
    try:
        result = subprocess.run(
            ["python", "-m", "pip", "install", "-U", "yt-dlp"],
            check=True,
            capture_output=True,
            text=True
        )
        print("[YT-DLP] Update completed successfully.")
        if result.stdout:
            print(f"[YT-DLP] Output: {result.stdout}")
        return True
    except subprocess.CalledProcessError as e:
        print(f"[YT-DLP] Update failed: {e}")
        if e.stderr:
            print(f"[YT-DLP] Error: {e.stderr}")
        return False

async def update_yt_dlp_daily():
    """Run pip install -U yt-dlp daily at 12:00 AM"""
    # Run immediately on startup
    print("[STARTUP] Running initial yt-dlp update...")
    await run_yt_dlp_update()
    
    # Then schedule daily updates
    while True:
        now = datetime.now()
        # Calculate next midnight
        target = datetime.combine(now.date(), time(0, 0))
        if now >= target:
            # If it's already past midnight today, schedule for tomorrow
            from datetime import timedelta
            target = target + timedelta(days=1)
        
        wait_seconds = (target - now).total_seconds()
        print(f"[CRON] Next yt-dlp update scheduled in {wait_seconds/3600:.2f} hours")

        # Wait until midnight
        await asyncio.sleep(wait_seconds)

        # Run the update
        await run_yt_dlp_update()

# Background task for cache cleanup
async def periodic_cache_cleanup():
    """Periodically clean up expired cache entries"""
    while True:
        await asyncio.sleep(300)
        try:
            print("[CACHE] Running periodic cleanup...")
            search_cache._cleanup_expired()
            audio_cache._cleanup_expired()
            video_cache._cleanup_expired()
            
            gc.collect()
            print("[CACHE] Cleanup completed")
        except Exception as e:
            print(f"[CACHE] Cleanup error: {e}")

@app.on_event("startup")
async def startup_event():
    asyncio.create_task(periodic_cache_cleanup())
    asyncio.create_task(update_yt_dlp_daily())
    print("🚀 High-Performance API started with MP3-only audio streams!")

async def cleanup_executors():
    """Gracefully shutdown all thread pools"""
    print("Shutting down thread pools...")
    for executor in search_executors + audio_executors + video_executors:
        executor.shutdown(wait=True)
    print("All thread pools shut down successfully")

@app.on_event("shutdown")
async def shutdown_event():
    await cleanup_executors()

@app.get("/")
async def root():
    return {
        "message": "Ultra High-Performance Music Streaming API with MP3-Only Audio!",
        "performance": {
            "search_threads": 12,
            "audio_stream_threads": 12,
            "video_stream_threads": 12,
            "total_threads": 36,
            "audio_format": "MP3 ONLY (320kbps preferred)",
            "features": [
                "Advanced caching system",
                "Request deduplication", 
                "Load balancing",
                "Multiple thread pools per endpoint",
                "MP3-only audio streaming",
                "Auto yt-dlp updates (startup + daily at midnight)"
            ]
        }
    }

async def cached_search(q: str, limit: Optional[int] = None) -> Tuple[List[SearchResult], bool]:
    """Search with caching and deduplication"""
    cache_key = create_cache_key("search", q, limit)
    
    cached_result = search_cache.get(cache_key)
    if cached_result:
        print(f"[SEARCH] Cache HIT for query: {q}")
        return cached_result, True
    
    print(f"[SEARCH] Cache MISS for query: {q}")
    
    async def execute_search():
        executor = load_balancer.get_least_loaded_executor(search_executors)
        loop = asyncio.get_event_loop()
        results = await loop.run_in_executor(executor, SearchHelper.perform_search, q.strip(), limit)
        
        search_cache.set(cache_key, results)
        return results
    
    results = await request_deduplicator.get_or_execute(cache_key, execute_search)
    return results, False

async def cached_audio_stream(video_id: str) -> Tuple[StreamResponse, bool]:
    """Audio stream with caching and deduplication - RETURNS MP3 ONLY"""
    cache_key = create_cache_key("audio_mp3", video_id)
    
    cached_result = audio_cache.get(cache_key)
    if cached_result:
        print(f"[AUDIO] Cache HIT for video_id: {video_id} (MP3)")
        cached_result['cached'] = True
        return cached_result, True
    
    print(f"[AUDIO] Cache MISS for video_id: {video_id} (MP3)")
    
    async def execute_audio_stream():
        executor = load_balancer.get_least_loaded_executor(audio_executors)
        loop = asyncio.get_event_loop()
        result = await loop.run_in_executor(executor, SearchHelper.get_audio_stream_url, video_id)
        
        audio_cache.set(cache_key, result)
        return result
    
    result = await request_deduplicator.get_or_execute(cache_key, execute_audio_stream)
    result['cached'] = False
    return result, False

async def cached_video_stream(video_id: str) -> Tuple[VideoStreamResponse, bool]:
    """Video stream with caching and deduplication"""
    cache_key = create_cache_key("video", video_id)
    
    cached_result = video_cache.get(cache_key)
    if cached_result:
        print(f"[VIDEO] Cache HIT for video_id: {video_id}")
        cached_result['cached'] = True
        return cached_result, True
    
    print(f"[VIDEO] Cache MISS for video_id: {video_id}")
    
    async def execute_video_stream():
        executor = load_balancer.get_least_loaded_executor(video_executors)
        loop = asyncio.get_event_loop()
        result = await loop.run_in_executor(executor, SearchHelper.get_video_stream_url, video_id)
        
        video_cache.set(cache_key, result)
        return result
    
    result = await request_deduplicator.get_or_execute(cache_key, execute_video_stream)
    result['cached'] = False
    return result, False

@app.get("/search", response_model=List[SearchResult])
async def search_music(
    q: str = Query(..., description="Search query for music"),
    limit: Optional[int] = Query(None, description="Limit number of results (unlimited by default)")
):
    """Search for music - OPTIMIZED with caching, deduplication, and load balancing"""
    if not q or len(q.strip()) < 2:
        raise HTTPException(status_code=400, detail="Query must be at least 2 characters")
    
    try:
        print(f"[SEARCH] Processing query: '{q}' with advanced optimizations")
        results, from_cache = await cached_search(q, limit)
        
        if not results:
            return []
        
        print(f"[SEARCH] Completed - returned {len(results)} results {'(cached)' if from_cache else '(fresh)'}")
        return results
        
    except Exception as e:
        print(f"[SEARCH] Error: {e}")
        raise HTTPException(status_code=500, detail="Search failed")

@app.get("/stream/{video_id}", response_model=StreamResponse)
async def get_stream(video_id: str):
    """Get MP3 audio streaming URL - GUARANTEED MP3 FORMAT ONLY"""
    if not video_id:
        raise HTTPException(status_code=400, detail="Video ID is required")
    
    try:
        print(f"[AUDIO] Processing video_id: {video_id} - ENFORCING MP3 FORMAT")
        result, from_cache = await cached_audio_stream(video_id)
        
        print(f"[AUDIO] Completed MP3 stream for video_id: {video_id} {'(cached)' if from_cache else '(fresh)'}")
        return result
        
    except HTTPException:
        raise
    except Exception as e:
        print(f"[AUDIO] Error: {e}")
        raise HTTPException(status_code=500, detail="Failed to get MP3 audio stream")

@app.get("/streamvideo/{video_id}", response_model=VideoStreamResponse)
async def get_video_stream(video_id: str):
    """Get highest quality video streaming URL - OPTIMIZED with caching, deduplication, and load balancing"""
    if not video_id:
        raise HTTPException(status_code=400, detail="Video ID is required")
    
    try:
        print(f"[VIDEO] Processing video_id: {video_id} with advanced optimizations")
        result, from_cache = await cached_video_stream(video_id)
        
        print(f"[VIDEO] Completed for video_id: {video_id} {'(cached)' if from_cache else '(fresh)'}")
        return result
        
    except HTTPException:
        raise
    except Exception as e:
        print(f"[VIDEO] Error: {e}")
    except Exception as e:
        print(f"[VIDEO] Error: {e}")
        raise HTTPException(status_code=500, detail="Failed to get video stream")

@app.get("/top-artists")
async def get_top_artists(limit: int = 100):
    """Get global top artists from Last.fm"""
    try:
        artists = lastfm_client.get_global_top_artists(limit=limit)
        return artists
    except Exception as e:
        print(f"[LASTFM] Error: {e}")
        raise HTTPException(status_code=500, detail="Failed to fetch top artists")

@app.get("/getartistssongs/{artist_name}")
def get_artists_songs(artist_name: str):
    """
    Return all songs by an artist, aligned with their albums,
    including release date, month, year, and thumbnail.
    """
    songs = itunes.get_all_official_songs_by_artist(artist_name)

    if not songs:
        raise HTTPException(
            status_code=404,
            detail=f"Artist '{artist_name}' not found or no songs available."
        )

    # Group songs by album with release info and thumbnail
    albums_dict = {}
    for song in songs:
        album = song["album_name"]
        song_info = {
            "song_name": song["song_name"],
            "release_date": song["release_date"],
            "release_month": song["release_month"],
            "release_year": song["release_year"],
            "thumbnail": song.get("thumbnail"),
            "preview_url": song.get("preview_url")
        }
        if album not in albums_dict:
            albums_dict[album] = []
        albums_dict[album].append(song_info)

    return {
        "artist": artist_name,
        "total_songs": len(songs),
        "albums": albums_dict
    }

@app.get("/health")
async def health_check():
    """Health check endpoint with performance metrics"""
    return {
        "status": "healthy", 
        "service": "Ultra High-Performance Music Streaming API with MP3-Only Audio",
        "audio_format": "MP3 ONLY (320kbps preferred)",
        "thread_pools": {
            "search_pools": len(search_executors),
            "audio_pools": len(audio_executors), 
            "video_pools": len(video_executors),
            "total_threads": 36
        },
        "cache_stats": {
            "search_cache": search_cache.stats(),
            "audio_cache": audio_cache.stats(),
            "video_cache": video_cache.stats()
        }
    }

@app.get("/stats")
async def performance_stats():
    """Get current performance statistics and metrics"""
    active_threads = {
        "search": sum(len(e._threads) if e._threads else 0 for e in search_executors),
        "audio": sum(len(e._threads) if e._threads else 0 for e in audio_executors),
        "video": sum(len(e._threads) if e._threads else 0 for e in video_executors)
    }
    
    return {
        "performance_optimization": "ULTRA ACTIVE with MP3-ONLY AUDIO",
        "audio_format_guarantee": "ALL /stream endpoints return MP3 format only",
        "architecture": {
            "search_endpoint": f"{len(search_executors)} pools × 4 threads = 12 total",
            "audio_stream_endpoint": f"{len(audio_executors)} pools × 4 threads = 12 total (MP3 ONLY)", 
            "video_stream_endpoint": f"{len(video_executors)} pools × 4 threads = 12 total",
            "total_worker_threads": 36
        },
        "active_threads": active_threads,
        "optimizations": [
            "Multiple thread pools per endpoint for load distribution",
            "Advanced LRU caching with TTL expiration",
            "Request deduplication to prevent duplicate processing",
            "Intelligent load balancing across thread pools",
            "Automatic cache cleanup and memory management",
            "Optimized timeouts for faster response times",
            "MP3-only audio format enforcement with FFmpeg post-processing",
            "Auto yt-dlp updates on startup and daily at midnight"
        ],
        "cache_performance": {
            "search_cache": {
                **search_cache.stats(),
                "ttl_minutes": 15,
                "description": "Search results cached for 15 minutes"
            },
            "audio_cache": {
                **audio_cache.stats(), 
                "ttl_minutes": 60,
                "description": "MP3 audio URLs cached for 60 minutes"
            },
            "video_cache": {
                **video_cache.stats(),
                "ttl_minutes": 45, 
                "description": "Video URLs cached for 45 minutes"
            }
        },
        "concurrent_performance": {
            "max_simultaneous_search": 12,
            "max_simultaneous_audio": 12,
            "max_simultaneous_video": 12,
            "request_deduplication": "Active - prevents duplicate processing",
            "load_balancing": "Active - distributes load across thread pools"
        }
    }

@app.post("/cache/clear")
async def clear_cache():
    """Clear all caches (admin endpoint)"""
    search_cache.clear()
    audio_cache.clear()
    video_cache.clear()
    return {
        "status": "success",
        "message": "All caches cleared successfully (including MP3 audio cache)",
        "timestamp": datetime.now().isoformat()
    }

@app.get("/cache/stats")
async def cache_statistics():
    """Get detailed cache statistics"""
    return {
        "search_cache": {
            **search_cache.stats(),
            "entries": len(search_cache.cache),
            "ttl_minutes": 15
        },
        "audio_cache": {
            **audio_cache.stats(),
            "entries": len(audio_cache.cache),
            "ttl_minutes": 60,
            "format": "MP3 ONLY"
        },
        "video_cache": {
            **video_cache.stats(),
            "entries": len(video_cache.cache),
            "ttl_minutes": 45
        },
        "total_cached_items": len(search_cache.cache) + len(audio_cache.cache) + len(video_cache.cache)
    }

@app.get("/performance/realtime")
async def realtime_performance():
    """Get real-time performance metrics"""
    return {
        "timestamp": datetime.now().isoformat(),
        "audio_format": "MP3 ONLY - ALL audio streams guaranteed to be MP3",
        "thread_utilization": {
            "search_pools": [
                {
                    "pool_id": i,
                    "active_threads": len(executor._threads) if executor._threads else 0,
                    "max_workers": executor._max_workers
                }
                for i, executor in enumerate(search_executors)
            ],
            "audio_pools": [
                {
                    "pool_id": i,
                    "active_threads": len(executor._threads) if executor._threads else 0,
                    "max_workers": executor._max_workers,
                    "format": "MP3 ONLY"
                }
                for i, executor in enumerate(audio_executors)
            ],
            "video_pools": [
                {
                    "pool_id": i,
                    "active_threads": len(executor._threads) if executor._threads else 0,
                    "max_workers": executor._max_workers
                }
                for i, executor in enumerate(video_executors)
            ]
        },
        "deduplication": {
            "active_requests": len(request_deduplicator.active_requests),
            "status": "preventing duplicate processing"
        }
    }

@app.get("/format/info")
async def format_info():
    """Get information about supported audio formats"""
    return {
        "audio_streaming": {
            "format": "MP3 ONLY",
            "quality": "320kbps preferred (varies based on source)",
            "codec": "MP3 (MPEG-1 Audio Layer III)",
            "compatibility": "Universal - works on all devices and platforms",
            "processing": "FFmpeg post-processing ensures MP3 format",
            "endpoint": "/stream/{video_id}"
        },
        "video_streaming": {
            "formats": "Various (MP4, WebM, etc.)",
            "quality": "Highest available (up to 4K)",
            "endpoint": "/streamvideo/{video_id}"
        },
        "guaranteed_features": [
            "All /stream endpoints return MP3 format only",
            "No other audio formats (WebM, M4A, etc.) will be returned",
            "FFmpeg post-processing converts to MP3 if needed",
            "High quality 320kbps preferred when available",
            "Auto yt-dlp updates on startup and daily at midnight"
        ]
    }

if __name__ == "__main__":
    print("🚀 ==> HanyaMusic Music Streaming API <==")
    print("🌐 API will be available at: http://localhost:8000")
    print("📚 Documentation at: http://localhost:8000/docs")
    print("📊 Performance Stats: http://localhost:8000/stats")
    print("📈 Real-time Metrics: http://localhost:8000/performance/realtime")
    print("🗄️  Cache Management: http://localhost:8000/cache/stats")
    print("🎵 Format Info: http://localhost:8000/format/info")
    print("🔄 Auto-Update: yt-dlp updates on startup + daily at midnight")
    
    uvicorn.run(
        "app:app",
        host="0.0.0.0",
        port=8000,
        reload=True,
        log_level="info",
        access_log=True,
        workers=1
    )

# Usage Examples:
# Search: /search?q=aespa 
# Audio: /stream/5oQVTnq-UKk  
# Video: /streamvideo/5oQVTnq-UKk 
# Stats: /stats
# Format: /format/info 

# To start with ngrok:
# ngrok http --domain=instinctually-monosodium-shawnda.ngrok-free.app 8000
# https://instinctually-monosodium-shawnda.ngrok-free.app/