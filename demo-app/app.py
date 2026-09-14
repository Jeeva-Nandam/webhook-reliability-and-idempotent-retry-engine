"""
demo-app: a minimal, self-contained example of "your own project" using
the Webhook Reliability Engine as a relay in front of it.

This deliberately mirrors the real-world pattern discussed in the README:

    Browser --(1)--> demo-app /posts --(2)--> Reliability Engine
                                                      |
                                                     (3) verifies signature,
                                                         dedupes by idempotency
                                                         key, retries on failure
                                                      |
    demo-app /internal/handle-event <--(4)-- Reliability Engine (forwarded)

(1) The browser never holds any secret -- it just sends content plus an
    idempotency key it generated once per logical action.
(2) demo-app's own backend (trusted, server-side) signs the request with
    the shared secret before forwarding to the engine's public /webhooks
    endpoint. This is the ONLY place a secret is used on this side.
(3) The engine does all the hard work: verifying, deduplicating by
    event_id, retrying on failure -- exactly as it does for a real
    provider like Stripe or GitHub.
(4) Once the engine considers the event successfully "processed", it
    forwards it here. This endpoint verifies the engine's own signature,
    and -- as a defense-in-depth idempotency check -- refuses to create a
    second post even if it were somehow called twice for the same event.

Storage is a plain in-memory list, on purpose -- this file is meant to be
read start to finish as a worked example, not used as-is in production.
"""
import hashlib
import hmac
import json
import os
import time

import httpx
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse

WEBHOOK_SECRET = os.environ.get("WEBHOOK_SECRET", "change-me-to-a-long-random-string")
ENGINE_URL = os.environ.get("ENGINE_URL", "http://localhost:8000/api/v1/webhooks")

app = FastAPI(title="Demo App (example integration)")

# In-memory "database" for this demo only.
POSTS: list[dict] = []
PROCESSED_EVENT_IDS: set[str] = set()


def _compute_signature(secret: str, raw_body: bytes) -> str:
    return hmac.new(secret.encode("utf-8"), raw_body, hashlib.sha256).hexdigest()


def _verify_signature(secret: str, raw_body: bytes, provided: str | None) -> bool:
    if not provided:
        return False
    expected = _compute_signature(secret, raw_body)
    return hmac.compare_digest(expected, provided)


@app.post("/posts")
async def create_post(request: Request):
    """
    Called directly by the browser. Signs the request server-side (the
    secret never reaches client-side JS) and forwards it to the
    reliability engine's public ingestion endpoint.
    """
    body = await request.json()
    content = body.get("content", "").strip()
    idempotency_key = body.get("idempotency_key", "").strip()

    if not content:
        raise HTTPException(status_code=400, detail="content is required")
    if not idempotency_key:
        raise HTTPException(status_code=400, detail="idempotency_key is required")

    engine_payload = json.dumps(
        {
            "event_id": idempotency_key,
            "event_type": "post.created",
            "payload": {"content": content},
        }
    ).encode()
    signature = _compute_signature(WEBHOOK_SECRET, engine_payload)

    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            resp = await client.post(
                ENGINE_URL,
                content=engine_payload,
                headers={"Content-Type": "application/json", "X-Webhook-Signature": signature},
            )
    except httpx.RequestError as exc:
        raise HTTPException(status_code=502, detail=f"could not reach reliability engine: {exc}") from exc

    return JSONResponse(status_code=resp.status_code, content=resp.json())


@app.post("/internal/handle-event")
async def handle_event(request: Request):
    """
    Called by the reliability engine once an event is verified and
    deduplicated. Verifies the engine's own signature, then does the
    actual work -- here, creating a post.

    Defense in depth: even though the engine guards against duplicate
    *deliveries*, this endpoint also checks PROCESSED_EVENT_IDS before
    acting. This is exactly the "downstream idempotency" concept from the
    README's reliability discussion -- protecting against the case where
    the engine's own retry fires a second real call because a prior
    response was lost in transit, even though the first call actually
    succeeded.
    """
    raw_body = await request.body()
    signature = request.headers.get("X-Engine-Signature")

    if not _verify_signature(WEBHOOK_SECRET, raw_body, signature):
        raise HTTPException(status_code=401, detail="invalid engine signature")

    data = json.loads(raw_body)
    event_id = data["event_id"]
    event_type = data["event_type"]
    payload = data.get("payload", {})

    if event_id in PROCESSED_EVENT_IDS:
        return {"received": True, "note": "already processed, no-op"}

    if event_type == "post.created":
        POSTS.append(
            {
                "id": event_id,
                "content": payload.get("content", ""),
                "created_at": time.time(),
            }
        )

    PROCESSED_EVENT_IDS.add(event_id)
    return {"received": True}


