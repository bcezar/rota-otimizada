from __future__ import annotations

import json
import secrets
import uuid
from datetime import datetime, timezone
from typing import Optional

import httpx

from app.config import settings

_SIGNUP_SOURCE = "findmyroute" if settings.locale == "en-US" else "rotaotimizada"

_routes: dict[str, dict] = {}
_saved:  dict[str, dict] = {}
_users:  dict[str, dict] = {}   # email → user dict
_sessions: dict[str, str] = {}  # token → user_id


def _turso_configured() -> bool:
    return bool(settings.turso_database_url and settings.turso_auth_token)


def _turso_http_url() -> str:
    return (settings.turso_database_url or "").replace("libsql://", "https://")


def _turso_arg(value) -> dict:
    if value is None:
        return {"type": "null", "value": None}
    return {"type": "text", "value": str(value)}


async def _execute(sql: str, args: list | None = None, *, ignore_error: bool = False) -> dict:
    stmt: dict = {"sql": sql}
    if args:
        stmt["args"] = [_turso_arg(a) for a in args]
    try:
        async with httpx.AsyncClient() as client:
            r = await client.post(
                f"{_turso_http_url()}/v2/pipeline",
                headers={"Authorization": f"Bearer {settings.turso_auth_token}"},
                json={"requests": [{"type": "execute", "stmt": stmt}, {"type": "close"}]},
                timeout=10,
            )
            r.raise_for_status()
            return r.json()["results"][0]["response"]["result"]
    except Exception:
        if ignore_error:
            return {}
        raise


def _cell(cell):
    if isinstance(cell, dict):
        v = cell.get("value")
        return None if v in (None, "null", "None") else v
    return None if cell in (None, "null", "None") else cell


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


