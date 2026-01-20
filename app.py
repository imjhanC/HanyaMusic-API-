from fastapi import FastAPI, HTTPException, Query, BackgroundTasks, Request, File, UploadFile, Form
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
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.util import get_remote_address
from slowapi.errors import RateLimitExceeded
from slowapi.middleware import SlowAPIMiddleware
from sqlalchemy import create_engine, text
import os
from dotenv import load_dotenv
load_dotenv()
import base64

# Importing other classes
from AdvancedCache import AdvancedCache
from RedisCache import RedisCache
from RequestDeduplicator import RequestDeduplicator
from LoadBalancer import LoadBalancer
from SearchHelper import SearchHelper
from LastFM import LastFMClient
from ITunesAPI import ITunes
from SQLconn import get_db, engine
from sqlalchemy.orm import Session
from passlib.context import CryptContext
import bcrypt as bcrypt_lib
from fastapi import Depends, status, Security
from fastapi.security import OAuth2PasswordBearer, OAuth2PasswordRequestForm
from jose import JWTError, jwt
from datetime import timedelta

limiter = Limiter(key_func=get_remote_address, default_limits=["60/minute"])
app = FastAPI(title="HanyaMusic Music Streaming API", version="3.0.0")
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)

# For POSTGRESQL connection
# For POSTGRESQL connection
# engine imported from SQLconn

# Enable CORS for React Native
app.add_middleware(SlowAPIMiddleware)
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

class UserRegister(BaseModel):
    username: str
    email: str
    password: str
    display_name: Optional[str] = None
    avatar_url: Optional[str] = None

class UserResponse(BaseModel):
    id: int
    username: str
    email: str
    display_name: Optional[str]
    avatar_url: Optional[str]
    is_verified: bool
    is_active: bool
    role: str
    last_login: Optional[datetime]
    created_at: datetime
    updated_at: datetime
    
    class Config:
        from_attributes = True

    class Config:
        from_attributes = True

class Token(BaseModel):
    access_token: str
    token_type: str

class TokenData(BaseModel):
    username: Optional[str] = None


# Password hashing functions using bcrypt directly
def hash_password(password: str) -> str:
    """Hash a password using bcrypt"""
    # Truncate to 72 bytes for bcrypt compatibility
    password_bytes = password.encode('utf-8')[:72]
    salt = bcrypt_lib.gensalt()
    hashed = bcrypt_lib.hashpw(password_bytes, salt)
    return hashed.decode('utf-8')

def verify_password(plain_password: str, hashed_password: str) -> bool:
    """Verify a password against a hash"""
    # Truncate to 72 bytes for bcrypt compatibility
    password_bytes = plain_password.encode('utf-8')[:72]
    return bcrypt_lib.checkpw(password_bytes, hashed_password.encode('utf-8'))
oauth2_scheme = OAuth2PasswordBearer(tokenUrl="token")

# JWT Configuration
SECRET_KEY = "HANYAMUSIC_SECRET_KEY_PLEASE_CHANGE_IN_PRODUCTION" # TODO: Move to .env
ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_MINUTES = 30

def create_access_token(data: dict, expires_delta: Optional[timedelta] = None):
    to_encode = data.copy()
    if expires_delta:
        expire = datetime.utcnow() + expires_delta
    else:
        expire = datetime.utcnow() + timedelta(minutes=15)
    to_encode.update({"exp": expire})
    encoded_jwt = jwt.encode(to_encode, SECRET_KEY, algorithm=ALGORITHM)
    return encoded_jwt

async def get_current_user(token: str = Depends(oauth2_scheme), db: Session = Depends(get_db)):
    credentials_exception = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Could not validate credentials",
        headers={"WWW-Authenticate": "Bearer"},
    )
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        username: str = payload.get("sub")
        if username is None:
            raise credentials_exception
        token_data = TokenData(username=username)
    except JWTError:
        raise credentials_exception
    
    user = db.execute(text("SELECT * FROM users WHERE username = :username"), {"username": token_data.username}).fetchone()
    if user is None:
        raise credentials_exception
    return user

# Global caches for each endpoint
# Global caches - Now using REDIS for persistence and shared state
# We use different prefixes to namespace the keys
search_cache = RedisCache(prefix="hanya:search:")
audio_cache = RedisCache(prefix="hanya:audio:")
video_cache = RedisCache(prefix="hanya:video:")

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
            print("[CACHE] Redis handles expiration automatically")
            
            gc.collect()
            print("[GC] Garbage collection completed")
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
async def root(request: Request):
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
        
        search_cache.set(cache_key, results, ttl_minutes=15)
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
        
        audio_cache.set(cache_key, result, ttl_minutes=60)
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
        
        video_cache.set(cache_key, result, ttl_minutes=45)
        return result
    
    result = await request_deduplicator.get_or_execute(cache_key, execute_video_stream)
    result['cached'] = False
    return result, False

