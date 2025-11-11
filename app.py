from flask import Flask, render_template, request, jsonify, send_file, url_for
import uuid, qrcode, io, json, os, threading, time
from datetime import datetime
from pathlib import Path

app = Flask(__name__, static_folder="static", template_folder="templates")

# ----------------------------
# Data storage setup
# ----------------------------
DATA_FILE = Path("queues.json")
STATS_FILE = Path("stats.json")
LOCK = threading.Lock()

# ----------------------------
# Helper functions
# ----------------------------
def load_data():
    if DATA_FILE.exists():
        return json.loads(DATA_FILE.read_text())
    return {}

def save_data(data):
    with LOCK:
        DATA_FILE.write_text(json.dumps(data))

def load_stats():
    if STATS_FILE.exists():
        return json.loads(STATS_FILE.read_text())
    return {"served_today": 0, "history": []}

def save_stats(stats):
    STATS_FILE.write_text(json.dumps(stats))

# Initialize storage
if not DATA_FILE.exists():
    save_data({})
if not STATS_FILE.exists():
    save_stats({"served_today": 0, "history": []})

# ----------------------------
# Routes
# ----------------------------
@app.route("/")
def index():
    return render_template("index.html")

# ---------- ADMIN SIDE ----------
@app.route("/create_queue", methods=["POST"])
def create_queue():
    data = load_data()
    queue_id = uuid.uuid4().hex[:8]
    data[queue_id] = {"created": time.time(), "users": []}
    save_data(data)
    queue_url = url_for("join_queue_page", queue_id=queue_id, _external=True)
    return jsonify({"queue_id": queue_id, "queue_url": queue_url})

@app.route("/queue/<queue_id>/qr")
def queue_qr(queue_id):
    """Generate QR Code that links directly to the Join Queue page"""
    data = load_data()
    if queue_id not in data:
        return "Queue not found", 404

    qr_dir = Path("static/qr_cache")
    qr_dir.mkdir(exist_ok=True)
    qr_path = qr_dir / f"{queue_id}.png"

    if not qr_path.exists():
        queue_url = url_for("join_queue_page", queue_id=queue_id, _external=True)
        img = qrcode.make(queue_url)
        img.save(qr_path)

    return send_file(qr_path, mimetype="image/png")

# ---------- USER SIDE ----------
@app.route("/queue/<queue_id>")
def join_queue_page(queue_id):
    """User-facing join page"""
    data = load_data()
    if queue_id not in data:
        return "Queue not found", 404
    return render_template("join.html", queue_id=queue_id)

# ---------- API ENDPOINTS ----------
@app.route("/api/queue/<queue_id>/data")
def queue_data(queue_id):
    """Returns current queue data"""
    data = load_data()
    if queue_id not in data:
        return jsonify({"error": "not_found"}), 404
    return jsonify({"users": data[queue_id]["users"]})

@app.route("/api/queue/<queue_id>/join", methods=["POST"])
def queue_join(queue_id):
    """User joins queue"""
    payload = request.json or {}
    name = payload.get("name") or f"User_{uuid.uuid4().hex[:6]}"
    data = load_data()
    if queue_id not in data:
        return jsonify({"error": "not_found"}), 404
    data[queue_id]["users"].append(name)
    save_data(data) 
    position = len(data[queue_id]["users"])  
    return jsonify({"name": name, "position": position})

@app.route("/api/queue/<queue_id>/add", methods=["POST"])
def queue_add(queue_id):
    """Admin adds a user manually"""
    payload = request.json or {}
    name = payload.get("name")
    if not name:
        return jsonify({"error": "missing_name"}), 400
    data = load_data()
    if queue_id not in data:
        return jsonify({"error": "not_found"}), 404
    data[queue_id]["users"].append(name)
    save_data(data)
    return jsonify({"ok": True})