async def init_db() -> None:
    if not _turso_configured():
        return
    await _execute(
        "CREATE TABLE IF NOT EXISTS routes "
        "(code TEXT PRIMARY KEY, state TEXT NOT NULL, created_at TEXT DEFAULT (datetime('now')))"
    )
    await _execute(
        "CREATE TABLE IF NOT EXISTS saved_routes "
        "(code TEXT PRIMARY KEY, name TEXT, result TEXT NOT NULL, inputs TEXT NOT NULL, "
        "user_id TEXT, created_at TEXT DEFAULT (datetime('now')))"
    )
    await _execute(
        "CREATE TABLE IF NOT EXISTS users "
        "(id TEXT PRIMARY KEY, email TEXT UNIQUE NOT NULL, created_at TEXT DEFAULT (datetime('now')))"
    )
    await _execute(
        "CREATE TABLE IF NOT EXISTS sessions "
        "(token TEXT PRIMARY KEY, user_id TEXT NOT NULL, created_at TEXT DEFAULT (datetime('now')))"
    )
    await _execute(
        "CREATE TABLE IF NOT EXISTS magic_tokens "
        "(token TEXT PRIMARY KEY, user_id TEXT NOT NULL, "
        "expires_at TEXT NOT NULL, used_at TEXT DEFAULT NULL)"
    )
    await _execute(
        "CREATE TABLE IF NOT EXISTS oauth_states "
        "(state TEXT PRIMARY KEY, expires_at TEXT NOT NULL)"
    )
    await _execute(
        "CREATE TABLE IF NOT EXISTS geocoding_cache "
        "(address TEXT PRIMARY KEY, lat REAL NOT NULL, lng REAL NOT NULL, "
        "cached_at TEXT NOT NULL DEFAULT (datetime('now')))"
    )
    await _execute(
        "CREATE TABLE IF NOT EXISTS feedback "
        "(id TEXT PRIMARY KEY, user_id TEXT, signup_source TEXT, rating INTEGER NOT NULL, "
        "comment TEXT, created_at TEXT NOT NULL DEFAULT (datetime('now')))"
    )
    await _execute(
        "CREATE TABLE IF NOT EXISTS contact_messages "
        "(id TEXT PRIMARY KEY, user_id TEXT, email TEXT NOT NULL, message TEXT NOT NULL, "
        "created_at TEXT NOT NULL DEFAULT (datetime('now')))"
    )
    await _execute(
        "CREATE TABLE IF NOT EXISTS coupons "
        "(code TEXT PRIMARY KEY, max_redemptions INTEGER NOT NULL, "
        "expires_at TEXT NOT NULL, active INTEGER NOT NULL DEFAULT 1, "
        "created_at TEXT NOT NULL DEFAULT (datetime('now')))"
    )
    await _execute(
        "CREATE TABLE IF NOT EXISTS coupon_redemptions "
        "(code TEXT NOT NULL, user_id TEXT NOT NULL, cpf_cnpj TEXT NOT NULL, "
        "created_at TEXT NOT NULL DEFAULT (datetime('now')), PRIMARY KEY (code, user_id))"
    )
    # grants_stop_limit must exist before the seed inserts below reference it
    await _execute("ALTER TABLE coupons ADD COLUMN grants_stop_limit INTEGER", ignore_error=True)
    # seed the launch coupons (idempotent — safe to re-run on every deploy)
    await _execute(
        "INSERT OR IGNORE INTO coupons (code, max_redemptions, expires_at) "
        "VALUES ('ROTAREDDIT', 20, '2026-10-01 02:59:59')"
    )
    await _execute(
        "INSERT OR IGNORE INTO coupons (code, max_redemptions, expires_at, grants_stop_limit) "
        "VALUES ('ROTAEXCLUSIVE', 10, '2026-10-01 02:59:59', 100)"
    )
    # prune stale + excess geocoding cache entries on every startup
    await _execute(
        "DELETE FROM geocoding_cache WHERE cached_at < datetime('now', '-30 days')"
    )
    await _execute(
        "DELETE FROM geocoding_cache WHERE address NOT IN "
        "(SELECT address FROM geocoding_cache ORDER BY cached_at DESC LIMIT 10000)"
    )
    # migrations (idempotent)
    await _execute("ALTER TABLE saved_routes ADD COLUMN user_id TEXT", ignore_error=True)
    await _execute("ALTER TABLE users ADD COLUMN is_pro INTEGER DEFAULT 0", ignore_error=True)
    await _execute("ALTER TABLE users ADD COLUMN email_verified INTEGER DEFAULT 0", ignore_error=True)
    await _execute("ALTER TABLE users ADD COLUMN google_sub TEXT", ignore_error=True)
    await _execute("ALTER TABLE users ADD COLUMN name TEXT", ignore_error=True)
    await _execute("ALTER TABLE users ADD COLUMN picture_url TEXT", ignore_error=True)
    await _execute("ALTER TABLE sessions ADD COLUMN expires_at TEXT", ignore_error=True)
    await _execute("ALTER TABLE users ADD COLUMN asaas_customer_id TEXT", ignore_error=True)
    await _execute("ALTER TABLE users ADD COLUMN pro_expires_at TEXT", ignore_error=True)
    await _execute("ALTER TABLE users ADD COLUMN stripe_customer_id TEXT", ignore_error=True)
    await _execute("ALTER TABLE users ADD COLUMN signup_source TEXT", ignore_error=True)
    await _execute("ALTER TABLE users ADD COLUMN exclusive_until TEXT", ignore_error=True)
    await _execute("ALTER TABLE users ADD COLUMN marketing_opt_out INTEGER DEFAULT 0", ignore_error=True)


# ── Short links ─────────────────────────────────────────────────────────────

async def save_route(state: dict) -> str:
    code = secrets.token_urlsafe(6)
    if _turso_configured():
        await _execute(
            "INSERT INTO routes (code, state) VALUES (?, ?)",
            [code, json.dumps(state)],
        )
    else:
        while code in _routes:
            code = secrets.token_urlsafe(6)
        _routes[code] = state
    return code


async def get_route(code: str) -> dict | None:
    if _turso_configured():
        r = await _execute("SELECT state FROM routes WHERE code = ?", [code])
        rows = r.get("rows", [])
        if rows:
            return json.loads(_cell(rows[0][0]))
        return None
    return _routes.get(code)


# ── Saved routes ─────────────────────────────────────────────────────────────

async def save_result(name: str, result_dict: dict, inputs_dict: dict, user_id: str) -> str:
    code = secrets.token_urlsafe(6)
    if _turso_configured():
        await _execute(
            "INSERT INTO saved_routes (code, name, result, inputs, user_id) VALUES (?, ?, ?, ?, ?)",
            [code, name, json.dumps(result_dict), json.dumps(inputs_dict), user_id],
        )
    else:
        _saved[code] = {"name": name, "result": result_dict, "inputs": inputs_dict, "user_id": user_id}
    return code