## Endpoint for registering account 
@app.post("/register", status_code=status.HTTP_201_CREATED)
async def register(
    username: str = Form(...),
    email: str = Form(...),
    password: str = Form(...),
    display_name: Optional[str] = Form(None),
    avatar: Optional[UploadFile] = File(None),
    db: Session = Depends(get_db)
):
    """Register a new user with optional avatar image login"""
    # Validate password length (bcrypt has 72 byte limit)
    if len(password) < 8:
        raise HTTPException(status_code=400, detail="Password must be at least 8 characters")
    if len(password) > 100:
        raise HTTPException(status_code=400, detail="Password is too long (max 100 characters)")
    
    # Check if user exists
    existing_user = db.execute(text("SELECT id FROM users WHERE username = :username OR email = :email"), 
                               {"username": username, "email": email}).fetchone()
    if existing_user:
        raise HTTPException(status_code=400, detail="Username or email already registered")
    
    # Hash password
    hashed_password = hash_password(password)

    # Handle Avatar
    avatar_url_value = None
    if avatar:
        if avatar.content_type not in ["image/jpeg", "image/png", "image/gif", "image/jpg"]:
             raise HTTPException(status_code=400, detail="Invalid image format")
        
        file_content = await avatar.read()
        encoded_string = base64.b64encode(file_content).decode("utf-8")
        avatar_url_value = f"data:{avatar.content_type};base64,{encoded_string}"
    
    # Insert user
    try:
        query = text("""
            INSERT INTO users (username, email, password_hash, display_name, avatar_url)
            VALUES (:username, :email, :password_hash, :display_name, :avatar_url)
            RETURNING id, username, email, created_at
        """)
        result = db.execute(query, {
            "username": username,
            "email": email,
            "password_hash": hashed_password,
            "display_name": display_name,
            "avatar_url": avatar_url_value
        })
        new_user = result.fetchone()
        db.commit()
        
        # Generate Access Token for the new user
        access_token_expires = timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES)
        access_token = create_access_token(
            data={"sub": new_user.username}, expires_delta=access_token_expires
        )
        
        return {
            "id": new_user.id,
            "username": new_user.username,
            "email": new_user.email,
            "created_at": new_user.created_at,
            "message": "User registered successfully",
            "access_token": access_token,
            "token_type": "bearer"
        }
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=500, detail=f"Registration failed: {str(e)}")

@app.post("/token", response_model=Token)
async def login_for_access_token(form_data: OAuth2PasswordRequestForm = Depends(), db: Session = Depends(get_db)):
    """Login to get access token (checks both username and email)"""
    # Check if username OR email matches
    user = db.execute(
        text("SELECT * FROM users WHERE username = :identifier OR email = :identifier"), 
        {"identifier": form_data.username}
    ).fetchone()
    
    
    if not user or not verify_password(form_data.password, user.password_hash):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect username or password",
            headers={"WWW-Authenticate": "Bearer"},
        )
    
    access_token_expires = timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES)
    access_token = create_access_token(
        data={"sub": user.username}, expires_delta=access_token_expires
    )
    return {"access_token": access_token, "token_type": "bearer"}

@app.get("/users/me", response_model=UserResponse)
async def read_users_me(current_user: UserResponse = Depends(get_current_user)):
    """Get current logged in user details"""
    return current_user

@app.get("/users/{user_id}", response_model=UserResponse)
def get_user_details(user_id: int, current_user: UserResponse = Depends(get_current_user), db: Session = Depends(get_db)):
    """Get user details by ID - Requires Auth"""
    try:
        user = db.execute(
            text("""
                SELECT id, username, email, display_name, avatar_url, 
                       is_verified, is_active, role, last_login, 
                       created_at, updated_at
                FROM users 
                WHERE id = :user_id
            """), 
            {"user_id": user_id}
        ).fetchone()
        
        if not user:
            raise HTTPException(status_code=404, detail="User not found")
            
        return user
    except Exception as e:
        print(f"[USER_DETAIL] Error: {e}")
        raise HTTPException(status_code=500, detail="Failed to fetch user details")