@app.get("/posts")
async def list_posts():
    return {"posts": sorted(POSTS, key=lambda p: p["created_at"], reverse=True)}


@app.get("/", response_class=HTMLResponse)
async def index():
    return HTML_PAGE


HTML_PAGE = """
<!doctype html>
<html>
<head>
<meta charset="utf-8">
<title>Demo App — Idempotent Post Creation</title>
<style>
  body { font-family: -apple-system, sans-serif; max-width: 640px; margin: 40px auto; padding: 0 16px; background:#0f172a; color:#e2e8f0; }
  textarea { width: 100%; min-height: 80px; padding: 8px; border-radius: 6px; border: 1px solid #334155; background:#1e293b; color:#e2e8f0; }
  button { padding: 8px 16px; margin: 6px 6px 6px 0; border-radius: 6px; border: none; cursor: pointer; font-weight: 600; }
  .primary { background: #3b82f6; color: white; }
  .danger { background: #ef4444; color: white; }
  .neutral { background: #334155; color: #e2e8f0; }
  .key-box { font-size: 0.8rem; color: #94a3b8; margin: 8px 0; word-break: break-all; }
  .log { background: #1e293b; border-radius: 6px; padding: 10px; font-size: 0.8rem; max-height: 160px; overflow-y: auto; margin-top: 10px; }
  .log div { padding: 2px 0; border-bottom: 1px solid #334155; }
  .post { background: #1e293b; border-radius: 6px; padding: 10px; margin-top: 8px; }
  .post small { color: #94a3b8; }
  h1 { font-size: 1.3rem; }
  h2 { font-size: 1rem; color: #94a3b8; margin-top: 32px; }
</style>
</head>
<body>
  <h1>Demo App — Idempotent Post Creation</h1>
  <p style="color:#94a3b8; font-size:0.9rem;">
    This page talks to its own backend, which forwards through the
    Webhook Reliability Engine before actually creating a post.
    Click "Simulate double-click" to fire the same submission 5 times —
    watch only one post appear.
  </p>

  <textarea id="content" placeholder="What's on your mind?"></textarea>
  <div class="key-box">idempotency_key for this draft: <span id="key"></span></div>

  <button class="primary" onclick="submitOnce()">Create Post</button>
  <button class="danger" onclick="submitFiveTimes()">Simulate double-click (5x)</button>
  <button class="neutral" onclick="newDraft()">New Post (new key)</button>

  <div class="log" id="log"></div>

  <h2>Posts (from the real backend, GET /posts)</h2>
  <div id="posts"></div>

<script>
let currentKey = crypto.randomUUID();
document.getElementById('key').textContent = currentKey;

function log(msg) {
  const el = document.getElementById('log');
  const line = document.createElement('div');
  line.textContent = new Date().toLocaleTimeString() + '  ' + msg;
  el.prepend(line);
}

async function submitOnce() {
  const content = document.getElementById('content').value;
  if (!content.trim()) { log('(skipped empty content)'); return; }
  try {
    const res = await fetch('/posts', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({content, idempotency_key: currentKey})
    });
    const body = await res.json();
    log(`POST /posts -> ${res.status} ${body.message || body.detail || JSON.stringify(body)}`);
  } catch (e) {
    log('error: ' + e.message);
  }
  refreshPosts();
}

function submitFiveTimes() {
  log('--- firing 5 rapid submissions with the SAME idempotency_key ---');
  for (let i = 0; i < 5; i++) submitOnce();
}

function newDraft() {
  currentKey = crypto.randomUUID();
  document.getElementById('key').textContent = currentKey;
  document.getElementById('content').value = '';
  log('--- new draft, fresh idempotency_key ---');
}

async function refreshPosts() {
  const res = await fetch('/posts');
  const data = await res.json();
  const el = document.getElementById('posts');
  el.innerHTML = '';
  if (data.posts.length === 0) {
    el.innerHTML = '<p style="color:#64748b;">No posts yet.</p>';
    return;
  }
  for (const p of data.posts) {
    const div = document.createElement('div');
    div.className = 'post';
    div.innerHTML = `<div>${p.content}</div><small>id: ${p.id}</small>`;
    el.appendChild(div);
  }
}

setInterval(refreshPosts, 2000);
refreshPosts();
</script>
</body>
</html>
"""
