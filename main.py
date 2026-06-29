import os
import re
import json
import time
import shutil
import base64
from datetime import datetime
from typing import Optional

import httpx
from fastapi import FastAPI, HTTPException, Depends, UploadFile, File, Header, Request
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from database import (
    init_db, get_db, hash_password, verify_password,
    create_session, get_user_by_token, get_project_folder,
    EXTENSIONS_DIR
)

app = FastAPI(title="KNI AI Extension Platform")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

init_db()


def build_manifest(row: dict) -> str:
    manifest = {
        "manifest_version": 3,
        "name": row["name"],
        "version": row["version"] or "1.0",
        "description": row["description"] or "",
        "author": row["author"] or "",
        "content_scripts": [{
            "matches": ["<all_urls>"],
            "js": ["inject.js"]
        }]
    }
    icon = row.get("icon_filename") or ""
    if icon:
        manifest["action"] = {"default_icon": {"128": icon}}
    return json.dumps(manifest, indent=2, ensure_ascii=False) + "\n"


def cleanup_project_folder(folder_path: str, icon_filename: str):
    allowed = {"manifest.json", "inject.js"}
    if icon_filename:
        allowed.add(icon_filename)
    if not os.path.exists(folder_path):
        return
    for fname in os.listdir(folder_path):
        if fname not in allowed:
            try:
                os.remove(os.path.join(folder_path, fname))
            except OSError:
                pass


def write_project_files(folder_path: str, row: dict, inject_content: str = None):
    os.makedirs(folder_path, exist_ok=True)
    icon = row.get("icon_filename") or ""
    cleanup_project_folder(folder_path, icon)
    with open(os.path.join(folder_path, "manifest.json"), "w", encoding="utf-8") as f:
        f.write(build_manifest(row))
    if inject_content is not None and inject_content.strip():
        with open(os.path.join(folder_path, "inject.js"), "w", encoding="utf-8") as f:
            f.write(inject_content)


def refresh_project_manifest(conn, project_id: int, user_id: int):
    row = conn.execute("SELECT * FROM projects WHERE id=? AND user_id=?", (project_id, user_id)).fetchone()
    if not row:
        return
    row = dict(row)
    folder_path = get_project_folder(user_id, row["folder_name"])
    inject_path = os.path.join(folder_path, "inject.js")
    inject_content = None
    if os.path.exists(inject_path):
        with open(inject_path, "r", encoding="utf-8") as f:
            inject_content = f.read()
    write_project_files(folder_path, row, inject_content)


# ── Auth dependency ────────────────────────────────────────────────────────────

async def get_current_user(authorization: str = Header(None)):
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Not authenticated")
    token = authorization.split(" ", 1)[1]
    user = get_user_by_token(token)
    if not user:
        raise HTTPException(status_code=401, detail="Invalid or expired token")
    return user


async def get_optional_user(authorization: str = Header(None)):
    if not authorization or not authorization.startswith("Bearer "):
        return None
    token = authorization.split(" ", 1)[1]
    return get_user_by_token(token)


# ── Pydantic models ────────────────────────────────────────────────────────────

class RegisterModel(BaseModel):
    username: str
    email: str
    password: str


class LoginModel(BaseModel):
    email: str
    password: str


class ProjectCreateModel(BaseModel):
    name: str
    version: str = "1.0"
    description: str = ""
    author: str = ""
    website: str = ""
    tags: str = ""


class ProjectUpdateModel(BaseModel):
    name: Optional[str] = None
    version: Optional[str] = None
    description: Optional[str] = None
    author: Optional[str] = None
    website: Optional[str] = None
    tags: Optional[str] = None


class SaveFilesModel(BaseModel):
    manifest_content: str
    inject_content: str


class SaveFileModel(BaseModel):
    filename: str
    content: str


class CommentModel(BaseModel):
    content: str


class PasswordModel(BaseModel):
    old_password: str
    new_password: str


class ProfileModel(BaseModel):
    bio: Optional[str] = None
    website: Optional[str] = None


class AIGenerateModel(BaseModel):
    prompt: str
    extension_name: str
    version: str = "1.0"
    description: str = ""
    author: str = ""
    api_key: str = ""