@app.get("/search", response_model=List[SearchResult])
async def search_music(
    request: Request,
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

@app.get("/search/exact", response_model=StreamResponse)
async def search_exact_music(
    request: Request,
    song_title: str = Query(..., description="Song title"),
    artist: str = Query(..., description="Artist name")
):
    """
    Search specifically for ONE best match exact song and return its AUDIO STREAM directly.
    Requires separate song_title and artist parameters.
    """
    if not song_title or len(song_title.strip()) < 2:
        raise HTTPException(status_code=400, detail="Song title must be at least 2 characters")
    if not artist or len(artist.strip()) < 2:
        raise HTTPException(status_code=400, detail="Artist name must be at least 2 characters")
    
    try:
        # Step 1: Find the video ID
        # Combine song title and artist for search
        combined_query = f"{song_title} {artist}"
        enhanced_q = f"{combined_query} official audio"
        print(f"[SEARCH-EXACT] Processing: '{song_title}' by '{artist}' -> '{enhanced_q}'")
        
        # Search with limit=1 because we only want the BEST match
        results, from_cache = await cached_search(enhanced_q, limit=1)
        
        if not results:
            print(f"[SEARCH-EXACT] strict search empty, retrying with just 'audio'")
            fallback_q = f"{q} audio"
            results, from_cache = await cached_search(fallback_q, limit=1)
        
        if not results:
             raise HTTPException(status_code=404, detail="No exact match found")
        
        best_match = results[0]
        # database results are dicts internally until FastAPI serializes them
        video_id = best_match['videoId']
        print(f"[SEARCH-EXACT] Found best match: {best_match.get('title')} ({video_id})")

        # Step 2: Get the stream for this video ID
        # We reuse the existing cached_audio_stream function
        stream_result, stream_from_cache = await cached_audio_stream(video_id)
        
        print(f"[SEARCH-EXACT] Retrieved stream for {video_id} {'(cached)' if stream_from_cache else '(fresh)'}")
        return stream_result
        
    except HTTPException:
        raise
    except Exception as e:
        print(f"[SEARCH-EXACT] Error: {e}")
        raise HTTPException(status_code=500, detail="Exact search failed")

@app.get("/stream/{video_id}", response_model=StreamResponse)
async def get_stream(request: Request, video_id: str):
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
async def get_video_stream(request: Request, video_id: str):
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
async def get_top_artists(request: Request, limit: int = 100):
    """Get global top artists from Last.fm"""
    try:
        artists = lastfm_client.get_global_top_artists(limit=limit)
        return artists
    except Exception as e:
        print(f"[LASTFM] Error: {e}")
        raise HTTPException(status_code=500, detail="Failed to fetch top artists")

@app.get("/getartistssongs/{artist_name}")
def get_artists_songs(request: Request, artist_name: str):
    """
    Return all songs by an artist, aligned with their albums,
    including release date, month, year, thumbnail, and sample thumbnails.
    """
    result = itunes_client.get_artist_songs_with_sample_thumbnails(artist_name)

    if not result["total_songs"]:
        raise HTTPException(
            status_code=404,
            detail=f"Artist '{artist_name}' not found or no songs available."
        )

    return result

@app.get("/getrelatedartists/{song_name}")
async def get_related_artists(request: Request, song_name: str):
    """
    Get related artists for a song and fetch their images from Last.fm concurrently.
    """
    try:
        # Using a thread pool for the synchronous iTunes call
        loop = asyncio.get_event_loop()
        related_artists = await loop.run_in_executor(
            None, itunes_client.get_top_5_artists_for_song, song_name
        )
        
        if not related_artists:
            return {"song": song_name, "related_artists": []}
            
        #Fetch images for all artists concurrently
        async def fetch_artist_data(artist_name):
            image = await loop.run_in_executor(
                None, lastfm_client.get_artist_image, artist_name
            )
            return {
                "artist_name": artist_name,
                "image": image
            }

        # Run all image fetches in parallel
        tasks = [fetch_artist_data(artist) for artist in related_artists]
        results = await asyncio.gather(*tasks)

        return {
            "song": song_name,
            "related_artists": results
        }

    except Exception as e:
        print(f"[RELATED-ARTISTS] Error: {e}")
        raise HTTPException(status_code=500, detail="Failed to fetch related artists")

# https://gist.github.com/daFish/5990634 refer this 
@app.get("/topglobalartists")
def top_global_artists(request: Request, limit: int = 100):
    result = itunes_client.get_top_global_artists_with_thumbnails(limit=limit)
    
    if not result["artists"]:
        raise HTTPException(
            status_code=404,
            detail="Unable to fetch top global artists."
        )
    
    return result

@app.get("/topglobalsongs")
def top_global_songs(request: Request, limit: int = 100):
    result = itunes_client.get_top_global_songs_with_thumbnails(limit=limit)
    
    if not result["songs"]:
        raise HTTPException(
            status_code=404,
            detail="Unable to fetch top global songs."
        )
    
    return result

@app.get("/topcountrysongs/{country_code}")
def top_country_songs(request: Request, country_code: str, limit: int = 100):
    result = itunes_client.get_top_country_songs_with_thumbnails(country_code=country_code, limit=limit)
    
    if not result["songs"]:
        raise HTTPException(
            status_code=404,
            detail=f"Unable to fetch top songs for country '{country_code}'."
        )
    
    return result

@app.get("/health")
async def health_check(request: Request):
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
async def performance_stats(request: Request):
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
async def clear_cache(request: Request):
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
async def cache_statistics(request: Request):
    """Get detailed cache statistics"""
    return {
        "search_cache": {
            **search_cache.stats(),
            "ttl_minutes": 15
        },
        "audio_cache": {
            **audio_cache.stats(),
            "ttl_minutes": 60,
            "format": "MP3 ONLY"
        },
        "video_cache": {
            **video_cache.stats(),
            "ttl_minutes": 45
        },
        "total_cached_items": "Managed by Redis"
    }

# PostGRESQL test endpoint 
@app.get("/test-db")
def test_db():
    try:
        with engine.connect() as conn:
            result = conn.execute(text("SELECT 1"))
            return {
                "status": "success",
                "message": "FastAPI connected to PostgreSQL!",
                "result": result.scalar()
            }
    except Exception as e:
        return {
            "status": "error",
            "message": str(e)
        }
    
@app.get("/performance/realtime")
async def realtime_performance(request: Request):
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
async def format_info(request: Request):
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