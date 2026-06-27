import os
import uuid
import requests
from typing import Dict, Any, List, Optional, Tuple
from flask import Flask, request, jsonify, Response, render_template_string

app = Flask(__name__)

# ---------- Configuration ----------
JSONCLIP_API_KEY = os.environ.get("JSONCLIP_API_KEY", "ed73662576e19a6d8d7b92a1dacf42e4")
JSONCLIP_RENDER_URL = "https://api.jsonclip.com/render?sync=1"
TIMEOUT = 60
DEV = "@R4_QN"
CHANNEL = "@R4_QX"
# الرابط الأساسي للمشروع
BASE_URL = "https://xk-vedio-generator.vercel.app"

# ---------- In-memory video store (use Redis in production) ----------
video_store: Dict[str, str] = {}

# ---------- HTTP session for connection reuse ----------
session = requests.Session()
session.headers.update({
    "Authorization": f"Bearer {JSONCLIP_API_KEY}",
    "Content-Type": "application/json"
})

# =============================================================
#  Parameter Definitions (for documentation & optional defaults)
# =============================================================
PARAM_DEFS = [
    # General / Env
    {"name": "env", "type": "str", "required": False, "default": "production", "desc": "Environment (production / development)"},
    # Video dimensions
    {"name": "width", "type": "int", "required": False, "default": 720, "desc": "Video width in pixels"},
    {"name": "height", "type": "int", "required": False, "default": 1280, "desc": "Video height in pixels"},
    # FPS
    {"name": "fps", "type": "int", "required": False, "default": 30, "desc": "Frames per second"},
    # Background
    {"name": "background_color", "type": "str", "required": False, "default": "#000000", "desc": "Background color (hex with #)"},
    # Images / scenes (multiple)
    {"name": "image", "type": "url", "required": True, "default": "", "desc": "First image URL. Use image2, image3... for additional scenes."},
    {"name": "image2", "type": "url", "required": False, "default": "", "desc": "Second image URL (optional)"},
    {"name": "image3", "type": "url", "required": False, "default": "", "desc": "Third image URL (optional)"},
    {"name": "image4", "type": "url", "required": False, "default": "", "desc": "Fourth image URL (optional)"},
    {"name": "image5", "type": "url", "required": False, "default": "", "desc": "Fifth image URL (optional)"},
    # Durations per scene
    {"name": "duration", "type": "int", "required": False, "default": 3000, "desc": "Duration of first scene in ms. Use duration2, duration3..."},
    {"name": "duration2", "type": "int", "required": False, "default": 3000, "desc": "Duration of second scene in ms"},
    {"name": "duration3", "type": "int", "required": False, "default": 3000, "desc": "Duration of third scene in ms"},
    {"name": "duration4", "type": "int", "required": False, "default": 3000, "desc": "Duration of fourth scene in ms"},
    {"name": "duration5", "type": "int", "required": False, "default": 3000, "desc": "Duration of fifth scene in ms"},
    # Audio
    {"name": "audio", "type": "url", "required": False, "default": "", "desc": "Audio file URL"},
    {"name": "audio_role", "type": "str", "required": False, "default": "background", "desc": "Audio role (background, voiceover, etc.)"},
    {"name": "audio_from", "type": "int", "required": False, "default": 0, "desc": "Audio start time in ms"},
    {"name": "audio_to", "type": "int", "required": False, "default": 0, "desc": "Audio end time in ms (0 = full duration)"},
    {"name": "audio_fade_out", "type": "int", "required": False, "default": 0, "desc": "Audio fade out in ms"},
    # Output
    {"name": "output_format", "type": "str", "required": False, "default": "mp4", "desc": "Output format (mp4, webm, gif)"},
    {"name": "quality", "type": "str", "required": False, "default": "high", "desc": "Render quality (low, medium, high)"},
    # Sync (rendering mode)
    {"name": "sync", "type": "int", "required": False, "default": 1, "desc": "Synchronous rendering (1 = wait for result)"},
]

# Map of param name to default for quick access
DEFAULTS = {p["name"]: p["default"] for p in PARAM_DEFS}

# =============================================================
#  Helper: build JSONClip payload dynamically from query args
# =============================================================
def build_jsonclip_payload(args: Dict[str, str]) -> Dict[str, Any]:
    """
    Converts flat query parameters into the nested JSON structure
    required by JSONClip API.

    - Simple scalar fields are added directly.
    - Images and durations are grouped into a 'scenes' array.
    - Audio parameters are collected into an 'audio' object.
    """
    payload: Dict[str, Any] = {}

    # --- 1. Scalar fields (non‑scene, non‑audio) ---
    scalar_fields = [
        "env", "width", "height", "fps", "background_color",
        "output_format", "quality", "sync"
    ]
    for field in scalar_fields:
        if field in args and args[field].strip():
            val = args[field].strip()
            # Convert numeric types where appropriate
            if field in ("width", "height", "fps", "sync"):
                try:
                    val = int(val)
                except ValueError:
                    continue  # skip invalid numbers
            payload[field] = val

    # --- 2. Build scenes array ---
    scenes = _build_scenes(args)
    if scenes:
        payload["scenes"] = scenes

    # --- 3. Build audio object ---
    audio = _build_audio(args)
    if audio:
        payload["audio"] = audio

    return payload