# ── AUTH ───────────────────────────────────────────────────────────────────────

@app.post("/api/auth/register")
async def register(data: RegisterModel):
    if len(data.username) < 3:
        raise HTTPException(400, "Username must be at least 3 characters")
    if not re.match(r'^[a-zA-Z0-9_]+$', data.username):
        raise HTTPException(400, "Username can only contain letters, numbers and underscores")
    if len(data.password) < 6:
        raise HTTPException(400, "Password must be at least 6 characters")
    conn = get_db()
    try:
        existing = conn.execute(
            "SELECT id FROM users WHERE LOWER(username)=? OR LOWER(email)=?",
            (data.username.lower(), data.email.lower())
        ).fetchone()
        if existing:
            raise HTTPException(400, "Username or email already taken")
        pw_hash, salt = hash_password(data.password)
        conn.execute(
            "INSERT INTO users (username, email, password_hash, salt) VALUES (?, ?, ?, ?)",
            (data.username.lower(), data.email.lower(), pw_hash, salt)
        )
        conn.commit()
        user = conn.execute("SELECT * FROM users WHERE LOWER(username)=?", (data.username.lower(),)).fetchone()
        token = create_session(user["id"])
        return {"token": token, "user": {"id": user["id"], "username": user["username"], "email": user["email"]}}
    finally:
        conn.close()


@app.post("/api/auth/login")
async def login(data: LoginModel):
    conn = get_db()
    try:
        user = conn.execute(
            "SELECT * FROM users WHERE LOWER(email)=?", (data.email.lower(),)
        ).fetchone()
        if not user or not verify_password(data.password, user["password_hash"], user["salt"]):
            raise HTTPException(401, "Invalid email or password")
        token = create_session(user["id"])
        return {"token": token, "user": {"id": user["id"], "username": user["username"], "email": user["email"]}}
    finally:
        conn.close()


@app.get("/api/auth/me")
async def get_me(user=Depends(get_current_user)):
    safe_keys = ["id", "username", "email", "bio", "website", "avatar_url", "created_at"]
    return {k: user[k] for k in safe_keys if k in user}


@app.put("/api/auth/password")
async def change_password(data: PasswordModel, user=Depends(get_current_user)):
    if not verify_password(data.old_password, user["password_hash"], user["salt"]):
        raise HTTPException(400, "Current password is incorrect")
    if len(data.new_password) < 6:
        raise HTTPException(400, "New password must be at least 6 characters")
    pw_hash, salt = hash_password(data.new_password)
    conn = get_db()
    conn.execute("UPDATE users SET password_hash=?, salt=? WHERE id=?", (pw_hash, salt, user["id"]))
    conn.commit()
    conn.close()
    return {"message": "Password changed successfully"}


@app.put("/api/auth/profile")
async def update_profile(data: ProfileModel, user=Depends(get_current_user)):
    conn = get_db()
    if data.bio is not None:
        conn.execute("UPDATE users SET bio=? WHERE id=?", (data.bio, user["id"]))
    if data.website is not None:
        conn.execute("UPDATE users SET website=? WHERE id=?", (data.website, user["id"]))
    conn.commit()
    conn.close()
    return {"message": "Profile updated"}


@app.post("/api/auth/logout")
async def logout(authorization: str = Header(None)):
    if authorization and authorization.startswith("Bearer "):
        token = authorization.split(" ", 1)[1]
        conn = get_db()
        conn.execute("DELETE FROM sessions WHERE token=?", (token,))
        conn.commit()
        conn.close()
    return {"message": "Logged out"}


# ── PROJECTS ───────────────────────────────────────────────────────────────────