@app.route("/api/queue/<queue_id>/next", methods=["POST"])
def queue_next(queue_id):
    """Admin serves next person + log stats"""
    data = load_data()
    stats = load_stats()

    if queue_id not in data:
        return jsonify({"error": "not_found"}), 404

    if data[queue_id]["users"]:
        removed = data[queue_id]["users"].pop(0)
        save_data(data)

        # Log served user
        stats["served_today"] += 1
        stats["history"].append({
            "user": removed,
            "queue": queue_id,
            "time": datetime.now().strftime("%H:%M:%S")
        })
        stats["history"] = stats["history"][-50:]  # keep last 50
        save_stats(stats)
        return jsonify({"removed": removed})

    return jsonify({"removed": None})

@app.route("/api/queue/<queue_id>/clear", methods=["POST"])
def queue_clear(queue_id):  
    """Admin clears queue"""
    data = load_data()
    if queue_id not in data:
        return jsonify({"error": "not_found"}), 404
    data[queue_id]["users"] = []
    save_data(data)
    return jsonify({"ok": True})

@app.route("/api/queue/<qid>/delete", methods=["POST"])
def delete_queue(qid):
    """Completely remove a queue and log deletion"""
    data = load_data()
    stats = load_stats()

    if qid not in data:
        return jsonify({"error": "not_found"}), 404

    # Remove QR cache if exists
    qr_path = Path(f"static/qr_cache/{qid}.png")
    if qr_path.exists():
        qr_path.unlink()

    # Log deletion in stats
    stats["history"].append({
        "user": "-",
        "queue": qid,
        "event": "Queue deleted",
        "time": datetime.now().strftime("%H:%M:%S")
    })
    stats["history"] = stats["history"][-50:]
    save_stats(stats)

    # Delete queue
    del data[qid]
    save_data(data)

    return jsonify({"ok": True, "message": f"Queue {qid} deleted"})

# ---------- ADMIN STATS ----------
@app.route("/api/admin/stats")
def admin_stats():
    """Admin dashboard statistics"""
    data = load_data()
    stats = load_stats()

    active_queues = len(data)
    total_waiting = sum(len(q["users"]) for q in data.values())

    return jsonify({
        "active_queues": active_queues,
        "total_waiting": total_waiting,
        "served_today": stats.get("served_today", 0),
        "history": stats.get("history", [])[-10:]
    })

@app.route("/api/admin/queues")
def admin_queues():
    data = load_data()
    results = []
    for qid, q in data.items():
        results.append({
            "id": qid,
            "waiting": len(q.get("users", [])),
            "created": time.strftime("%H:%M:%S", time.localtime(q.get("created", 0)))
        })

    stats = load_stats()
    return jsonify({"queues": results, "history": stats.get("history", [])})

@app.route("/api/admin/history/clear_last", methods=["POST"])
def clear_last_history():
    """Remove the last entry from stats history.json"""
    stats = load_stats()

    if not stats.get("history"):
        return jsonify(ok=False, message="No history to clear")

    last_entry = stats["history"].pop()  # remove last
    save_stats(stats)
    return jsonify(ok=True, message="Last entry cleared", removed=last_entry)

@app.route("/api/admin/history/clear_all", methods=["POST"])
def clear_all_history():
    """Clear ALL recent served/deleted items from stats.json"""
    stats = load_stats()
    cleared_count = len(stats.get("history", []))
    stats["history"] = []
    save_stats(stats)
    return jsonify(ok=True, message=f"Cleared {cleared_count} history items.")

@app.route("/admin/queue/<queue_id>")
def admin_queue_manage(queue_id):
    data = load_data()
    if queue_id not in data:
        return "Queue not found", 404
    return render_template("admin_manage.html", queue_id=queue_id)

@app.route("/admin")
def admin_portal():
    data = load_data()
    return render_template("admin.html", queues=data)

# ----------------------------
if __name__ == "__main__":
    app.run(debug=True, port=5000)