def _build_scenes(args: Dict[str, str]) -> List[Dict[str, Any]]:
    """
    Constructs a list of scene objects from args like:
    image, image2, image3, ... and duration, duration2, ...
    """
    # Collect images (image, image2, image3, ...)
    images = []
    for key in sorted(args.keys()):
        # Accept both "image" (first) and "image2", "image3", ...
        if key == "image" or (key.startswith("image") and key[5:].isdigit()):
            url = args[key].strip()
            if url:
                images.append(url)
    if not images:
        return []

    # Collect durations (duration, duration2, ...)
    durations = []
    for key in sorted(args.keys()):
        if key == "duration" or (key.startswith("duration") and key[8:].isdigit()):
            dur = args[key].strip()
            try:
                dur_ms = int(dur) if dur else DEFAULTS["duration"]
            except ValueError:
                dur_ms = DEFAULTS["duration"]
            durations.append(dur_ms)

    # Build scenes: match each image with a duration; if not enough durations, repeat default
    scenes = []
    default_dur = DEFAULTS["duration"]  # 3000 ms
    for i, img in enumerate(images):
        dur = durations[i] if i < len(durations) else default_dur
        scenes.append({"image": img, "duration": dur})
    return scenes


def _build_audio(args: Dict[str, str]) -> Optional[Dict[str, Any]]:
    """Builds audio object if at least an audio URL is provided."""
    audio_url = args.get("audio", "").strip()
    if not audio_url:
        return None
    audio_obj = {"url": audio_url}
    # Optional audio parameters
    if "audio_role" in args:
        audio_obj["role"] = args["audio_role"].strip()
    if "audio_from" in args:
        try:
            audio_obj["from"] = int(args["audio_from"])
        except ValueError:
            pass
    if "audio_to" in args:
        try:
            audio_obj["to"] = int(args["audio_to"])
        except ValueError:
            pass
    if "audio_fade_out" in args:
        try:
            audio_obj["fade_out"] = int(args["audio_fade_out"])
        except ValueError:
            pass
    return audio_obj


# =============================================================
#  HTML Templates (embedded, dark mode, Bootstrap 5)
# =============================================================
DOCS_HTML = f"""<!DOCTYPE html>
<html lang="en" data-bs-theme="dark">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>XK VIDEO GENERATOR – Docs</title>
  <link href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.0/dist/css/bootstrap.min.css" rel="stylesheet">
</head>
<body>
  <div class="container py-5">
    <h1 class="mb-4">📹 XK VIDEO GENERATOR</h1>
    <p class="lead">Use this API to render videos with JSONClip directly from browser URLs. All JSONClip features are exposed as query parameters.</p>

    <h2>⚡ Quick Start</h2>
    <pre class="bg-dark text-light p-3 rounded"><code>{BASE_URL}/generate?image=https://picsum.photos/720/1280&width=720&height=1280&fps=30&background_color=%23000000&duration=2000&audio=https://www.soundhelix.com/examples/mp3/SoundHelix-Song-1.mp3</code></pre>

    <h2>📋 Parameters Reference</h2>
    <div class="table-responsive">
      <table class="table table-striped table-bordered">
        <thead>
          <tr>
            <th>Parameter</th>
            <th>Type</th>
            <th>Required</th>
            <th>Default</th>
            <th>Description</th>
            <th>Example</th>
          </tr>
        </thead>
        <tbody>
          {% for param in params %}
          <tr>
            <td><code>{{ param.name }}</code></td>
            <td>{{ param.type }}</td>
            <td>{{ 'Yes' if param.required else 'No' }}</td>
            <td>{{ param.default if param.default != '' else '—' }}</td>
            <td>{{ param.desc }}</td>
            <td><code>{{ param.name }}={{ param.default if param.default != '' else 'value' }}</code></td>
          </tr>
          {% endfor %}
        </tbody>
      </table>
    </div>

    <h2>🧪 Code Examples</h2>
    <h3>cURL</h3>
    <pre class="bg-dark text-light p-3"><code>curl "{BASE_URL}/generate?image=https://picsum.photos/720/1280&width=720&height=1280&fps=30&duration=2000"</code></pre>

    <h3>Python</h3>
    <pre class="bg-dark text-light p-3"><code>import requests
url = "{BASE_URL}/generate"
params = {{"image": "https://picsum.photos/720/1280", "width": 720, "height": 1280, "fps": 30}}
resp = requests.get(url, params=params)
print(resp.json())
# {{
#   "success": true,
#   "video": "{BASE_URL}/video/uuid-here",
#   "DEV": "{DEV}",
#   "CHANNEL": "{CHANNEL}"
# }}</code></pre>

    <h3>JavaScript (fetch)</h3>
    <pre class="bg-dark text-light p-3"><code>fetch('{BASE_URL}/generate?image=https://picsum.photos/720/1280&width=720&height=1280')
  .then(r => r.json())
  .then(data => console.log(data.video));</code></pre>

    <h3>Browser</h3>
    <p>Simply paste this URL in your browser's address bar:</p>
    <pre class="bg-dark text-light p-3"><code>{BASE_URL}/generate?image=https://picsum.photos/720/1280&width=720&height=1280</code></pre>

    <hr>
    <p class="text-muted">Developed by <strong>{DEV}</strong> • Channel: <strong>{CHANNEL}</strong></p>
  </div>
</body>
</html>"""


