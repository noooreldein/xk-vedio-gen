import os
import uuid
import requests
from flask import Flask, request, jsonify, Response, stream_with_context

app = Flask(__name__)

# --- الإعدادات الثابتة ---
JSONCLIP_API_KEY = os.environ.get("JSONCLIP_API_KEY", "ed73662576e19a6d8d7b92a1dacf42e4")  # استخدم متغير بيئة في النشر
JSONCLIP_RENDER_URL = "https://api.jsonclip.com/render?sync=1"
TIMEOUT = 60
DEV = "@R4_QN"
CHANNEL = "@R4_QX"

# تخزين مؤقت للروابط (في الذاكرة - مناسب للتطوير، يُنصح باستخدام Redis في الإنتاج)
video_store = {}

# جلسة requests للاستفادة من إعادة استخدام الاتصال
session = requests.Session()
session.headers.update({
    "Authorization": f"Bearer {JSONCLIP_API_KEY}",
    "Content-Type": "application/json"
})

def build_jsonclip_payload(prompt):
    """
    بناء جسم JSON المطلوب من JSONClip.
    يتم إدراج الـ prompt في المكان الصحيح حسب توثيق JSONClip.
    (يمكن تعديل البنية حسب القالب المستخدم)
    """
    return {
        "prompt": prompt
        # يمكن إضافة حقول ثابتة أخرى إذا تطلب الأمر، مثلاً:
        # "template": "default",
        # "settings": {"duration": 5}
    }

@app.route("/generate")
def generate():
    prompt = request.args.get("prompt", "").strip()
    if not prompt:
        return jsonify({
            "success": False,
            "error": "Missing prompt parameter.",
            "DEV": DEV,
            "CHANNEL": CHANNEL
        }), 400

    payload = build_jsonclip_payload(prompt)

    try:
        resp = session.post(JSONCLIP_RENDER_URL, json=payload, timeout=TIMEOUT)
        resp.raise_for_status()  # يثير استثناء عند أي كود خطأ HTTP
        data = resp.json()
    except requests.exceptions.Timeout:
        return jsonify({
            "success": False,
            "error": "JSONClip request timed out.",
            "DEV": DEV,
            "CHANNEL": CHANNEL
        }), 504
    except requests.exceptions.RequestException as e:
        return jsonify({
            "success": False,
            "error": f"JSONClip request failed: {str(e)}",
            "DEV": DEV,
            "CHANNEL": CHANNEL
        }), 502
    except ValueError:
        return jsonify({
            "success": False,
            "error": "Invalid JSON response from JSONClip.",
            "DEV": DEV,
            "CHANNEL": CHANNEL
        }), 502

    # استخراج رابط الفيديو - قد يختلف اسم الحقل حسب استجابة JSONClip الفعلية
    video_url = data.get("url") or data.get("video_url") or data.get("result", {}).get("url")
    if not video_url:
        return jsonify({
            "success": False,
            "error": "Video URL not found in JSONClip response.",
            "DEV": DEV,
            "CHANNEL": CHANNEL
        }), 502

    # توليد UUID وتخزين الرابط الحقيقي
    video_id = str(uuid.uuid4())
    video_store[video_id] = video_url

    # بناء الرابط الوسيط للمستخدم
    proxy_url = f"{request.host_url}video/{video_id}"

    return jsonify({
        "success": True,
        "video": proxy_url,
        "DEV": DEV,
        "CHANNEL": CHANNEL
    })

@app.route("/video/<video_id>")
def serve_video(video_id):
    real_url = video_store.get(video_id)
    if not real_url:
        return jsonify({
            "success": False,
            "error": "Video not found or link expired.",
            "DEV": DEV,
            "CHANNEL": CHANNEL
        }), 404

    try:
        # جلب الفيديو مع التدفق (stream) دون إعادة توجيه
        video_resp = requests.get(real_url, stream=True, timeout=TIMEOUT)
        video_resp.raise_for_status()

        # تحديد نوع المحتوى من الاستجابة الأصلية
        content_type = video_resp.headers.get("Content-Type", "video/mp4")

        def generate():
            for chunk in video_resp.iter_content(chunk_size=8192):
                if chunk:
                    yield chunk

        return Response(
            stream_with_context(generate()),
            content_type=content_type,
            status=video_resp.status_code
        )
    except requests.exceptions.RequestException as e:
        return jsonify({
            "success": False,
            "error": f"Failed to fetch video: {str(e)}",
            "DEV": DEV,
            "CHANNEL": CHANNEL
        }), 502

@app.route("/")
def home():
    return jsonify({"status": "JSONClip Proxy API is running", "DEV": DEV, "CHANNEL": CHANNEL})

if __name__ == "__main__":
    app.run(debug=True)
