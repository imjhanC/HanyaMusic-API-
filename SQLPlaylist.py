from fastapi import APIRouter, Depends, status, HTTPException, File, UploadFile, Form
from sqlalchemy.orm import Session
from sqlalchemy import text
from datetime import datetime
from typing import Optional, List
import base64

from SQLconn import get_db
from SQLAuth import get_current_user
from pydantic import BaseModel

# ─── Pydantic Models ─────────────────────────────────────────────────────────

class PlaylistCreate(BaseModel):
    name: str
    description: Optional[str] = None
    is_public: bool = False

class PlaylistUpdate(BaseModel):
    name: Optional[str] = None
    description: Optional[str] = None
    is_public: Optional[bool] = None

class PlaylistResponse(BaseModel):
    id: int
    user_id: int
    name: str
    description: Optional[str]
    image_url: Optional[str]
    is_public: bool
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True

class TrackAdd(BaseModel):
    video_id: str
    title: str
    artist: Optional[str] = None
    image_url: Optional[str] = None
    duration_seconds: Optional[int] = None
    track_order: Optional[int] = None

class TrackUpdate(BaseModel):
    track_order: Optional[int] = None
    title: Optional[str] = None
    artist: Optional[str] = None

class TrackResponse(BaseModel):
    id: int
    playlist_id: int
    video_id: str
    title: str
    artist: Optional[str]
    image_url: Optional[str]
    duration_seconds: Optional[int]
    track_order: Optional[int]
    added_at: datetime

    class Config:
        from_attributes = True

# ─── Router Setup ────────────────────────────────────────────────────────────

router = APIRouter(prefix="/playlists", tags=["playlists"])

# ─── Playlist Endpoints ──────────────────────────────────────────────────────

@router.post("", status_code=status.HTTP_201_CREATED, response_model=PlaylistResponse)
async def create_playlist(
    name: str = Form(...),
    description: Optional[str] = Form(None),
    is_public: bool = Form(False),
    image: Optional[UploadFile] = File(None),
    current_user=Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """Create a new playlist for the authenticated user."""
    image_url_value = None
    if image:
        if image.content_type not in ["image/jpeg", "image/png", "image/gif", "image/jpg", "image/webp"]:
            raise HTTPException(status_code=400, detail="Invalid image format")
        file_content = await image.read()
        encoded = base64.b64encode(file_content).decode("utf-8")
        image_url_value = f"data:{image.content_type};base64,{encoded}"

    try:
        result = db.execute(
            text("""
                INSERT INTO playlists (user_id, name, description, image_url, is_public)
                VALUES (:user_id, :name, :description, :image_url, :is_public)
                RETURNING *
            """),
            {
                "user_id": current_user.id,
                "name": name,
                "description": description,
                "image_url": image_url_value,
                "is_public": is_public,
            }
        )
        new_playlist = result.fetchone()
        db.commit()
        return new_playlist
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=500, detail=f"Failed to create playlist: {str(e)}")


