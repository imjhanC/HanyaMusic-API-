from fastapi import FastAPI, HTTPException, Query, BackgroundTasks, Request, File, UploadFile, Form
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
import uvicorn
from typing import List, Dict, Optional, Tuple, AsyncGenerator
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

# Enable CORS for React Native
app.add_middleware(SlowAPIMiddleware)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ─── Pydantic Models ──────────────────────────────────────────────────────────

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

class VideoStreamMobileResponse(BaseModel):
    """
    Returned by /search/exactwithMVMobile and /streamvideo/mobile/{video_id}.

    stream_url  : the /video/proxy/{video_id} endpoint — point your player here.
    video_url   : raw YouTube video-only URL  (useful for debugging / fallback).
    audio_url   : raw YouTube audio-only URL  (useful for debugging / fallback).
    """
    stream_url: str          # ← the seekable streaming endpoint
    video_url: str           # raw separate video stream
    audio_url: str           # raw separate audio stream
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

class Token(BaseModel):
    access_token: str
    token_type: str

class TokenData(BaseModel):
    username: Optional[str] = None

# ─── Auth Helpers ─────────────────────────────────────────────────────────────

def hash_password(password: str) -> str:
    password_bytes = password.encode('utf-8')[:72]
    salt = bcrypt_lib.gensalt()
    hashed = bcrypt_lib.hashpw(password_bytes, salt)
    return hashed.decode('utf-8')

def verify_password(plain_password: str, hashed_password: str) -> bool:
    password_bytes = plain_password.encode('utf-8')[:72]
    return bcrypt_lib.checkpw(password_bytes, hashed_password.encode('utf-8'))

oauth2_scheme = OAuth2PasswordBearer(tokenUrl="token")

SECRET_KEY = "HANYAMUSIC_SECRET_KEY_PLEASE_CHANGE_IN_PRODUCTION"  # TODO: Move to .env
ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_MINUTES = 30

def create_access_token(data: dict, expires_delta: Optional[timedelta] = None):
    to_encode = data.copy()
    expire = datetime.utcnow() + (expires_delta or timedelta(minutes=15))
    to_encode.update({"exp": expire})
    return jwt.encode(to_encode, SECRET_KEY, algorithm=ALGORITHM)

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

    user = db.execute(
        text("SELECT * FROM users WHERE username = :username"),
        {"username": token_data.username}
    ).fetchone()
    if user is None:
        raise credentials_exception
    return user

# ─── Caches & Infrastructure ─────────────────────────────────────────────────

search_cache       = RedisCache(prefix="hanya:search:")
audio_cache        = RedisCache(prefix="hanya:audio:")
video_cache        = RedisCache(prefix="hanya:video:")
video_mobile_cache = RedisCache(prefix="hanya:video_mobile:")

request_deduplicator = RequestDeduplicator()
load_balancer        = LoadBalancer()
lastfm_client        = LastFMClient()
itunes_client        = ITunes()

def create_cache_key(func_name: str, *args, **kwargs) -> str:
    key_data = f"{func_name}:{str(args)}:{str(sorted(kwargs.items()))}"
    return hashlib.md5(key_data.encode()).hexdigest()

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

# ─── Merged-video dir ────────────────────────────────────────────────────────
_MERGED_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'merged_videos')
os.makedirs(_MERGED_DIR, exist_ok=True)
app.mount("/merged", StaticFiles(directory=_MERGED_DIR), name="merged_videos")

# ─── In-progress merge deduplication ─────────────────────────────────────────
# Prevents multiple ffmpeg processes for the same video_id when concurrent
# requests arrive before the first merge finishes.
_merge_in_progress: Dict[str, asyncio.Event] = {}
_merge_lock = asyncio.Lock()

# ─── Background tasks ────────────────────────────────────────────────────────