async def get_result(code: str) -> dict | None:
    if _turso_configured():
        r = await _execute("SELECT result FROM saved_routes WHERE code = ?", [code])
        rows = r.get("rows", [])
        if rows:
            return json.loads(_cell(rows[0][0]))
        return None
    entry = _saved.get(code)
    return entry["result"] if entry else None


async def list_results(user_id: str) -> list[dict]:
    if _turso_configured():
        r = await _execute(
            "SELECT code, name, result, inputs, created_at FROM saved_routes "
            "WHERE user_id = ? ORDER BY created_at DESC LIMIT 50",
            [user_id],
        )
        rows = r.get("rows", [])
        return [
            {
                "code":       _cell(row[0]),
                "name":       _cell(row[1]),
                "result":     json.loads(_cell(row[2])),
                "inputs":     json.loads(_cell(row[3])),
                "created_at": _cell(row[4]),
            }
            for row in rows
        ]
    return [
        {"code": k, "name": v["name"], "result": v["result"], "inputs": v["inputs"], "created_at": None}
        for k, v in _saved.items()
        if v.get("user_id") == user_id
    ]


async def delete_result(code: str, user_id: str) -> None:
    if _turso_configured():
        await _execute(
            "DELETE FROM saved_routes WHERE code = ? AND user_id = ?",
            [code, user_id],
        )
    elif code in _saved and _saved[code].get("user_id") == user_id:
        del _saved[code]


# ── Users & sessions ─────────────────────────────────────────────────────────

def _row_to_user(row: list) -> dict:
    def _int(v) -> int:
        try:
            return int(v) if v is not None else 0
        except (TypeError, ValueError):
            return 0

    return {
        "id":             _cell(row[0]),
        "email":          _cell(row[1]),
        "is_pro":         bool(_int(_cell(row[2]))),
        "email_verified": bool(_int(_cell(row[3]))),
        "name":           _cell(row[4]) if len(row) > 4 else None,
        "picture_url":      _cell(row[5]) if len(row) > 5 else None,
        "asaas_customer_id": _cell(row[6]) if len(row) > 6 else None,
        "stripe_customer_id": _cell(row[7]) if len(row) > 7 else None,
        "is_exclusive":    bool(_int(_cell(row[8]))) if len(row) > 8 else False,
        "exclusive_until": _cell(row[9]) if len(row) > 9 else None,
    }


async def find_or_create_user(email: str) -> dict:
    if _turso_configured():
        r = await _execute(
            "SELECT id, email, is_pro, email_verified, name, picture_url "
            "FROM users WHERE email = ?", [email]
        )
        rows = r.get("rows", [])
        if rows:
            return _row_to_user(rows[0])
        user_id = str(uuid.uuid4())
        await _execute(
            "INSERT INTO users (id, email, created_at, signup_source) VALUES (?, ?, ?, ?)",
            [user_id, email, _now_iso(), _SIGNUP_SOURCE],
        )
        return {"id": user_id, "email": email, "is_pro": False, "email_verified": False,
                "name": None, "picture_url": None}
    for user in _users.values():
        if user["email"] == email:
            return user
    user_id = str(uuid.uuid4())
    user = {"id": user_id, "email": email, "is_pro": False, "email_verified": False,
            "name": None, "picture_url": None}
    _users[user_id] = user
    return user


async def find_or_create_user_google(email: str, google_sub: str,
                                      name: Optional[str], picture_url: Optional[str]) -> dict:
    """Upsert user by google_sub; links to existing email account if present."""
    if _turso_configured():
        # Try by google_sub first
        r = await _execute(
            "SELECT id, email, is_pro, email_verified, name, picture_url "
            "FROM users WHERE google_sub = ?", [google_sub]
        )
        rows = r.get("rows", [])
        if rows:
            return _row_to_user(rows[0])
        # Try by email (link existing magic-link account)
        r = await _execute(
            "SELECT id, email, is_pro, email_verified, name, picture_url "
            "FROM users WHERE email = ?", [email]
        )
        rows = r.get("rows", [])
        if rows:
            uid = _cell(rows[0][0])
            await _execute(
                "UPDATE users SET google_sub=?, name=?, picture_url=?, email_verified=1 WHERE id=?",
                [google_sub, name, picture_url, uid],
            )
            user = _row_to_user(rows[0])
            user.update({"google_sub": google_sub, "name": name,
                         "picture_url": picture_url, "email_verified": True})
            return user
        # New user
        user_id = str(uuid.uuid4())
        await _execute(
            "INSERT INTO users (id, email, google_sub, name, picture_url, email_verified, created_at, signup_source) "
            "VALUES (?, ?, ?, ?, ?, 1, ?, ?)",
            [user_id, email, google_sub, name, picture_url, _now_iso(), _SIGNUP_SOURCE],
        )
        return {"id": user_id, "email": email, "is_pro": False, "email_verified": True,
                "name": name, "picture_url": picture_url}
    # in-memory fallback
    for user in _users.values():
        if user.get("google_sub") == google_sub or user["email"] == email:
            user.update({"google_sub": google_sub, "name": name,
                         "picture_url": picture_url, "email_verified": True})
            return user
    user_id = str(uuid.uuid4())
    user = {"id": user_id, "email": email, "is_pro": False, "email_verified": True,
            "google_sub": google_sub, "name": name, "picture_url": picture_url}
    _users[user_id] = user
    return user