@router.get("/me", response_model=List[PlaylistResponse])
def get_my_playlists(
    current_user=Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """Get all playlists belonging to the authenticated user."""
    playlists = db.execute(
        text("SELECT * FROM playlists WHERE user_id = :user_id ORDER BY created_at DESC"),
        {"user_id": current_user.id}
    ).fetchall()
    # If the user has no playlists yet, auto-create a "Liked Songs" playlist
    if not playlists:
        try:
            result = db.execute(
                text("""
                    INSERT INTO playlists (user_id, name, description, is_public, created_at, updated_at)
                    VALUES (:user_id, :name, :description, :is_public, NOW(), NOW())
                    RETURNING *
                """),
                {
                    "user_id": current_user.id,
                    "name": "Liked Songs",
                    "description": "",
                    "is_public": False,
                }
            )
            new_playlist = result.fetchone()
            db.commit()
            return [new_playlist]
        except Exception as e:
            db.rollback()
            raise HTTPException(status_code=500, detail=f"Failed to create default playlist: {str(e)}")

    return playlists


@router.get("/user/{user_id}", response_model=List[PlaylistResponse])
def get_user_playlists(
    user_id: int,
    current_user=Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """
    Get playlists of any user.
    - Own playlists: returns all (public + private).
    - Other users: returns only public playlists.
    """
    if current_user.id == user_id:
        playlists = db.execute(
            text("SELECT * FROM playlists WHERE user_id = :user_id ORDER BY created_at DESC"),
            {"user_id": user_id}
        ).fetchall()
        # If the user is requesting their own playlists and has none, create "Liked Songs"
        if not playlists:
            try:
                result = db.execute(
                    text("""
                        INSERT INTO playlists (user_id, name, description, is_public, created_at, updated_at)
                        VALUES (:user_id, :name, :description, :is_public, NOW(), NOW())
                        RETURNING *
                    """),
                    {
                        "user_id": user_id,
                        "name": "Liked Songs",
                        "description": "",
                        "is_public": False,
                    }
                )
                new_playlist = result.fetchone()
                db.commit()
                playlists = [new_playlist]
            except Exception as e:
                db.rollback()
                raise HTTPException(status_code=500, detail=f"Failed to create default playlist: {str(e)}")
    else:
        playlists = db.execute(
            text("""
                SELECT * FROM playlists
                WHERE user_id = :user_id AND is_public = TRUE
                ORDER BY created_at DESC
            """),
            {"user_id": user_id}
        ).fetchall()
    return playlists


@router.get("/{playlist_id}", response_model=PlaylistResponse)
def get_playlist(
    playlist_id: int,
    current_user=Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """Get a single playlist by ID. Private playlists are only visible to their owner."""
    playlist = db.execute(
        text("SELECT * FROM playlists WHERE id = :playlist_id"),
        {"playlist_id": playlist_id}
    ).fetchone()

    if not playlist:
        raise HTTPException(status_code=404, detail="Playlist not found")
    if not playlist.is_public and playlist.user_id != current_user.id:
        raise HTTPException(status_code=403, detail="Access denied to this private playlist")

    return playlist


@router.patch("/{playlist_id}", response_model=PlaylistResponse)
async def update_playlist(
    playlist_id: int,
    name: Optional[str] = Form(None),
    description: Optional[str] = Form(None),
    is_public: Optional[bool] = Form(None),
    image: Optional[UploadFile] = File(None),
    current_user=Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """Update a playlist's metadata. Only the owner can update."""
    playlist = db.execute(
        text("SELECT * FROM playlists WHERE id = :playlist_id"),
        {"playlist_id": playlist_id}
    ).fetchone()

    if not playlist:
        raise HTTPException(status_code=404, detail="Playlist not found")
    if playlist.user_id != current_user.id:
        raise HTTPException(status_code=403, detail="You do not own this playlist")

    new_name        = name        if name        is not None else playlist.name
    new_description = description if description is not None else playlist.description
    new_is_public   = is_public   if is_public   is not None else playlist.is_public
    new_image_url   = playlist.image_url

    if image:
        if image.content_type not in ["image/jpeg", "image/png", "image/gif", "image/jpg", "image/webp"]:
            raise HTTPException(status_code=400, detail="Invalid image format")
        file_content = await image.read()
        encoded = base64.b64encode(file_content).decode("utf-8")
        new_image_url = f"data:{image.content_type};base64,{encoded}"

    try:
        result = db.execute(
            text("""
                UPDATE playlists
                SET name = :name, description = :description,
                    is_public = :is_public, image_url = :image_url,
                    updated_at = NOW()
                WHERE id = :playlist_id
                RETURNING *
            """),
            {
                "name": new_name,
                "description": new_description,
                "is_public": new_is_public,
                "image_url": new_image_url,
                "playlist_id": playlist_id,
            }
        )
        updated = result.fetchone()
        db.commit()
        return updated
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=500, detail=f"Failed to update playlist: {str(e)}")


@router.delete("/{playlist_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_playlist(
    playlist_id: int,
    current_user=Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """Delete a playlist and all its tracks (cascade). Only the owner can delete."""
    playlist = db.execute(
        text("SELECT * FROM playlists WHERE id = :playlist_id"),
        {"playlist_id": playlist_id}
    ).fetchone()

    if not playlist:
        raise HTTPException(status_code=404, detail="Playlist not found")
    if playlist.user_id != current_user.id:
        raise HTTPException(status_code=403, detail="You do not own this playlist")

    try:
        db.execute(
            text("DELETE FROM playlists WHERE id = :playlist_id"),
            {"playlist_id": playlist_id}
        )
        db.commit()
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=500, detail=f"Failed to delete playlist: {str(e)}")


# ─── Playlist Track Endpoints ────────────────────────────────────────────────

@router.post("/{playlist_id}/tracks", status_code=status.HTTP_201_CREATED, response_model=TrackResponse)
def add_track(
    playlist_id: int,
    track: TrackAdd,
    current_user=Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """Add a track to a playlist. Only the owner can add tracks."""
    playlist = db.execute(
        text("SELECT * FROM playlists WHERE id = :playlist_id"),
        {"playlist_id": playlist_id}
    ).fetchone()

    if not playlist:
        raise HTTPException(status_code=404, detail="Playlist not found")
    if playlist.user_id != current_user.id:
        raise HTTPException(status_code=403, detail="You do not own this playlist")

    # Auto-assign track_order to the end if not provided
    track_order = track.track_order
    if track_order is None:
        row = db.execute(
            text("SELECT COALESCE(MAX(track_order), 0) + 1 AS next_order FROM playlist_tracks WHERE playlist_id = :playlist_id"),
            {"playlist_id": playlist_id}
        ).fetchone()
        track_order = row.next_order

    try:
        result = db.execute(
            text("""
                INSERT INTO playlist_tracks
                    (playlist_id, video_id, title, artist, image_url, duration_seconds, track_order)
                VALUES
                    (:playlist_id, :video_id, :title, :artist, :image_url, :duration_seconds, :track_order)
                RETURNING *
            """),
            {
                "playlist_id": playlist_id,
                "video_id": track.video_id,
                "title": track.title,
                "artist": track.artist,
                "image_url": track.image_url,
                "duration_seconds": track.duration_seconds,
                "track_order": track_order,
            }
        )
        new_track = result.fetchone()
        db.commit()
        return new_track
    except Exception as e:
        db.rollback()
        if "unique" in str(e).lower():
            raise HTTPException(status_code=409, detail="This track is already in the playlist")
        raise HTTPException(status_code=500, detail=f"Failed to add track: {str(e)}")


@router.get("/{playlist_id}/tracks", response_model=List[TrackResponse])
def get_tracks(
    playlist_id: int,
    current_user=Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """Get all tracks in a playlist, ordered by track_order."""
    playlist = db.execute(
        text("SELECT * FROM playlists WHERE id = :playlist_id"),
        {"playlist_id": playlist_id}
    ).fetchone()

    if not playlist:
        raise HTTPException(status_code=404, detail="Playlist not found")
    if not playlist.is_public and playlist.user_id != current_user.id:
        raise HTTPException(status_code=403, detail="Access denied to this private playlist")

    tracks = db.execute(
        text("""
            SELECT * FROM playlist_tracks
            WHERE playlist_id = :playlist_id
            ORDER BY track_order ASC, added_at ASC
        """),
        {"playlist_id": playlist_id}
    ).fetchall()
    return tracks


@router.patch("/{playlist_id}/tracks/{track_id}", response_model=TrackResponse)
def update_track(
    playlist_id: int,
    track_id: int,
    body: TrackUpdate,
    current_user=Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """Update a track's metadata (order, title, artist). Only the playlist owner can update."""
    playlist = db.execute(
        text("SELECT * FROM playlists WHERE id = :playlist_id"),
        {"playlist_id": playlist_id}
    ).fetchone()

    if not playlist:
        raise HTTPException(status_code=404, detail="Playlist not found")
    if playlist.user_id != current_user.id:
        raise HTTPException(status_code=403, detail="You do not own this playlist")

    track = db.execute(
        text("SELECT * FROM playlist_tracks WHERE id = :track_id AND playlist_id = :playlist_id"),
        {"track_id": track_id, "playlist_id": playlist_id}
    ).fetchone()

    if not track:
        raise HTTPException(status_code=404, detail="Track not found in this playlist")

    new_order  = body.track_order if body.track_order is not None else track.track_order
    new_title  = body.title       if body.title       is not None else track.title
    new_artist = body.artist      if body.artist      is not None else track.artist

    try:
        result = db.execute(
            text("""
                UPDATE playlist_tracks
                SET track_order = :track_order, title = :title, artist = :artist
                WHERE id = :track_id
                RETURNING *
            """),
            {"track_order": new_order, "title": new_title, "artist": new_artist, "track_id": track_id}
        )
        updated = result.fetchone()
        db.commit()
        return updated
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=500, detail=f"Failed to update track: {str(e)}")


@router.delete("/{playlist_id}/tracks/{track_id}", status_code=status.HTTP_204_NO_CONTENT)
def remove_track(
    playlist_id: int,
    track_id: int,
    current_user=Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """Remove a track from a playlist. Only the playlist owner can remove tracks."""
    playlist = db.execute(
        text("SELECT * FROM playlists WHERE id = :playlist_id"),
        {"playlist_id": playlist_id}
    ).fetchone()

    if not playlist:
        raise HTTPException(status_code=404, detail="Playlist not found")
    if playlist.user_id != current_user.id:
        raise HTTPException(status_code=403, detail="You do not own this playlist")

    track = db.execute(
        text("SELECT id FROM playlist_tracks WHERE id = :track_id AND playlist_id = :playlist_id"),
        {"track_id": track_id, "playlist_id": playlist_id}
    ).fetchone()

    if not track:
        raise HTTPException(status_code=404, detail="Track not found in this playlist")

    try:
        db.execute(
            text("DELETE FROM playlist_tracks WHERE id = :track_id"),
            {"track_id": track_id}
        )
        db.commit()
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=500, detail=f"Failed to remove track: {str(e)}")


@router.delete("/{playlist_id}/tracks", status_code=status.HTTP_204_NO_CONTENT)
def clear_playlist(
    playlist_id: int,
    current_user=Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """Remove ALL tracks from a playlist without deleting the playlist itself."""
    playlist = db.execute(
        text("SELECT * FROM playlists WHERE id = :playlist_id"),
        {"playlist_id": playlist_id}
    ).fetchone()

    if not playlist:
        raise HTTPException(status_code=404, detail="Playlist not found")
    if playlist.user_id != current_user.id:
        raise HTTPException(status_code=403, detail="You do not own this playlist")

    try:
        db.execute(
            text("DELETE FROM playlist_tracks WHERE playlist_id = :playlist_id"),
            {"playlist_id": playlist_id}
        )
        db.commit()
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=500, detail=f"Failed to clear playlist: {str(e)}")