# =============================================================
#  Routes
# =============================================================

@app.route("/")
def home():
    """API information page."""
    base = request.host_url.rstrip("/")
    return jsonify({
        "name": "XK VIDEO GENERATOR",
        "version": "2.0.0",
        "developer": DEV,
        "channel": CHANNEL,
        "docs": f"{base}docs",
        "generate_endpoint": f"{base}generate?<params>",
        "video_proxy": f"{base}video/<uuid>",
        "usage": "See /docs for full parameter reference."
    })

@app.route("/docs")
def documentation():
    """Serves the HTML documentation page with dark design."""
    return render_template_string(DOCS_HTML, params=PARAM_DEFS)


@app.route("/generate")
def generate_video():
    """
    Main endpoint:
    1. Reads all query parameters.
    2. Builds the JSONClip payload.
    3. Sends to JSONClip API.
    4. Stores the real video URL behind a UUID.
    5. Returns a proxy link.
    """
    # At least one image is required (we validate in build logic)
    args = request.args.to_dict()

    # Check for bare minimum: an image parameter
    if not any(k in args for k in ("image", "image2", "image3")):
        return jsonify({
            "success": False,
            "error": "At least one image parameter (image, image2, ...) is required.",
            "DEV": DEV,
            "CHANNEL": CHANNEL
        }), 400

    # Build payload dynamically
    try:
        payload = build_jsonclip_payload(args)
    except Exception as e:
        return jsonify({
            "success": False,
            "error": f"Failed to build request payload: {str(e)}",
            "DEV": DEV,
            "CHANNEL": CHANNEL
        }), 400

    # Send to JSONClip
    try:
        resp = session.post(JSONCLIP_RENDER_URL, json=payload, timeout=TIMEOUT)
        resp.raise_for_status()
        data = resp.json()
    except requests.exceptions.Timeout:
        return jsonify({
            "success": False,
            "error": "Request timed out.",
            "DEV": DEV,
            "CHANNEL": CHANNEL
        }), 504
    except requests.exceptions.RequestException as e:
        return jsonify({
            "success": False,
            "error": f"Request failed: {str(e)}",
            "DEV": DEV,
            "CHANNEL": CHANNEL
        }), 502
    except ValueError:
        return jsonify({
            "success": False,
            "error": "Invalid JSON response from video generator.",
            "DEV": DEV,
            "CHANNEL": CHANNEL
        }), 502

    # Extract the actual video URL (adjust field name according to JSONClip response)
    real_video_url = (
        data.get("url")
        or data.get("video_url")
        or data.get("result", {}).get("url")
    )
    if not real_video_url:
        return jsonify({
            "success": False,
            "error": "Video URL not found in response.",
            "DEV": DEV,
            "CHANNEL": CHANNEL
        }), 502

    # Store behind a UUID
    video_id = str(uuid.uuid4())
    video_store[video_id] = real_video_url

    proxy_url = f"{request.host_url}video/{video_id}"
    return jsonify({
        "success": True,
        "video": proxy_url,
        "DEV": DEV,
        "CHANNEL": CHANNEL
    })


@app.route("/video/<video_id>")
def serve_video(video_id: str):
    """Streams the real video without revealing its true URL."""
    real_url = video_store.get(video_id)
    if not real_url:
        return jsonify({
            "success": False,
            "error": "Video not found or link expired.",
            "DEV": DEV,
            "CHANNEL": CHANNEL
        }), 404

    try:
        video_resp = session.get(real_url, stream=True, timeout=TIMEOUT)
        video_resp.raise_for_status()
        content_type = video_resp.headers.get("Content-Type", "video/mp4")

        def generate():
            for chunk in video_resp.iter_content(chunk_size=8192):
                if chunk:
                    yield chunk

        return Response(generate(), content_type=content_type, status=video_resp.status_code)
    except requests.exceptions.RequestException as e:
        return jsonify({
            "success": False,
            "error": f"Failed to fetch video: {str(e)}",
            "DEV": DEV,
            "CHANNEL": CHANNEL
        }), 502


# =============================================================
#  Main entry point
# =============================================================
if __name__ == "__main__":
    app.run(debug=True)