async def get_user_by_id(user_id: str) -> dict | None:
    if _turso_configured():
        r = await _execute(
            "SELECT id, email, is_pro, email_verified, name, picture_url "
            "FROM users WHERE id = ?", [user_id]
        )
        rows = r.get("rows", [])
        return _row_to_user(rows[0]) if rows else None
    return _users.get(user_id)


# ── Marketing e-mail opt-out ─────────────────────────────────────────────────

async def list_marketing_recipients() -> list[dict]:
    if _turso_configured():
        r = await _execute(
            "SELECT id, email, name, signup_source FROM users "
            "WHERE marketing_opt_out IS NULL OR marketing_opt_out = 0 "
            "ORDER BY created_at ASC"
        )
        rows = r.get("rows", [])
        return [
            {"id": _cell(row[0]), "email": _cell(row[1]), "name": _cell(row[2]), "signup_source": _cell(row[3])}
            for row in rows
        ]
    # dict preserves insertion order, which mirrors created_at for the in-memory fallback
    return [
        {"id": uid, "email": u["email"], "name": u.get("name"), "signup_source": u.get("signup_source")}
        for uid, u in _users.items()
        if not u.get("marketing_opt_out")
    ]


async def set_marketing_opt_out(user_id: str) -> None:
    if _turso_configured():
        await _execute("UPDATE users SET marketing_opt_out = 1 WHERE id = ?", [user_id])
    elif user_id in _users:
        _users[user_id]["marketing_opt_out"] = True


async def create_session(user_id: str) -> str:
    token = secrets.token_urlsafe(32)
    from datetime import timedelta
    expires_at = (datetime.now(timezone.utc) + timedelta(days=30)).strftime("%Y-%m-%d %H:%M:%S")
    if _turso_configured():
        await _execute(
            "INSERT INTO sessions (token, user_id, expires_at) VALUES (?, ?, ?)",
            [token, user_id, expires_at],
        )
    else:
        _sessions[token] = user_id
    return token


async def get_user_by_token(token: str) -> dict | None:
    if _turso_configured():
        r = await _execute(
            "SELECT u.id, u.email, "
            "CASE WHEN u.is_pro = 1 AND (u.pro_expires_at IS NULL OR u.pro_expires_at > datetime('now')) THEN 1 ELSE 0 END AS is_pro, "
            "u.email_verified, u.name, u.picture_url, u.asaas_customer_id, u.stripe_customer_id, "
            "CASE WHEN u.exclusive_until IS NOT NULL AND u.exclusive_until > datetime('now') THEN 1 ELSE 0 END AS is_exclusive, "
            "u.exclusive_until "
            "FROM sessions s JOIN users u ON s.user_id = u.id "
            "WHERE s.token = ? AND (s.expires_at IS NULL OR s.expires_at > datetime('now'))",
            [token],
        )
        rows = r.get("rows", [])
        if rows:
            return _row_to_user(rows[0])
        return None
    user_id = _sessions.get(token)
    if user_id:
        return _users.get(user_id)
    return None


async def delete_session(token: str) -> None:
    if _turso_configured():
        await _execute("DELETE FROM sessions WHERE token = ?", [token])
    else:
        _sessions.pop(token, None)


# ── Magic link tokens ─────────────────────────────────────────────────────────

async def create_magic_token(user_id: str) -> str:
    from datetime import timedelta
    token = secrets.token_urlsafe(32)
    expires_at = (datetime.now(timezone.utc) + timedelta(minutes=15)).strftime("%Y-%m-%d %H:%M:%S")
    if _turso_configured():
        await _execute(
            "INSERT INTO magic_tokens (token, user_id, expires_at) VALUES (?, ?, ?)",
            [token, user_id, expires_at],
        )
    else:
        _sessions["magic:" + token] = {"user_id": user_id, "expires_at": expires_at}
    return token