async def run_yt_dlp_update():
    print("[YT-DLP] Running yt-dlp update...")
    try:
        result = subprocess.run(
            ["python", "-m", "pip", "install", "-U", "yt-dlp"],
            check=True, capture_output=True, text=True
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
    print("[STARTUP] Running initial yt-dlp update...")
    await run_yt_dlp_update()
    while True:
        now = datetime.now()
        target = datetime.combine(now.date(), time(0, 0))
        if now >= target:
            target = target + timedelta(days=1)
        wait_seconds = (target - now).total_seconds()
        print(f"[CRON] Next yt-dlp update in {wait_seconds/3600:.2f} hours")
        await asyncio.sleep(wait_seconds)
        await run_yt_dlp_update()

async def periodic_cache_cleanup():
    while True:
        await asyncio.sleep(300)
        try:
            print("[CACHE] Redis handles expiration automatically")
            gc.collect()
            print("[GC] Garbage collection completed")
        except Exception as e:
            print(f"[CACHE] Cleanup error: {e}")

async def periodic_merged_cleanup():
    """Delete merged MP4 files older than 2 hours to save disk space."""
    import time as _time
    while True:
        await asyncio.sleep(3600)
        try:
            now = _time.time()
            removed = 0
            for fname in os.listdir(_MERGED_DIR):
                fpath = os.path.join(_MERGED_DIR, fname)
                if os.path.isfile(fpath) and (now - os.path.getmtime(fpath)) > 7200:
                    os.remove(fpath)
                    removed += 1
            if removed:
                print(f"[MERGE-CLEANUP] Removed {removed} old merged MP4 file(s)")
        except Exception as e:
            print(f"[MERGE-CLEANUP] Error: {e}")

@app.on_event("startup")
async def startup_event():
    asyncio.create_task(periodic_cache_cleanup())
    asyncio.create_task(update_yt_dlp_daily())
    asyncio.create_task(periodic_merged_cleanup())
    print("🚀 High-Performance API started with MP3-only audio + seekable MP4 streaming!")

@app.on_event("shutdown")
async def shutdown_event():
    print("Shutting down thread pools...")
    for executor in search_executors + audio_executors + video_executors:
        executor.shutdown(wait=True)
    print("All thread pools shut down successfully")

# ─── Cached helpers ───────────────────────────────────────────────────────────

async def cached_search(q: str, limit: Optional[int] = None) -> Tuple[List[SearchResult], bool]:
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


async def cached_audio_stream(video_id: str) -> Tuple[Dict, bool]:
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
        result['headers'] = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
            'Referer': 'https://www.youtube.com/',
            'Origin': 'https://www.youtube.com',
            'Accept': '*/*',
            'Accept-Language': 'en-US,en;q=0.9',
            'Accept-Encoding': 'identity',
            'DNT': '1',
        }
        audio_cache.set(cache_key, result, ttl_minutes=60)
        return result

    result = await request_deduplicator.get_or_execute(cache_key, execute_audio_stream)
    result['cached'] = False
    return result, False


async def cached_video_stream(video_id: str) -> Tuple[VideoStreamResponse, bool]:
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


async def cached_video_mobile_stream(
    video_id: str,
    base_url: str = ""
) -> Tuple[VideoStreamMobileResponse, bool]:
    """
    Cache stores only the raw video_url + audio_url from yt-dlp.
    Actual merging is deferred to /video/proxy/{video_id}.
    """
    cache_key = create_cache_key("video_mobile_v3", video_id)

    cached_result = video_mobile_cache.get(cache_key)
    if cached_result:
        print(f"[VIDEO-MOBILE] Cache HIT for video_id: {video_id}")
        cached_result['cached'] = True
        return cached_result, True

    print(f"[VIDEO-MOBILE] Cache MISS for video_id: {video_id}")

    async def execute_video_mobile_stream():
        executor = load_balancer.get_least_loaded_executor(video_executors)
        loop = asyncio.get_event_loop()
        result = await loop.run_in_executor(
            executor,
            SearchHelper.get_mobile_video_stream_url,
            video_id,
        )
        result['stream_url'] = f"{base_url}/video/proxy/{video_id}"
        video_mobile_cache.set(cache_key, result, ttl_minutes=45)
        return result

    result = await request_deduplicator.get_or_execute(cache_key, execute_video_mobile_stream)
    result['cached'] = False
    return result, False


# ─── Fast fragmented-MP4 streaming endpoint (non-seekable, for legacy use) ───

