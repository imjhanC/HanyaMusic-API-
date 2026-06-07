from fastapi import APIRouter, Depends, status, HTTPException, Form, File, UploadFile
from fastapi.security import OAuth2PasswordBearer, OAuth2PasswordRequestForm
from sqlalchemy.orm import Session
from sqlalchemy import text
from datetime import datetime, timedelta
from typing import Optional
from jose import JWTError, jwt
import bcrypt as bcrypt_lib
import base64
import secrets  # <-- ADDED for refresh tokens

from SQLconn import get_db
from pydantic import BaseModel

# ─── Pydantic Models ─────────────────────────────────────────────────────────

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
    refresh_token: str  # <-- ADDED
    token_type: str

class TokenData(BaseModel):
    username: Optional[str] = None

class RefreshTokenRequest(BaseModel): # <-- ADDED
    refresh_token: str

# ─── Configuration ───────────────────────────────────────────────────────────

SECRET_KEY = "HANYAMUSIC_SECRET_KEY_PLEASE_CHANGE_IN_PRODUCTION"  # TODO: Move to .env
ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_MINUTES = 30
REFRESH_TOKEN_EXPIRE_DAYS = 30 # <-- ADDED

# ─── Password Helpers ────────────────────────────────────────────────────────

def hash_password(password: str) -> str:
    password_bytes = password.encode('utf-8')[:72]
    salt = bcrypt_lib.gensalt()
    hashed = bcrypt_lib.hashpw(password_bytes, salt)
    return hashed.decode('utf-8')

def verify_password(plain_password: str, hashed_password: str) -> bool:
    password_bytes = plain_password.encode('utf-8')[:72]
    return bcrypt_lib.checkpw(password_bytes, hashed_password.encode('utf-8'))

# ─── JWT Helpers ─────────────────────────────────────────────────────────────

oauth2_scheme = OAuth2PasswordBearer(tokenUrl="auth/token") # Make sure tokenUrl matches the route prefix

def create_access_token(data: dict, expires_delta: Optional[timedelta] = None):
    to_encode = data.copy()
    expire = datetime.utcnow() + (expires_delta or timedelta(minutes=15))
    to_encode.update({"exp": expire})
    return jwt.encode(to_encode, SECRET_KEY, algorithm=ALGORITHM)

def create_refresh_token() -> str: # <-- ADDED
    return secrets.token_urlsafe(32)

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

# ─── Router Setup ────────────────────────────────────────────────────────────

router = APIRouter(prefix="/auth", tags=["authentication"])

# ─── Auth Endpoints ──────────────────────────────────────────────────────────

@router.post("/register", status_code=status.HTTP_201_CREATED)
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
        # 1. Insert User
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
        
        # 2. Generate Tokens
        access_token = create_access_token(
            data={"sub": new_user.username},
            expires_delta=timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES)
        )
        refresh_token = create_refresh_token()
        expires_at = datetime.utcnow() + timedelta(days=REFRESH_TOKEN_EXPIRE_DAYS)

        # 3. Store Refresh Token
        db.execute(
            text("""
                INSERT INTO user_sessions (user_id, refresh_token, expires_at)
                VALUES (:user_id, :refresh_token, :expires_at)
            """),
            {"user_id": new_user.id, "refresh_token": refresh_token, "expires_at": expires_at}
        )
        db.commit()

        return {
            "id": new_user.id,
            "username": new_user.username,
            "email": new_user.email,
            "created_at": new_user.created_at,
            "message": "User registered successfully",
            "access_token": access_token,
            "refresh_token": refresh_token, # Now returns refresh token too!
            "token_type": "bearer"
        }
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=500, detail=f"Registration failed: {str(e)}")

@router.post("/token", response_model=Token)
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

    # 1. Create short-lived Access Token
    access_token = create_access_token(
        data={"sub": user.username},
        expires_delta=timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES)
    )

    # 2. Create long-lived Refresh Token
    refresh_token = create_refresh_token()
    expires_at = datetime.utcnow() + timedelta(days=REFRESH_TOKEN_EXPIRE_DAYS)

    # 3. Store the session in the database
    db.execute(
        text("""
            INSERT INTO user_sessions (user_id, refresh_token, expires_at)
            VALUES (:user_id, :refresh_token, :expires_at)
        """),
        {"user_id": user.id, "refresh_token": refresh_token, "expires_at": expires_at}
    )
    db.commit()

    return {
        "access_token": access_token, 
        "refresh_token": refresh_token,
        "token_type": "bearer"
    }

@router.post("/refresh-token")
async def refresh_token(request: RefreshTokenRequest, db: Session = Depends(get_db)):
    # 1. Verify the refresh token exists, isn't revoked, and isn't expired
    session = db.execute(
        text("""
            SELECT user_sessions.*, users.username 
            FROM user_sessions 
            JOIN users ON user_sessions.user_id = users.id
            WHERE refresh_token = :refresh_token AND revoked = FALSE AND expires_at > NOW()
        """),
        {"refresh_token": request.refresh_token}
    ).fetchone()

    if not session:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid or expired refresh token")

    # 2. Issue a fresh access token
    new_access_token = create_access_token(
        data={"sub": session.username},
        expires_delta=timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES)
    )

    # 3. ROLLING SESSION: Push the expiration date back another 30 days
    new_expires_at = datetime.utcnow() + timedelta(days=REFRESH_TOKEN_EXPIRE_DAYS)
    db.execute(
        text("""
            UPDATE user_sessions 
            SET expires_at = :new_expires_at 
            WHERE refresh_token = :refresh_token
        """),
        {"new_expires_at": new_expires_at, "refresh_token": request.refresh_token}
    )
    db.commit()

    return {
        "access_token": new_access_token,
        "refresh_token": request.refresh_token, 
        "token_type": "bearer"
    }

@router.post("/logout")
async def logout(request: RefreshTokenRequest, db: Session = Depends(get_db)):
    # 1. Mark the specific refresh token as revoked
    db.execute(
        text("UPDATE user_sessions SET revoked = TRUE WHERE refresh_token = :refresh_token"),
        {"refresh_token": request.refresh_token}
    )
    db.commit()
    return {"message": "Successfully logged out"}

@router.get("/users/me", response_model=UserResponse)
async def read_users_me(current_user = Depends(get_current_user)):
    return current_user

@router.get("/users/{user_id}", response_model=UserResponse)
def get_user_details(
    user_id: int,
    current_user = Depends(get_current_user),
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