async def consume_magic_token(token: str) -> Optional[str]:
    """Returns user_id if token is valid and unused; marks it used. Returns None otherwise."""
    if _turso_configured():
        r = await _execute(
            "SELECT user_id FROM magic_tokens "
            "WHERE token = ? AND used_at IS NULL AND expires_at > datetime('now')",
            [token],
        )
        rows = r.get("rows", [])
        if not rows:
            return None
        user_id = _cell(rows[0][0])
        await _execute(
            "UPDATE magic_tokens SET used_at = ? WHERE token = ?",
            [_now_iso(), token],
        )
        return user_id
    entry = _sessions.get("magic:" + token)
    if entry and not entry.get("used_at"):
        entry["used_at"] = _now_iso()
        return entry["user_id"]
    return None


async def mark_email_verified(user_id: str) -> None:
    if _turso_configured():
        await _execute("UPDATE users SET email_verified = 1 WHERE id = ?", [user_id])
    elif user_id in _users:
        _users[user_id]["email_verified"] = True


# ── OAuth states (CSRF) ───────────────────────────────────────────────────────

async def create_oauth_state() -> str:
    from datetime import timedelta
    state = secrets.token_urlsafe(16)
    expires_at = (datetime.now(timezone.utc) + timedelta(minutes=10)).strftime("%Y-%m-%d %H:%M:%S")
    if _turso_configured():
        await _execute(
            "INSERT INTO oauth_states (state, expires_at) VALUES (?, ?)",
            [state, expires_at],
        )
    else:
        _sessions["oauth_state:" + state] = expires_at
    return state


async def consume_oauth_state(state: str) -> bool:
    """Returns True if state is valid; deletes it."""
    if _turso_configured():
        r = await _execute(
            "SELECT state FROM oauth_states WHERE state = ? AND expires_at > datetime('now')",
            [state],
        )
        rows = r.get("rows", [])
        if not rows:
            return False
        await _execute("DELETE FROM oauth_states WHERE state = ?", [state])
        return True
    key = "oauth_state:" + state
    if key in _sessions:
        del _sessions[key]
        return True
    return False


# ── Billing / Asaas ───────────────────────────────────────────────────────────

async def set_user_pro(user_id: str, is_pro: bool) -> None:
    value = 1 if is_pro else 0
    if _turso_configured():
        await _execute("UPDATE users SET is_pro = ? WHERE id = ?", [value, user_id])
    elif user_id in _users:
        _users[user_id]["is_pro"] = is_pro


async def set_pro_expires_at(user_id: str, expires_at: Optional[str]) -> None:
    if _turso_configured():
        await _execute(
            "UPDATE users SET pro_expires_at = ? WHERE id = ?",
            [expires_at, user_id],
        )
    elif user_id in _users:
        _users[user_id]["pro_expires_at"] = expires_at


async def set_exclusive_until(user_id: str, until: Optional[str]) -> None:
    if _turso_configured():
        await _execute(
            "UPDATE users SET exclusive_until = ? WHERE id = ?",
            [until, user_id],
        )
    elif user_id in _users:
        _users[user_id]["exclusive_until"] = until


async def set_asaas_customer_id(user_id: str, customer_id: str) -> None:
    if _turso_configured():
        await _execute(
            "UPDATE users SET asaas_customer_id = ? WHERE id = ?",
            [customer_id, user_id],
        )
    elif user_id in _users:
        _users[user_id]["asaas_customer_id"] = customer_id


async def get_user_by_asaas_customer(customer_id: str) -> Optional[dict]:
    if _turso_configured():
        r = await _execute(
            "SELECT id, email, is_pro, email_verified, name, picture_url "
            "FROM users WHERE asaas_customer_id = ?",
            [customer_id],
        )
        rows = r.get("rows", [])
        return _row_to_user(rows[0]) if rows else None
    for user in _users.values():
        if user.get("asaas_customer_id") == customer_id:
            return user
    return None


async def set_stripe_customer_id(user_id: str, customer_id: str) -> None:
    if _turso_configured():
        await _execute(
            "UPDATE users SET stripe_customer_id = ? WHERE id = ?",
            [customer_id, user_id],
        )
    elif user_id in _users:
        _users[user_id]["stripe_customer_id"] = customer_id


