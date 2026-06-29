/* KNI Platform — API Client */

const BASE = '';

async function getApiKey() {
  const res = await fetch("https://kni-org.up.railway.app/api/key");
  const data = await res.json();
  return data.key;
}

function getToken() { return localStorage.getItem('kni_token'); }
function getUser()  { try { return JSON.parse(localStorage.getItem('kni_user') || 'null'); } catch { return null; } }
function setAuth(token, user) { localStorage.setItem('kni_token', token); localStorage.setItem('kni_user', JSON.stringify(user)); }
function clearAuth()          { localStorage.removeItem('kni_token'); localStorage.removeItem('kni_user'); }
function getGroqKey()         {  return getApiKey() || ''; }

async function apiFetch(method, path, body = null, isForm = false) {
  const token = getToken();
  const headers = {};
  if (token) headers['Authorization'] = `Bearer ${token}`;
  if (!isForm && body) headers['Content-Type'] = 'application/json';
  const opts = { method, headers };
  if (body) opts.body = isForm ? body : JSON.stringify(body);
  const res = await fetch(`${BASE}${path}`, opts);
  if (res.status === 204) return {};
  const data = await res.json().catch(() => ({ detail: 'Network error' }));
  if (!res.ok) throw new Error(data.detail || `HTTP ${res.status}`);
  return data;
}

const api = {
  get:    (p)    => apiFetch('GET', p),
  post:   (p, b) => apiFetch('POST', p, b),
  put:    (p, b) => apiFetch('PUT', p, b),
  del:    (p)    => apiFetch('DELETE', p),
  upload: (p, f) => apiFetch('POST', p, f, true),
  download: async (path, folderName) => {
    const token = getToken();
    const headers = token ? { Authorization: `Bearer ${token}` } : {};
    const res = await fetch(`${BASE}${path}`, { headers });
    if (!res.ok) throw new Error('Download failed');
    const data = await res.json();
    if (!window.showDirectoryPicker) throw new Error('Your browser does not support folder download');
    const parentDir = await window.showDirectoryPicker();
    const folderHandle = await parentDir.getDirectoryHandle(folderName || data.folder_name || 'extension', { create: true });
    for (const file of data.files) {
      const parts = file.name.split('/');
      let dirHandle = folderHandle;
      for (let i = 0; i < parts.length - 1; i++) {
        dirHandle = await dirHandle.getDirectoryHandle(parts[i], { create: true });
      }
      const fileHandle = await dirHandle.getFileHandle(parts[parts.length - 1], { create: true });
      const writable = await fileHandle.createWritable();
      if (file.encoding === 'base64') {
        const bytes = Uint8Array.from(atob(file.content), c => c.charCodeAt(0));
        await writable.write(bytes);
      } else {
        await writable.write(file.content);
      }
      await writable.close();
    }
  }
};

/* ── Toast ─────────────────────────────────────────── */
function toast(msg, type = 'info') {
  let container = document.getElementById('toast-container');
  if (!container) {
    container = document.createElement('div');
    container.id = 'toast-container';
    document.body.appendChild(container);
  }
  const el = document.createElement('div');
  el.className = `toast toast-${type}`;
  const icons = { success: '✓', error: '✕', info: 'ℹ' };
  el.innerHTML = `<span>${icons[type] || 'ℹ'}</span><span>${msg}</span>`;
  container.appendChild(el);
  setTimeout(() => {
    el.style.animation = 'slideOut .3s ease forwards';
    setTimeout(() => el.remove(), 300);
  }, 3500);
}