@app.get("/video/stream/{video_id}")
async def stream_mobile_video(video_id: str, request: Request):
    """
    Streams a merged MP4 (video + audio) directly to the client via ffmpeg pipe.
    NOTE: This endpoint does NOT support seeking. For a seekable player, use
    /video/proxy/{video_id} instead — it serves a fully merged file with 206
    range support once the merge completes.
    """
    cache_key = create_cache_key("video_mobile_v3", video_id)
    cached = video_mobile_cache.get(cache_key)

    if cached:
        video_url = cached['video_url']
        audio_url = cached['audio_url']
        print(f"[STREAM] Using cached URLs for {video_id}")
    else:
        print(f"[STREAM] No cached URLs for {video_id}, extracting via yt-dlp...")
        executor = load_balancer.get_least_loaded_executor(video_executors)
        loop = asyncio.get_event_loop()
        data = await loop.run_in_executor(
            executor,
            SearchHelper.get_mobile_video_stream_url,
            video_id,
        )
        video_url = data['video_url']
        audio_url = data['audio_url']
        base_url  = str(request.base_url).rstrip('/')
        data['stream_url'] = f"{base_url}/video/proxy/{video_id}"
        video_mobile_cache.set(cache_key, data, ttl_minutes=45)

    proc = SearchHelper.merge_video_audio_ffmpeg_stream(video_url, audio_url)

    async def generate() -> AsyncGenerator[bytes, None]:
        loop = asyncio.get_event_loop()
        try:
            while True:
                chunk: bytes = await loop.run_in_executor(None, proc.stdout.read, 65536)
                if not chunk:
                    break
                yield chunk
        except asyncio.CancelledError:
            print(f"[STREAM] Client disconnected for {video_id}, killing ffmpeg pid={proc.pid}")
            proc.kill()
            raise
        finally:
            try:
                proc.stdout.close()
                stderr_output = proc.stderr.read()
                if stderr_output:
                    print(f"[STREAM][ffmpeg stderr] {stderr_output.decode(errors='replace').strip()}")
                proc.stderr.close()
            except Exception:
                pass
            proc.wait()
            print(f"[STREAM] ffmpeg pid={proc.pid} exited for {video_id}")

    return StreamingResponse(
        generate(),
        media_type="video/mp4",
        headers={
            "Content-Disposition": f"inline; filename={video_id}.mp4",
            "X-Content-Type-Options": "nosniff",
            "Cache-Control": "no-cache",
        },
    )


# ─── Seekable proxy endpoint — merges to disk, serves with 206 range support ─

@app.get("/video/proxy/{video_id}")
async def proxy_video(video_id: str, request: Request):
    """
    Serves a fully merged MP4 with HTTP 206 range support so players can seek.

    First request:
      • Waits for ffmpeg to merge video+audio to disk (~10–30s depending on
        video length and bandwidth).
      • Concurrent requests for the same video_id wait on an asyncio.Event
        rather than spawning duplicate ffmpeg processes.
      • Falls back to a non-seekable pipe stream if the merge fails.

    Subsequent requests:
      • The merged file is served immediately from disk with full 206 range
        support — seeking, timeline scrubbing, and duration display all work.

    The periodic_merged_cleanup task removes files older than 2 hours so disk
    usage stays bounded.
    """
    import aiofiles

    # ── 1. Resolve stream URLs (cache or fresh yt-dlp extraction) ────────────
    cache_key = create_cache_key("video_mobile_v3", video_id)
    cached = video_mobile_cache.get(cache_key)

    if not cached:
        executor = load_balancer.get_least_loaded_executor(video_executors)
        loop = asyncio.get_event_loop()
        data = await loop.run_in_executor(
            executor, SearchHelper.get_mobile_video_stream_url, video_id
        )
        base_url = str(request.base_url).rstrip('/')
        data['stream_url'] = f"{base_url}/video/proxy/{video_id}"
        video_mobile_cache.set(cache_key, data, ttl_minutes=45)
        cached = data

    video_url   = cached['video_url']
    audio_url   = cached['audio_url']
    merged_path = os.path.join(_MERGED_DIR, f"{video_id}.mp4")

    # ── 2. Helper: serve the merged file with full 206 range support ──────────
    async def serve_merged_file():
        file_size    = os.path.getsize(merged_path)
        range_header = request.headers.get("range")
        start, end   = 0, file_size - 1

        if range_header and range_header.startswith("bytes="):
            try:
                parts = range_header[6:].split("-")
                start = int(parts[0]) if parts[0] else 0
                end   = int(parts[1]) if len(parts) > 1 and parts[1] else file_size - 1
            except ValueError:
                pass

        end        = min(end, file_size - 1)
        chunk_size = end - start + 1

        async def file_generator():
            async with aiofiles.open(merged_path, "rb") as f:
                await f.seek(start)
                remaining = chunk_size
                while remaining > 0:
                    data = await f.read(min(65536, remaining))
                    if not data:
                        break
                    yield data
                    remaining -= len(data)

        return StreamingResponse(
            file_generator(),
            status_code=206 if range_header else 200,
            media_type="video/mp4",
            headers={
                "Content-Range":   f"bytes {start}-{end}/{file_size}",
                "Content-Length":  str(chunk_size),
                "Accept-Ranges":   "bytes",
                "Cache-Control":   "public, max-age=3600",
                "Content-Disposition": f"inline; filename={video_id}.mp4",
            },
        )

    # ── 3. Fast path: merged file already on disk ─────────────────────────────
    if os.path.exists(merged_path) and os.path.getsize(merged_path) > 1_000_000:
        print(f"[PROXY] Serving cached merged file for {video_id}")
        return await serve_merged_file()

    # ── 4. Slow path: need to merge — deduplicate concurrent requests ─────────
    #
    # _merge_in_progress maps video_id → asyncio.Event.
    # The first request creates the Event and does the merge.
    # All subsequent concurrent requests await the same Event.
    #
    async with _merge_lock:
        if video_id in _merge_in_progress:
            # Another coroutine is already merging this video — wait for it.
            merge_event = _merge_in_progress[video_id]
            i_am_merger = False
        else:
            # We are first — create the event and take ownership of the merge.
            merge_event = asyncio.Event()
            _merge_in_progress[video_id] = merge_event
            i_am_merger = True

    if not i_am_merger:
        # ── 4a. Wait for the in-progress merge to finish ──────────────────
        print(f"[PROXY] Waiting for in-progress merge for {video_id}...")
        try:
            await asyncio.wait_for(merge_event.wait(), timeout=300)
        except asyncio.TimeoutError:
            raise HTTPException(status_code=504, detail="Video merge timed out")

        if os.path.exists(merged_path) and os.path.getsize(merged_path) > 1_000_000:
            return await serve_merged_file()
        else:
            raise HTTPException(
                status_code=500,
                detail="Video merge failed. Please try again."
            )

    # ── 4b. We are the merger — run ffmpeg in a thread pool ──────────────────
    print(f"[PROXY] Starting foreground merge for {video_id}...")
    merge_succeeded = False
    try:
        executor = load_balancer.get_least_loaded_executor(video_executors)
        loop = asyncio.get_event_loop()
        await loop.run_in_executor(
            executor,
            lambda: SearchHelper.merge_video_audio_ffmpeg_to_path(
                video_url, audio_url, merged_path
            )
        )
        merge_succeeded = True
        print(
            f"[PROXY] Merge complete for {video_id} "
            f"({os.path.getsize(merged_path):,} bytes)"
        )
    except Exception as e:
        print(f"[PROXY] Merge failed for {video_id}: {e}")
    finally:
        # Always signal waiting coroutines and remove from the in-progress map
        merge_event.set()
        async with _merge_lock:
            _merge_in_progress.pop(video_id, None)

    if merge_succeeded:
        return await serve_merged_file()

    # ── 4c. Merge failed — fall back to non-seekable pipe stream ─────────────
    print(f"[PROXY] Falling back to pipe stream for {video_id} (seeking will not work)")
    proc = SearchHelper.merge_video_audio_ffmpeg_stream(video_url, audio_url)

    async def generate_fallback() -> AsyncGenerator[bytes, None]:
        loop = asyncio.get_event_loop()
        try:
            while True:
                chunk = await loop.run_in_executor(None, proc.stdout.read, 65536)
                if not chunk:
                    break
                yield chunk
        except asyncio.CancelledError:
            proc.kill()
            raise
        finally:
            try:
                proc.stdout.close()
                proc.stderr.read()
                proc.stderr.close()
            except Exception:
                pass
            proc.wait()

    return StreamingResponse(
        generate_fallback(),
        media_type="video/mp4",
        headers={
            "Content-Disposition": f"inline; filename={video_id}.mp4",
            "Cache-Control": "no-cache",
        },
    )