async def get_user_by_stripe_customer(customer_id: str) -> Optional[dict]:
    if _turso_configured():
        r = await _execute(
            "SELECT id, email, is_pro, email_verified, name, picture_url "
            "FROM users WHERE stripe_customer_id = ?",
            [customer_id],
        )
        rows = r.get("rows", [])
        return _row_to_user(rows[0]) if rows else None
    for user in _users.values():
        if user.get("stripe_customer_id") == customer_id:
            return user
    return None


# ── Geocoding cache ──────────────────────────────────────────────────────────

async def get_geocoding_cache_batch(
    addresses: list[str],
) -> dict[str, tuple[float, float]]:
    """Return {address: (lat, lng)} for all addresses found in Turso cache."""
    if not _turso_configured() or not addresses:
        return {}
    placeholders = ",".join(["?"] * len(addresses))
    r = await _execute(
        f"SELECT address, lat, lng FROM geocoding_cache "
        f"WHERE address IN ({placeholders}) "
        f"AND cached_at > datetime('now', '-30 days')",
        addresses,
    )
    result: dict[str, tuple[float, float]] = {}
    for row in r.get("rows", []):
        addr = _cell(row[0])
        lat  = _cell(row[1])
        lng  = _cell(row[2])
        if addr and lat is not None and lng is not None:
            try:
                result[addr] = (float(lat), float(lng))
            except (TypeError, ValueError):
                pass
    return result


async def set_geocoding_cache(address: str, lat: float, lng: float) -> None:
    """Upsert a geocoding result. Fire-and-forget safe."""
    if not _turso_configured():
        return
    try:
        await _execute(
            "INSERT OR REPLACE INTO geocoding_cache (address, lat, lng, cached_at) "
            "VALUES (?, ?, ?, datetime('now'))",
            [address, str(lat), str(lng)],
        )
    except Exception:
        pass


# ── Feedback ─────────────────────────────────────────────────────────────────

async def save_feedback(user_id: Optional[str], rating: int, comment: Optional[str]) -> None:
    if not _turso_configured():
        return
    await _execute(
        "INSERT INTO feedback (id, user_id, signup_source, rating, comment) VALUES (?, ?, ?, ?, ?)",
        [str(uuid.uuid4()), user_id, _SIGNUP_SOURCE, rating, comment],
    )


# ── Contact messages ─────────────────────────────────────────────────────────

async def save_contact_message(user_id: Optional[str], email: str, message: str) -> None:
    if not _turso_configured():
        return
    await _execute(
        "INSERT INTO contact_messages (id, user_id, email, message) VALUES (?, ?, ?, ?)",
        [str(uuid.uuid4()), user_id, email, message],
    )


# ── Coupons ──────────────────────────────────────────────────────────────────

async def get_coupon(code: str) -> Optional[dict]:
    if not _turso_configured():
        return None
    r = await _execute(
        "SELECT code, max_redemptions, expires_at, active, grants_stop_limit FROM coupons WHERE code = ?",
        [code],
    )
    rows = r.get("rows", [])
    if not rows:
        return None
    row = rows[0]
    grants_stop_limit = _cell(row[4])
    return {
        "code":               _cell(row[0]),
        "max_redemptions":    int(_cell(row[1])),
        "expires_at":         _cell(row[2]),
        "active":             bool(int(_cell(row[3]))),
        "grants_stop_limit":  int(grants_stop_limit) if grants_stop_limit is not None else None,
    }


async def count_coupon_redemptions(code: str) -> int:
    if not _turso_configured():
        return 0
    r = await _execute(
        "SELECT COUNT(*) FROM coupon_redemptions WHERE code = ?",
        [code],
    )
    rows = r.get("rows", [])
    return int(_cell(rows[0][0])) if rows else 0


async def cpf_has_redeemed_coupon(cpf_cnpj: str) -> bool:
    if not _turso_configured():
        return False
    r = await _execute(
        "SELECT 1 FROM coupon_redemptions WHERE cpf_cnpj = ? LIMIT 1",
        [cpf_cnpj],
    )
    return len(r.get("rows", [])) > 0


async def record_coupon_redemption(code: str, user_id: str, cpf_cnpj: str) -> None:
    if not _turso_configured():
        return
    await _execute(
        "INSERT INTO coupon_redemptions (code, user_id, cpf_cnpj) VALUES (?, ?, ?)",
        [code, user_id, cpf_cnpj],
    )
