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
    new_image_url = None
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
                SET name = COALESCE(:name, name),
                    description = COALESCE(:description, description),
                    is_public = COALESCE(:is_public, is_public),
                    image_url = COALESCE(:image_url, image_url),
                    updated_at = NOW()
                WHERE id = :playlist_id AND user_id = :user_id
                RETURNING *
            """),
            {
                "name": name,
                "description": description,
                "is_public": is_public,
                "image_url": new_image_url,
                "playlist_id": playlist_id,
                "user_id": current_user.id,
            }
        )
        updated = result.fetchone()

        if not updated:
            # Check why it failed (fallback for detailed error message)
            playlist = db.execute(
                text("SELECT id, user_id FROM playlists WHERE id = :playlist_id"),
                {"playlist_id": playlist_id}
            ).fetchone()
            if not playlist:
                raise HTTPException(status_code=404, detail="Playlist not found")
            if playlist.user_id != current_user.id:
                raise HTTPException(status_code=403, detail="You do not own this playlist")

        db.commit()
        return updated
    except HTTPException:
        db.rollback()
        raise
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
    try:
        result = db.execute(
            text("DELETE FROM playlists WHERE id = :playlist_id AND user_id = :user_id RETURNING id"),
            {"playlist_id": playlist_id, "user_id": current_user.id}
        )
        deleted = result.fetchone()

        if not deleted:
            # Check why it failed
            playlist = db.execute(
                text("SELECT id, user_id FROM playlists WHERE id = :playlist_id"),
                {"playlist_id": playlist_id}
            ).fetchone()
            if not playlist:
                raise HTTPException(status_code=404, detail="Playlist not found")
            if playlist.user_id != current_user.id:
                raise HTTPException(status_code=403, detail="You do not own this playlist")

        db.commit()
    except HTTPException:
        db.rollback()
        raise
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
    try:
        result = db.execute(
            text("""
                INSERT INTO playlist_tracks (playlist_id, video_id, title, artist, image_url, duration_seconds, track_order)
                SELECT :playlist_id, :video_id, :title, :artist, :image_url, :duration_seconds,
                       COALESCE(:track_order, (SELECT COALESCE(MAX(track_order), 0) + 1 FROM playlist_tracks WHERE playlist_id = :playlist_id))
                WHERE EXISTS (
                    SELECT 1 FROM playlists WHERE id = :playlist_id AND user_id = :user_id
                )
                RETURNING *
            """),
            {
                "playlist_id": playlist_id,
                "video_id": track.video_id,
                "title": track.title,
                "artist": track.artist,
                "image_url": track.image_url,
                "duration_seconds": track.duration_seconds,
                "track_order": track.track_order,
                "user_id": current_user.id,
            }
        )
        new_track = result.fetchone()

        if not new_track:
            # Fallback error check
            playlist = db.execute(
                text("SELECT id, user_id FROM playlists WHERE id = :playlist_id"),
                {"playlist_id": playlist_id}
            ).fetchone()
            if not playlist:
                raise HTTPException(status_code=404, detail="Playlist not found")
            if playlist.user_id != current_user.id:
                raise HTTPException(status_code=403, detail="You do not own this playlist")

        db.commit()
        return new_track
    except HTTPException:
        db.rollback()
        raise
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
    rows = db.execute(
        text("""
            SELECT pt.*, p.user_id AS playlist_owner_id, p.is_public AS playlist_is_public
            FROM playlists p
            LEFT JOIN playlist_tracks pt ON p.id = pt.playlist_id
            WHERE p.id = :playlist_id
            ORDER BY pt.track_order ASC, pt.added_at ASC
        """),
        {"playlist_id": playlist_id}
    ).fetchall()

    if not rows:
        raise HTTPException(status_code=404, detail="Playlist not found")

    first_row = rows[0]
    if not first_row.playlist_is_public and first_row.playlist_owner_id != current_user.id:
        raise HTTPException(status_code=403, detail="Access denied to this private playlist")

    # If playlist has no tracks, left join yields None values for all pt.* columns
    tracks = [r for r in rows if r.id is not None]
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
    try:
        result = db.execute(
            text("""
                UPDATE playlist_tracks pt
                SET track_order = COALESCE(:track_order, pt.track_order),
                    title = COALESCE(:title, pt.title),
                    artist = COALESCE(:artist, pt.artist)
                FROM playlists p
                WHERE pt.playlist_id = p.id
                  AND pt.id = :track_id
                  AND pt.playlist_id = :playlist_id
                  AND p.user_id = :user_id
                RETURNING pt.*
            """),
            {
                "track_order": body.track_order,
                "title": body.title,
                "artist": body.artist,
                "track_id": track_id,
                "playlist_id": playlist_id,
                "user_id": current_user.id
            }
        )
        updated = result.fetchone()

        if not updated:
            # Fallback error check
            playlist = db.execute(
                text("SELECT id, user_id FROM playlists WHERE id = :playlist_id"),
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

        db.commit()
        return updated
    except HTTPException:
        db.rollback()
        raise
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
    try:
        result = db.execute(
            text("""
                DELETE FROM playlist_tracks pt
                USING playlists p
                WHERE pt.playlist_id = p.id
                  AND pt.id = :track_id
                  AND pt.playlist_id = :playlist_id
                  AND p.user_id = :user_id
                RETURNING pt.id;
            """),
            {
                "track_id": track_id,
                "playlist_id": playlist_id,
                "user_id": current_user.id
            }
        )
        deleted = result.fetchone()

        if not deleted:
            # Fallback error check
            playlist = db.execute(
                text("SELECT id, user_id FROM playlists WHERE id = :playlist_id"),
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

        db.commit()
    except HTTPException:
        db.rollback()
        raise
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
    try:
        # Single-trip conditional delete
        result = db.execute(
            text("""
                DELETE FROM playlist_tracks pt
                USING playlists p
                WHERE pt.playlist_id = p.id
                  AND pt.playlist_id = :playlist_id
                  AND p.user_id = :user_id
                RETURNING pt.id;
            """),
            {"playlist_id": playlist_id, "user_id": current_user.id}
        )
        deleted = result.fetchall()

        # Check ownership and existence (since clearing an already empty playlist is not an error)
        playlist = db.execute(
            text("SELECT id, user_id FROM playlists WHERE id = :playlist_id"),
            {"playlist_id": playlist_id}
        ).fetchone()

        if not playlist:
            raise HTTPException(status_code=404, detail="Playlist not found")
        if playlist.user_id != current_user.id:
            raise HTTPException(status_code=403, detail="You do not own this playlist")

        db.commit()
    except HTTPException:
        db.rollback()
        raise
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=500, detail=f"Failed to clear playlist: {str(e)}")