# ─── Auth endpoints ───────────────────────────────────────────────────────────

@app.post("/register", status_code=status.HTTP_201_CREATED)
async def register(
    username: str = Form(...),
    email: str = Form(...),
    password: str = Form(...),
    display_name: Optional[str] = Form(None),
    avatar: Optional[UploadFile] = File(None),
    db: Session = Depends(get_db)
):
    if len(password) < 8:
        raise HTTPException(status_code=400, detail="Password must be at least 8 characters")
    if len(password) > 100:
        raise HTTPException(status_code=400, detail="Password is too long (max 100 characters)")

    existing_user = db.execute(
        text("SELECT id FROM users WHERE username = :username OR email = :email"),
        {"username": username, "email": email}
    ).fetchone()
    if existing_user:
        raise HTTPException(status_code=400, detail="Username or email already registered")

    hashed_password = hash_password(password)

    avatar_url_value = None
    if avatar:
        if avatar.content_type not in ["image/jpeg", "image/png", "image/gif", "image/jpg"]:
            raise HTTPException(status_code=400, detail="Invalid image format")
        file_content = await avatar.read()
        encoded_string = base64.b64encode(file_content).decode("utf-8")
        avatar_url_value = f"data:{avatar.content_type};base64,{encoded_string}"

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

        access_token = create_access_token(
            data={"sub": new_user.username},
            expires_delta=timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES)
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
async def login_for_access_token(
    form_data: OAuth2PasswordRequestForm = Depends(),
    db: Session = Depends(get_db)
):
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

    access_token = create_access_token(
        data={"sub": user.username},
        expires_delta=timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES)
    )
    return {"access_token": access_token, "token_type": "bearer"}