/* ── Helpers ───────────────────────────────────────── */
function fmtDate(d) {
  if (!d) return '—';
  return new Date(d).toLocaleDateString('en-US', { year:'numeric', month:'short', day:'numeric' });
}
function fmtNum(n) {
  if (!n) return '0';
  if (n >= 1000000) return (n/1000000).toFixed(1) + 'M';
  if (n >= 1000) return (n/1000).toFixed(1) + 'K';
  return String(n);
}
function fmtSize(bytes) {
  if (!bytes) return '0 B';
  const k = 1024;
  const sizes = ['B','KB','MB','GB'];
  const i = Math.floor(Math.log(bytes) / Math.log(k));
  return parseFloat((bytes / Math.pow(k,i)).toFixed(1)) + ' ' + sizes[i];
}
function fileIcon(name) {
  const ext = name.split('.').pop().toLowerCase();
  const map = { json:'📋', js:'📜', html:'🌐', css:'🎨', png:'🖼️', jpg:'🖼️',
    jpeg:'🖼️', svg:'🎭', gif:'🖼️', md:'📝', txt:'📄' };
  return map[ext] || '📄';
}
function timeAgo(d) {
  if (!d) return '';
  const diff = Date.now() - new Date(d).getTime();
  const m = Math.floor(diff/60000), h = Math.floor(diff/3600000), day = Math.floor(diff/86400000);
  if (day > 30) return fmtDate(d);
  if (day > 0) return `${day}d ago`;
  if (h > 0) return `${h}h ago`;
  if (m > 0) return `${m}m ago`;
  return 'just now';
}
function initials(name) {
  return (name || '?').charAt(0).toUpperCase();
}
function extractBlock(text, keyword) {
  if (!text) return '';
  if (keyword.includes('inject')) {
    const blocks = [...text.matchAll(/```([^\n`]*)\r?\n([\s\S]*?)```/g)];
    let best = '';
    for (const [, tag, body] of blocks) {
      const content = body.trim();
      if (!content) continue;
      if (/manifest/i.test(tag)) continue;
      if (/^\s*\{/.test(content)) continue;
      if (content.length > best.length) best = content;
    }
    if (best) return best;
  }
  const esc = keyword.replace(/[.*+?^${}()|[\]\\]/g, '\\$&').replace(/\\\./g, '\\.');
  const tagPatterns = keyword.includes('inject')
    ? [esc, 'javascript', 'js']
    : [esc, 'json'];
  for (const tag of tagPatterns) {
    const re = new RegExp('```\\s*' + tag + '\\s*\\r?\\n([\\s\\S]*?)```', 'i');
    const m = text.match(re);
    if (m && m[1].trim()) return m[1].trim();
  }
  if (keyword.includes('inject')) {
    const plain = text.replace(/^```[^\n]*\n?/gm, '').replace(/```$/gm, '').trim();
    if (plain && !plain.startsWith('{')) return plain;
  }
  return '';
}

/* ── Loading overlay ───────────────────────────────── */
function showLoading(btn, text = 'Loading...') {
  if (!btn) return;
  btn._origHTML = btn.innerHTML;
  btn.disabled = true;
  btn.innerHTML = `<span class="spinner"></span> ${text}`;
}
function hideLoading(btn) {
  if (!btn || !btn._origHTML) return;
  btn.disabled = false;
  btn.innerHTML = btn._origHTML;
}

/* ── Confirm dialog ────────────────────────────────── */
function confirm(msg) {
  return window.confirm(msg);
}

/* ── Redirect helpers ──────────────────────────────── */
function requireAuth() {
  if (!getToken()) { window.location.href = '/login.html'; return false; }
  return true;
}
function redirectIfAuth() {
  if (getToken()) { window.location.href = '/dashboard.html'; }
}
function logout() {
  api.post('/api/auth/logout').catch(()=>{});
  clearAuth();
  window.location.href = '/';
}

/* ── Navbar user state ─────────────────────────────── */
function renderNavUser() {
  const user = getUser();
  const guest = document.getElementById('nav-guest');
  const auth  = document.getElementById('nav-auth');
  const navName = document.getElementById('nav-username');
  const navAvatar = document.getElementById('nav-avatar');
  if (user && getToken()) {
    if (guest) guest.classList.add('hidden');
    if (auth)  auth.classList.remove('hidden');
    if (navName) navName.textContent = user.username;
    if (navAvatar) navAvatar.textContent = initials(user.username);
  } else {
    if (guest) guest.classList.remove('hidden');
    if (auth)  auth.classList.add('hidden');
  }
}
document.addEventListener('DOMContentLoaded', renderNavUser);