@app.get("/api/projects")
async def list_projects(user=Depends(get_current_user)):
    conn = get_db()
    rows = conn.execute(
        "SELECT * FROM projects WHERE user_id=? ORDER BY updated_at DESC",
        (user["id"],)
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


@app.post("/api/projects")
async def create_project(data: ProjectCreateModel, user=Depends(get_current_user)):
    folder_name = re.sub(r'[^a-z0-9_]', '', data.name.lower().strip().replace(" ", "_").replace("-", "_"))
    if not folder_name:
        folder_name = f"extension_{int(time.time())}"
    conn = get_db()
    try:
        base = folder_name
        counter = 1
        while conn.execute(
            "SELECT id FROM projects WHERE user_id=? AND folder_name=?", (user["id"], folder_name)
        ).fetchone():
            folder_name = f"{base}_{counter}"
            counter += 1
        folder_path = get_project_folder(user["id"], folder_name)
        os.makedirs(folder_path, exist_ok=True)
        conn.execute("""
            INSERT INTO projects (user_id, folder_name, name, version, description, author, website, tags)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """, (user["id"], folder_name, data.name, data.version, data.description, data.author, data.website, data.tags))
        conn.commit()
        pid = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
        row = conn.execute("SELECT * FROM projects WHERE id=?", (pid,)).fetchone()
        return dict(row)
    finally:
        conn.close()


@app.get("/api/projects/{project_id}")
async def get_project(project_id: int, user=Depends(get_current_user)):
    conn = get_db()
    row = conn.execute("SELECT * FROM projects WHERE id=? AND user_id=?", (project_id, user["id"])).fetchone()
    conn.close()
    if not row:
        raise HTTPException(404, "Project not found")
    return dict(row)


@app.put("/api/projects/{project_id}")
async def update_project(project_id: int, data: ProjectUpdateModel, user=Depends(get_current_user)):
    conn = get_db()
    try:
        row = conn.execute("SELECT * FROM projects WHERE id=? AND user_id=?", (project_id, user["id"])).fetchone()
        if not row:
            raise HTTPException(404, "Project not found")
        updates = {"updated_at": datetime.now().isoformat()}
        for field in ["name", "version", "description", "author", "website", "tags"]:
            val = getattr(data, field)
            if val is not None:
                updates[field] = val
        set_clause = ", ".join(f"{k}=?" for k in updates)
        values = list(updates.values()) + [project_id]
        conn.execute(f"UPDATE projects SET {set_clause} WHERE id=?", values)
        conn.commit()
        refresh_project_manifest(conn, project_id, user["id"])
        row = conn.execute("SELECT * FROM projects WHERE id=?", (project_id,)).fetchone()
        return dict(row)
    finally:
        conn.close()


@app.delete("/api/projects/{project_id}")
async def delete_project(project_id: int, user=Depends(get_current_user)):
    conn = get_db()
    try:
        row = conn.execute("SELECT * FROM projects WHERE id=? AND user_id=?", (project_id, user["id"])).fetchone()
        if not row:
            raise HTTPException(404, "Project not found")
        folder_path = get_project_folder(user["id"], row["folder_name"])
        if os.path.exists(folder_path):
            shutil.rmtree(folder_path)
        conn.execute("DELETE FROM likes WHERE project_id=?", (project_id,))
        conn.execute("DELETE FROM comments WHERE project_id=?", (project_id,))
        conn.execute("DELETE FROM projects WHERE id=?", (project_id,))
        conn.commit()
        return {"message": "Project deleted"}
    finally:
        conn.close()


@app.post("/api/projects/{project_id}/generate")
async def save_generated_files(project_id: int, data: SaveFilesModel, user=Depends(get_current_user)):
    conn = get_db()
    row = conn.execute("SELECT * FROM projects WHERE id=? AND user_id=?", (project_id, user["id"])).fetchone()
    if not row:
        conn.close()
        raise HTTPException(404, "Project not found")
    row = dict(row)
    folder_path = get_project_folder(user["id"], row["folder_name"])
    if not data.inject_content or not data.inject_content.strip():
        conn.close()
        raise HTTPException(400, "inject.js content is required")
    write_project_files(folder_path, row, data.inject_content)
    conn.execute("UPDATE projects SET updated_at=? WHERE id=?", (datetime.now().isoformat(), project_id))
    conn.commit()
    conn.close()
    files = ["manifest.json", "inject.js"]
    if row.get("icon_filename"):
        files.append(row["icon_filename"])
    return {"message": "Files saved", "files": files}


@app.post("/api/projects/{project_id}/save-file")
async def save_single_file(project_id: int, data: SaveFileModel, user=Depends(get_current_user)):
    conn = get_db()
    row = conn.execute("SELECT * FROM projects WHERE id=? AND user_id=?", (project_id, user["id"])).fetchone()
    if not row:
        conn.close()
        raise HTTPException(404, "Project not found")
    filename = os.path.basename(data.filename)
    folder_path = get_project_folder(user["id"], row["folder_name"])
    os.makedirs(folder_path, exist_ok=True)
    with open(os.path.join(folder_path, filename), "w", encoding="utf-8") as f:
        f.write(data.content)
    conn.execute("UPDATE projects SET updated_at=? WHERE id=?", (datetime.now().isoformat(), project_id))
    conn.commit()
    conn.close()
    return {"message": "File saved", "filename": filename}


@app.post("/api/projects/{project_id}/upload-icon")
async def upload_icon(project_id: int, file: UploadFile = File(...), user=Depends(get_current_user)):
    conn = get_db()
    row = conn.execute("SELECT * FROM projects WHERE id=? AND user_id=?", (project_id, user["id"])).fetchone()
    if not row:
        conn.close()
        raise HTTPException(404, "Project not found")
    ext = os.path.splitext(file.filename or "")[1].lower()
    if ext not in [".png", ".jpg", ".jpeg", ".svg", ".gif"]:
        conn.close()
        raise HTTPException(400, "Invalid file type")
    raw_name = os.path.basename(file.filename or "")
    if not raw_name:
        conn.close()
        raise HTTPException(400, "Invalid filename")
    icon_filename = re.sub(r"[^\w.\-]", "_", raw_name)
    folder_path = get_project_folder(user["id"], row["folder_name"])
    os.makedirs(folder_path, exist_ok=True)
    old_icon = row["icon_filename"]
    if old_icon and old_icon != icon_filename:
        old_path = os.path.join(folder_path, old_icon)
        if os.path.exists(old_path):
            os.remove(old_path)
    content = await file.read()
    with open(os.path.join(folder_path, icon_filename), "wb") as f:
        f.write(content)
    conn.execute("UPDATE projects SET icon_filename=?, updated_at=? WHERE id=?",
                 (icon_filename, datetime.now().isoformat(), project_id))
    conn.commit()
    refresh_project_manifest(conn, project_id, user["id"])
    conn.close()
    return {"message": "Icon uploaded", "icon_filename": icon_filename}


TEXT_EXTENSIONS = {".json", ".js", ".css", ".html", ".txt", ".md", ".svg"}


@app.get("/api/projects/{project_id}/download")
async def download_project(project_id: int, user=Depends(get_optional_user)):
    conn = get_db()
    if user:
        row = conn.execute("""
            SELECT p.* FROM projects p WHERE p.id=? AND (p.user_id=? OR p.is_published=1)
        """, (project_id, user["id"])).fetchone()
    else:
        row = conn.execute("SELECT * FROM projects WHERE id=? AND is_published=1", (project_id,)).fetchone()
    if not row:
        conn.close()
        raise HTTPException(404, "Project not found")
    folder_path = get_project_folder(row["user_id"], row["folder_name"])
    if not os.path.exists(folder_path):
        conn.close()
        raise HTTPException(404, "Project files not found on server")
    conn.execute("UPDATE projects SET downloads=downloads+1 WHERE id=?", (project_id,))
    conn.commit()
    conn.close()
    files = []
    for root, _, fnames in os.walk(folder_path):
        for fname in fnames:
            fpath = os.path.join(root, fname)
            rel_name = os.path.relpath(fpath, folder_path).replace("\\", "/")
            ext = os.path.splitext(fname)[1].lower()
            if ext in TEXT_EXTENSIONS:
                with open(fpath, "r", encoding="utf-8", errors="replace") as f:
                    files.append({"name": rel_name, "content": f.read(), "encoding": "text"})
            else:
                with open(fpath, "rb") as f:
                    files.append({"name": rel_name, "content": base64.b64encode(f.read()).decode(), "encoding": "base64"})
    return {"folder_name": row["folder_name"], "files": files}


@app.get("/api/projects/{project_id}/files")
async def list_project_files(project_id: int, user=Depends(get_current_user)):
    conn = get_db()
    row = conn.execute("SELECT * FROM projects WHERE id=? AND user_id=?", (project_id, user["id"])).fetchone()
    if not row:
        conn.close()
        raise HTTPException(404, "Project not found")
    conn.close()
    folder_path = get_project_folder(user["id"], row["folder_name"])
    files = []
    if os.path.exists(folder_path):
        for fname in sorted(os.listdir(folder_path)):
            fpath = os.path.join(folder_path, fname)
            if os.path.isfile(fpath):
                files.append({
                    "name": fname,
                    "size": os.path.getsize(fpath),
                    "modified": datetime.fromtimestamp(os.path.getmtime(fpath)).isoformat()
                })
    return files


@app.get("/api/projects/{project_id}/file/{filename}")
async def get_file_content(project_id: int, filename: str, user=Depends(get_current_user)):
    conn = get_db()
    row = conn.execute("SELECT * FROM projects WHERE id=? AND user_id=?", (project_id, user["id"])).fetchone()
    if not row:
        conn.close()
        raise HTTPException(404, "Project not found")
    conn.close()
    filename = os.path.basename(filename)
    file_path = os.path.join(get_project_folder(user["id"], row["folder_name"]), filename)
    if not os.path.exists(file_path):
        raise HTTPException(404, "File not found")
    with open(file_path, "r", errors="replace") as f:
        content = f.read()
    return {"name": filename, "content": content}


@app.post("/api/projects/{project_id}/publish")
async def publish_project(project_id: int, user=Depends(get_current_user)):
    conn = get_db()
    row = conn.execute("SELECT * FROM projects WHERE id=? AND user_id=?", (project_id, user["id"])).fetchone()
    if not row:
        conn.close()
        raise HTTPException(404, "Project not found")
    conn.execute("UPDATE projects SET is_published=1, publish_date=? WHERE id=?",
                 (datetime.now().isoformat(), project_id))
    conn.commit()
    conn.close()
    return {"message": "Project published successfully"}


@app.post("/api/projects/{project_id}/unpublish")
async def unpublish_project(project_id: int, user=Depends(get_current_user)):
    conn = get_db()
    row = conn.execute("SELECT * FROM projects WHERE id=? AND user_id=?", (project_id, user["id"])).fetchone()
    if not row:
        conn.close()
        raise HTTPException(404, "Project not found")
    conn.execute("UPDATE projects SET is_published=0 WHERE id=?", (project_id,))
    conn.commit()
    conn.close()
    return {"message": "Project unpublished"}


@app.post("/api/projects/{project_id}/like")
async def toggle_like(project_id: int, user=Depends(get_current_user)):
    conn = get_db()
    existing = conn.execute(
        "SELECT id FROM likes WHERE user_id=? AND project_id=?", (user["id"], project_id)
    ).fetchone()
    if existing:
        conn.execute("DELETE FROM likes WHERE user_id=? AND project_id=?", (user["id"], project_id))
        conn.execute("UPDATE projects SET likes_count=MAX(0, likes_count-1) WHERE id=?", (project_id,))
        liked = False
    else:
        conn.execute("INSERT INTO likes (user_id, project_id) VALUES (?, ?)", (user["id"], project_id))
        conn.execute("UPDATE projects SET likes_count=likes_count+1 WHERE id=?", (project_id,))
        liked = True
    conn.commit()
    count = conn.execute("SELECT likes_count FROM projects WHERE id=?", (project_id,)).fetchone()
    conn.close()
    return {"liked": liked, "likes_count": count["likes_count"] if count else 0}


@app.get("/api/projects/{project_id}/comments")
async def get_comments(project_id: int):
    conn = get_db()
    rows = conn.execute("""
        SELECT c.*, u.username FROM comments c
        JOIN users u ON c.user_id=u.id
        WHERE c.project_id=? ORDER BY c.created_at DESC
    """, (project_id,)).fetchall()
    conn.close()
    return [dict(r) for r in rows]


@app.post("/api/projects/{project_id}/comments")
async def add_comment(project_id: int, data: CommentModel, user=Depends(get_current_user)):
    if not data.content.strip():
        raise HTTPException(400, "Comment cannot be empty")
    conn = get_db()
    conn.execute("INSERT INTO comments (user_id, project_id, content) VALUES (?, ?, ?)",
                 (user["id"], project_id, data.content.strip()))
    conn.commit()
    row = conn.execute("""
        SELECT c.*, u.username FROM comments c JOIN users u ON c.user_id=u.id
        WHERE c.id=last_insert_rowid()
    """).fetchone()
    conn.close()
    return dict(row)


@app.delete("/api/comments/{comment_id}")
async def delete_comment(comment_id: int, user=Depends(get_current_user)):
    conn = get_db()
    row = conn.execute("SELECT * FROM comments WHERE id=? AND user_id=?", (comment_id, user["id"])).fetchone()
    if not row:
        conn.close()
        raise HTTPException(404, "Comment not found or not yours")
    conn.execute("DELETE FROM comments WHERE id=?", (comment_id,))
    conn.commit()
    conn.close()
    return {"message": "Comment deleted"}


# ── PUBLIC / EXPLORE ───────────────────────────────────────────────────────────

@app.get("/api/public/projects")
async def browse_projects(search: str = "", sort: str = "recent", tag: str = "", page: int = 1, limit: int = 12):
    conn = get_db()
    where = ["p.is_published=1"]
    params: list = []
    if search:
        where.append("(p.name LIKE ? OR p.description LIKE ? OR u.username LIKE ?)")
        params += [f"%{search}%", f"%{search}%", f"%{search}%"]
    if tag:
        where.append("p.tags LIKE ?")
        params.append(f"%{tag}%")
    where_sql = " AND ".join(where)
    order_map = {"recent": "p.publish_date DESC", "popular": "p.downloads DESC",
                 "liked": "p.likes_count DESC", "views": "p.views DESC"}
    order = order_map.get(sort, "p.publish_date DESC")
    offset = (page - 1) * limit
    total = conn.execute(
        f"SELECT COUNT(*) FROM projects p JOIN users u ON p.user_id=u.id WHERE {where_sql}", params
    ).fetchone()[0]
    rows = conn.execute(
        f"""SELECT p.*, u.username FROM projects p JOIN users u ON p.user_id=u.id
        WHERE {where_sql} ORDER BY {order} LIMIT ? OFFSET ?""",
        params + [limit, offset]
    ).fetchall()
    conn.close()
    return {"projects": [dict(r) for r in rows], "total": total,
            "page": page, "pages": max(1, (total + limit - 1) // limit)}


@app.get("/api/public/projects/{project_id}")
async def get_public_project(project_id: int, user=Depends(get_optional_user)):
    conn = get_db()
    row = conn.execute("""
        SELECT p.*, u.username, u.bio as user_bio, u.website as user_website
        FROM projects p JOIN users u ON p.user_id=u.id
        WHERE p.id=? AND p.is_published=1
    """, (project_id,)).fetchone()
    if not row:
        conn.close()
        raise HTTPException(404, "Project not found")
    conn.execute("UPDATE projects SET views=views+1 WHERE id=?", (project_id,))
    user_liked = False
    if user:
        liked = conn.execute("SELECT id FROM likes WHERE user_id=? AND project_id=?",
                             (user["id"], project_id)).fetchone()
        user_liked = bool(liked)
    conn.commit()
    conn.close()
    result = dict(row)
    result["user_liked"] = user_liked
    return result


@app.get("/api/public/files/{project_id}/{filename}")
async def get_public_file(project_id: int, filename: str):
    conn = get_db()
    row = conn.execute("SELECT * FROM projects WHERE id=? AND is_published=1", (project_id,)).fetchone()
    if not row:
        conn.close()
        raise HTTPException(404, "Project not found")
    conn.close()
    filename = os.path.basename(filename)
    file_path = os.path.join(get_project_folder(row["user_id"], row["folder_name"]), filename)
    if not os.path.exists(file_path):
        raise HTTPException(404, "File not found")
    with open(file_path, "r", errors="replace") as f:
        content = f.read()
    return {"name": filename, "content": content}


@app.get("/api/stats")
async def get_platform_stats():
    conn = get_db()
    stats = {
        "total_users": conn.execute("SELECT COUNT(*) FROM users").fetchone()[0],
        "total_projects": conn.execute("SELECT COUNT(*) FROM projects").fetchone()[0],
        "published_projects": conn.execute("SELECT COUNT(*) FROM projects WHERE is_published=1").fetchone()[0],
        "total_downloads": conn.execute("SELECT COALESCE(SUM(downloads),0) FROM projects").fetchone()[0],
    }
    conn.close()
    return stats


@app.get("/api/users/{username}")
async def get_user_profile(username: str, user=Depends(get_optional_user)):
    conn = get_db()
    profile = conn.execute(
        "SELECT id, username, bio, website, created_at FROM users WHERE LOWER(username)=?",
        (username.lower(),)
    ).fetchone()
    if not profile:
        conn.close()
        raise HTTPException(404, "User not found")
    projects = conn.execute("""
        SELECT * FROM projects WHERE user_id=? AND is_published=1 ORDER BY publish_date DESC
    """, (profile["id"],)).fetchall()
    stats = conn.execute("""
        SELECT COUNT(*) as total_projects, COALESCE(SUM(downloads),0) as total_downloads,
               COALESCE(SUM(likes_count),0) as total_likes, COALESCE(SUM(views),0) as total_views
        FROM projects WHERE user_id=? AND is_published=1
    """, (profile["id"],)).fetchone()
    is_own = user and user["id"] == profile["id"]
    conn.close()
    return {"user": dict(profile), "projects": [dict(p) for p in projects],
            "stats": dict(stats), "is_own": is_own}


# ── AI GENERATE ────────────────────────────────────────────────────────────────

@app.post("/api/ai/generate")
async def ai_generate(data: AIGenerateModel):
    api_key = data.api_key or os.getenv("GROQ_API_KEY", "")
    if not api_key:
        raise HTTPException(400, "Groq API key required. Set it in Settings or provide it in the request.")
    system_msg = ("You are a Chrome Extension generator.\n"
                  "Return ONLY two code blocks: manifest.json (Manifest V3) and inject.js.\n"
                  "No explanation. No markdown outside code blocks.")
    user_msg = f"""Create a Chrome extension:
Name: {data.extension_name}
Version: {data.version}
Description: {data.description}
Author: {data.author}

User Request:
{data.prompt}

OUTPUT FORMAT ONLY:
```manifest.json
{{}}
```
```inject.js
console.log("loaded");
```"""
    async with httpx.AsyncClient(timeout=60.0) as client:
        resp = await client.post(
            "https://api.groq.com/openai/v1/chat/completions",
            headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
            json={"model": "llama-3.3-70b-versatile", "temperature": 0.2, "max_tokens": 8000,
                  "messages": [{"role": "system", "content": system_msg}, {"role": "user", "content": user_msg}]}
        )
    if resp.status_code != 200:
        raise HTTPException(500, f"Groq API error: {resp.text[:200]}")
    result = resp.json()
    return {"response": result["choices"][0]["message"]["content"]}


# ── ICON SERVING ───────────────────────────────────────────────────────────────

@app.get("/ext-icon/{user_id}/{folder_name}/{filename}")
async def serve_ext_icon(user_id: int, folder_name: str, filename: str):
    filename = os.path.basename(filename)
    folder_name = os.path.basename(folder_name)
    file_path = os.path.join(EXTENSIONS_DIR, str(user_id), folder_name, filename)
    if not os.path.exists(file_path):
        raise HTTPException(404, "Icon not found")
    import mimetypes
    ct, _ = mimetypes.guess_type(file_path)
    from fastapi.responses import FileResponse as FR
    return FR(file_path, media_type=ct or "image/png")


# ── HEALTH ─────────────────────────────────────────────────────────────────────

@app.get("/api/health")
async def health():
    return {"status": "ok", "time": datetime.now().isoformat()}


# ── STATIC FILES (must be last) ────────────────────────────────────────────────

static_dir = os.path.join(os.path.dirname(__file__), "static")
os.makedirs(static_dir, exist_ok=True)
app.mount("/", StaticFiles(directory=static_dir, html=True), name="static")


if __name__ == "__main__":
    import uvicorn
    port = int(os.getenv("PORT", "8080"))
    uvicorn.run("main:app", host="0.0.0.0", port=port, reload=True)