@app.get("/users/me", response_model=UserResponse)
async def read_users_me(current_user: UserResponse = Depends(get_current_user)):
    return current_user


@app.get("/users/{user_id}", response_model=UserResponse)
def get_user_details(
    user_id: int,
    current_user: UserResponse = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    try:
        user = db.execute(
            text("""
                SELECT id, username, email, display_name, avatar_url,
                       is_verified, is_active, role, last_login,
                       created_at, updated_at
                FROM users WHERE id = :user_id
            """),
            {"user_id": user_id}
        ).fetchone()
        if not user:
            raise HTTPException(status_code=404, detail="User not found")
        return user
    except Exception as e:
        print(f"[USER_DETAIL] Error: {e}")
        raise HTTPException(status_code=500, detail="Failed to fetch user details")


# ─── Music endpoints ──────────────────────────────────────────────────────────

@app.get("/")
async def root(request: Request):
    return {
        "message": "Ultra High-Performance Music Streaming API with MP3-Only Audio + Seekable MP4 Video!",
        "performance": {
            "search_threads": 12,
            "audio_stream_threads": 12,
            "video_stream_threads": 12,
            "total_threads": 36,
            "audio_format": "MP3 ONLY (320kbps preferred)",
            "video_mobile": "Seekable MP4 via disk merge + 206 range support",
            "features": [
                "Advanced caching system",
                "Request deduplication",
                "Load balancing",
                "Multiple thread pools per endpoint",
                "MP3-only audio streaming",
                "Seekable MP4 mobile video streaming (206 range support)",
                "Concurrent merge deduplication (no duplicate ffmpeg processes)",
                "Auto yt-dlp updates (startup + daily at midnight)"
            ]
        }
    }


@app.get("/search", response_model=List[SearchResult])
async def search_music(
    request: Request,
    q: str = Query(..., description="Search query for music"),
    limit: Optional[int] = Query(None, description="Limit number of results")
):
    if not q or len(q.strip()) < 2:
        raise HTTPException(status_code=400, detail="Query must be at least 2 characters")
    try:
        results, from_cache = await cached_search(q, limit)
        print(f"[SEARCH] {len(results)} results {'(cached)' if from_cache else '(fresh)'}")
        return results or []
    except Exception as e:
        print(f"[SEARCH] Error: {e}")
        raise HTTPException(status_code=500, detail="Search failed")


@app.get("/search/exact", response_model=StreamResponse)
async def search_exact_music(
    request: Request,
    song_title: str = Query(...),
    artist: str = Query(...)
):
    if not song_title or len(song_title.strip()) < 2:
        raise HTTPException(status_code=400, detail="Song title must be at least 2 characters")
    if not artist or len(artist.strip()) < 2:
        raise HTTPException(status_code=400, detail="Artist name must be at least 2 characters")

    try:
        combined_query = f"{song_title} {artist}"
        results, _ = await cached_search(f"{combined_query} official audio", limit=1)
        if not results:
            results, _ = await cached_search(f"{combined_query} audio", limit=1)
        if not results:
            raise HTTPException(status_code=404, detail="No exact match found")

        video_id = results[0]['videoId']
        stream_result, stream_from_cache = await cached_audio_stream(video_id)
        return stream_result

    except HTTPException:
        raise
    except Exception as e:
        print(f"[SEARCH-EXACT] Error: {e}")
        raise HTTPException(status_code=500, detail="Exact search failed")


@app.get("/search/exactwithMV", response_model=VideoStreamResponse)
async def search_exact_music_with_mv(
    request: Request,
    song_title: str = Query(...),
    artist: str = Query(...)
):
    if not song_title or len(song_title.strip()) < 2:
        raise HTTPException(status_code=400, detail="Song title must be at least 2 characters")
    if not artist or len(artist.strip()) < 2:
        raise HTTPException(status_code=400, detail="Artist name must be at least 2 characters")

    try:
        combined_query = f"{song_title} {artist}"
        search_terms = [
            f"{combined_query} official music video",
            f"{combined_query} music video",
            f"{combined_query} mv",
            f"{combined_query} color coded",
            f"{combined_query} visualizer",
            f"{combined_query} lyrics video",
            f"{combined_query} official video",
            f"{combined_query} audio"
        ]

        video_id = None
        for search_term in search_terms:
            results, _ = await cached_search(search_term, limit=3)
            if results:
                video_id = results[0]['videoId']
                print(f"[SEARCH-EXACTMV] Match via '{search_term}': {video_id}")
                break

        if not video_id:
            raise HTTPException(status_code=404, detail=f"No video found for '{song_title}' by '{artist}'")

        stream_result, from_cache = await cached_video_stream(video_id)
        return stream_result

    except HTTPException:
        raise
    except Exception as e:
        print(f"[SEARCH-EXACTMV] Error: {e}")
        raise HTTPException(status_code=500, detail=f"Exact MV search failed: {str(e)}")


@app.get("/search/exactwithMVMobile/", response_model=VideoStreamMobileResponse)
async def search_exact_music_with_mv_mobile(
    request: Request,
    song_title: str = Query(...),
    artist: str = Query(...)
):
    """
    Search for a music video and return URLs for seekable MP4 streaming.

    Point your mobile video player at the returned `stream_url`.

    First play will wait ~10–30s while the server merges video+audio to disk,
    after which the player receives a fully seekable response with correct
    duration and timeline support. Subsequent plays of the same video are
    instant (served from the disk cache).
    """
    if not song_title or len(song_title.strip()) < 2:
        raise HTTPException(status_code=400, detail="Song title must be at least 2 characters")
    if not artist or len(artist.strip()) < 2:
        raise HTTPException(status_code=400, detail="Artist name must be at least 2 characters")

    try:
        combined_query = f"{song_title} {artist}"
        search_terms = [
            f"{combined_query} official music video",
            f"{combined_query} music video",
            f"{combined_query} mv",
            f"{combined_query} color coded",
            f"{combined_query} visualizer",
            f"{combined_query} lyrics video",
            f"{combined_query} official video",
            f"{combined_query} audio"
        ]

        video_id = None
        for search_term in search_terms:
            results, _ = await cached_search(search_term, limit=3)
            if results:
                video_id = results[0]['videoId']
                print(f"[SEARCH-MOBILE] Match via '{search_term}': {video_id}")
                break

        if not video_id:
            raise HTTPException(status_code=404, detail=f"No video found for '{song_title}' by '{artist}'")

        base_url = str(request.base_url).rstrip('/')
        stream_result, from_cache = await cached_video_mobile_stream(video_id, base_url=base_url)
        print(f"[SEARCH-MOBILE] {'cached' if from_cache else 'fresh'} for {video_id}")
        return stream_result

    except HTTPException:
        raise
    except Exception as e:
        print(f"[SEARCH-MOBILE] Error: {e}")
        raise HTTPException(status_code=500, detail=f"Mobile MV search failed: {str(e)}")


@app.get("/stream/{video_id}", response_model=StreamResponse)
async def get_stream(request: Request, video_id: str):
    """Get MP3 audio streaming URL — guaranteed MP3 format only."""
    if not video_id:
        raise HTTPException(status_code=400, detail="Video ID is required")
    try:
        result, from_cache = await cached_audio_stream(video_id)
        print(f"[AUDIO] {'cached' if from_cache else 'fresh'} MP3 for {video_id}")
        return result
    except HTTPException:
        raise
    except Exception as e:
        print(f"[AUDIO] Error: {e}")
        raise HTTPException(status_code=500, detail="Failed to get MP3 audio stream")


@app.get("/streamvideo/{video_id}", response_model=VideoStreamResponse)
async def get_video_stream(request: Request, video_id: str):
    """Get highest quality video streaming URL (desktop / non-mobile)."""
    if not video_id:
        raise HTTPException(status_code=400, detail="Video ID is required")
    try:
        result, from_cache = await cached_video_stream(video_id)
        print(f"[VIDEO] {'cached' if from_cache else 'fresh'} for {video_id}")
        return result
    except HTTPException:
        raise
    except Exception as e:
        print(f"[VIDEO] Error: {e}")
        raise HTTPException(status_code=500, detail="Failed to get video stream")


@app.get("/streamvideo/mobile/{video_id}", response_model=VideoStreamMobileResponse)
async def get_video_stream_mobile(request: Request, video_id: str):
    """
    Get stream info for mobile video playback.
    Use the returned `stream_url` as your player's video source.
    The URL supports 206 range requests for full seeking support.
    """
    if not video_id:
        raise HTTPException(status_code=400, detail="Video ID is required")
    try:
        base_url = str(request.base_url).rstrip('/')
        result, from_cache = await cached_video_mobile_stream(video_id, base_url=base_url)
        print(f"[VIDEO-MOBILE] {'cached' if from_cache else 'fresh'} for {video_id}")
        return result
    except HTTPException:
        raise
    except Exception as e:
        print(f"[VIDEO-MOBILE] Error: {e}")
        raise HTTPException(status_code=500, detail="Failed to get mobile video stream")


# ─── Discovery / Charts endpoints ────────────────────────────────────────────

@app.get("/top-artists")
async def get_top_artists(request: Request, limit: int = 100):
    try:
        return lastfm_client.get_global_top_artists(limit=limit)
    except Exception as e:
        print(f"[LASTFM] Error: {e}")
        raise HTTPException(status_code=500, detail="Failed to fetch top artists")


@app.get("/getartistssongs/{artist_name}")
def get_artists_songs(request: Request, artist_name: str):
    result = itunes_client.get_artist_songs_with_sample_thumbnails(artist_name)
    if not result["total_songs"]:
        raise HTTPException(status_code=404, detail=f"Artist '{artist_name}' not found or no songs available.")
    return result


@app.get("/getrelatedartists/{song_name}")
async def get_related_artists(request: Request, song_name: str):
    try:
        loop = asyncio.get_event_loop()
        related_artists = await loop.run_in_executor(
            None, itunes_client.get_top_5_artists_for_song, song_name
        )
        if not related_artists:
            return {"song": song_name, "related_artists": []}

        async def fetch_artist_data(artist_name):
            image = await loop.run_in_executor(None, lastfm_client.get_artist_image, artist_name)
            return {"artist_name": artist_name, "image": image}

        results = await asyncio.gather(*[fetch_artist_data(a) for a in related_artists])
        return {"song": song_name, "related_artists": results}

    except Exception as e:
        print(f"[RELATED-ARTISTS] Error: {e}")
        raise HTTPException(status_code=500, detail="Failed to fetch related artists")


@app.get("/topglobalartists")
def top_global_artists(request: Request, limit: int = 100):
    result = itunes_client.get_top_global_artists_with_thumbnails(limit=limit)
    if not result["artists"]:
        raise HTTPException(status_code=404, detail="Unable to fetch top global artists.")
    return result


@app.get("/topglobalsongs")
def top_global_songs(request: Request, limit: int = 100):
    result = itunes_client.get_top_global_songs_with_thumbnails(limit=limit)
    if not result["songs"]:
        raise HTTPException(status_code=404, detail="Unable to fetch top global songs.")
    return result


@app.get("/topcountrysongs/{country_code}")
def top_country_songs(request: Request, country_code: str, limit: int = 100):
    result = itunes_client.get_top_country_songs_with_thumbnails(country_code=country_code, limit=limit)
    if not result["songs"]:
        raise HTTPException(status_code=404, detail=f"Unable to fetch top songs for country '{country_code}'.")
    return result


# ─── Admin / Infra endpoints ──────────────────────────────────────────────────

@app.get("/health")
async def health_check(request: Request):
    return {
        "status": "healthy",
        "service": "Ultra High-Performance Music Streaming API",
        "audio_format": "MP3 ONLY (320kbps preferred)",
        "video_mobile": "Seekable MP4 via disk merge + 206 range support",
        "thread_pools": {
            "search_pools": len(search_executors),
            "audio_pools": len(audio_executors),
            "video_pools": len(video_executors),
            "total_threads": 36
        },
        "cache_stats": {
            "search_cache": search_cache.stats(),
            "audio_cache": audio_cache.stats(),
            "video_cache": video_cache.stats(),
            "video_mobile_cache": video_mobile_cache.stats(),
        }
    }


@app.get("/stats")
async def performance_stats(request: Request):
    active_threads = {
        "search": sum(len(e._threads) if e._threads else 0 for e in search_executors),
        "audio":  sum(len(e._threads) if e._threads else 0 for e in audio_executors),
        "video":  sum(len(e._threads) if e._threads else 0 for e in video_executors),
    }
    return {
        "performance_optimization": "ULTRA ACTIVE with MP3-ONLY AUDIO + SEEKABLE MP4 VIDEO",
        "architecture": {
            "search_endpoint":       f"{len(search_executors)} pools × 4 threads = 12 total",
            "audio_stream_endpoint": f"{len(audio_executors)} pools × 4 threads = 12 total (MP3 ONLY)",
            "video_stream_endpoint": f"{len(video_executors)} pools × 4 threads = 12 total",
            "total_worker_threads":  36,
            "mobile_video": (
                "ffmpeg merges to disk → 206 range responses (full seeking). "
                "First play waits for merge; all subsequent plays are instant from disk cache."
            )
        },
        "active_threads": active_threads,
        "cache_performance": {
            "search_cache":       {**search_cache.stats(),       "ttl_minutes": 15},
            "audio_cache":        {**audio_cache.stats(),        "ttl_minutes": 60},
            "video_cache":        {**video_cache.stats(),        "ttl_minutes": 45},
            "video_mobile_cache": {**video_mobile_cache.stats(), "ttl_minutes": 45,
                                   "note": "Stores raw YT stream URLs only"},
        },
        "concurrent_performance": {
            "max_simultaneous_search": 12,
            "max_simultaneous_audio":  12,
            "max_simultaneous_video":  12,
            "request_deduplication":   "Active",
            "load_balancing":          "Active",
            "merge_deduplication":     "Active (asyncio.Event per video_id)",
        }
    }


@app.post("/cache/clear")
async def clear_cache(request: Request):
    search_cache.clear()
    audio_cache.clear()
    video_cache.clear()
    video_mobile_cache.clear()
    return {"status": "success", "message": "All caches cleared", "timestamp": datetime.now().isoformat()}


@app.get("/cache/stats")
async def cache_statistics(request: Request):
    return {
        "search_cache":       {**search_cache.stats(),       "ttl_minutes": 15},
        "audio_cache":        {**audio_cache.stats(),        "ttl_minutes": 60, "format": "MP3 ONLY"},
        "video_cache":        {**video_cache.stats(),        "ttl_minutes": 45},
        "video_mobile_cache": {**video_mobile_cache.stats(), "ttl_minutes": 45},
        "total_cached_items": "Managed by Redis"
    }


@app.get("/test-db")
def test_db():
    try:
        with engine.connect() as conn:
            result = conn.execute(text("SELECT 1"))
            return {"status": "success", "message": "FastAPI connected to PostgreSQL!", "result": result.scalar()}
    except Exception as e:
        return {"status": "error", "message": str(e)}


@app.get("/performance/realtime")
async def realtime_performance(request: Request):
    return {
        "timestamp": datetime.now().isoformat(),
        "audio_format": "MP3 ONLY",
        "mobile_video": "Seekable MP4 — disk merge + 206 range support",
        "active_merges": list(_merge_in_progress.keys()),
        "thread_utilization": {
            "search_pools": [{"pool_id": i, "active": len(e._threads) if e._threads else 0, "max": e._max_workers} for i, e in enumerate(search_executors)],
            "audio_pools":  [{"pool_id": i, "active": len(e._threads) if e._threads else 0, "max": e._max_workers} for i, e in enumerate(audio_executors)],
            "video_pools":  [{"pool_id": i, "active": len(e._threads) if e._threads else 0, "max": e._max_workers} for i, e in enumerate(video_executors)],
        },
        "deduplication": {
            "active_requests": len(request_deduplicator.active_requests),
            "status": "preventing duplicate processing"
        }
    }


@app.get("/format/info")
async def format_info(request: Request):
    return {
        "audio_streaming": {
            "format": "MP3 ONLY",
            "quality": "320kbps preferred",
            "endpoint": "/stream/{video_id}"
        },
        "video_streaming_desktop": {
            "formats": "Various (MP4/WebM)",
            "quality": "Highest available (up to 4K)",
            "endpoint": "/streamvideo/{video_id}"
        },
        "video_streaming_mobile": {
            "format": "MP4 (fully merged, moov atom at front)",
            "seeking": "Full 206 range support — timeline scrubbing and duration display work",
            "first_play_latency": "~10–30s (ffmpeg disk merge, runs once per video)",
            "subsequent_play_latency": "~0ms (served from disk cache)",
            "disk_cleanup": "Merged files auto-deleted after 2 hours",
            "endpoint": "/video/proxy/{video_id}",
            "usage": "Point AVPlayer / ExoPlayer directly at this URL"
        },
        "video_streaming_mobile_pipe": {
            "format": "Fragmented MP4 (fMP4, no seeking)",
            "latency": "~200ms to first byte",
            "seeking": "Not supported",
            "endpoint": "/video/stream/{video_id}",
            "usage": "Use only if you need instant start and don't need seeking"
        }
    }


if __name__ == "__main__":
    print("🚀 ==> HanyaMusic Music Streaming API <==")
    print("🌐 API: http://localhost:8000")
    print("📚 Docs: http://localhost:8000/docs")
    print("📊 Stats: http://localhost:8000/stats")
    print("🎥 Seekable video: /video/proxy/{video_id}  (206 range, full seeking)")
    print("⚡ Pipe stream:    /video/stream/{video_id} (~200ms start, no seeking)")

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
# Search:        /search?q=aespa
# Audio:         /stream/5oQVTnq-UKk
# Video:         /streamvideo/5oQVTnq-UKk
# Mobile video:  /video/proxy/5oQVTnq-UKk   ← seekable, use this in AVPlayer/ExoPlayer
# Stats:         /stats
# Format info:   /format/info

# To start with ngrok:
# ngrok http --domain=instinctually-monosodium-shawnda.ngrok-free.app 8000
# https://instinctually-monosodium-shawnda.ngrok-free.app/
# cloudflared tunnel --url http://localhost:8000