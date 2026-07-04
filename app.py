import os
import uuid
import copy
import threading

from flask import Flask, request, jsonify, send_file, render_template, abort, Response

import pdf_engine as engine

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
UPLOAD_DIR = os.path.join(BASE_DIR, "uploads")
OUTPUT_DIR = os.path.join(BASE_DIR, "outputs")
os.makedirs(UPLOAD_DIR, exist_ok=True)
os.makedirs(OUTPUT_DIR, exist_ok=True)

app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = 60 * 1024 * 1024  # 60 MB upload cap

# In-memory registry — this is a small local single-user tool, so a
# process-wide dict keyed by file_id is enough (no database needed).
FILES = {}
LOCK = threading.Lock()


def get_file_or_404(file_id):
    doc = FILES.get(file_id)
    if not doc:
        abort(404, description="Unknown file_id (server may have restarted).")
    return doc


def push_history(doc):
    snapshot = {
        "edits": copy.deepcopy(doc["edits"]),
        "deleted_pages": set(doc["deleted_pages"]),
    }
    doc["history"].append(snapshot)
    if len(doc["history"]) > 60:
        doc["history"].pop(0)


# --------------------------------------------------------------------------
# Pages
# --------------------------------------------------------------------------

@app.route("/")
def index():
    return render_template("index.html")


# --------------------------------------------------------------------------
# API
# --------------------------------------------------------------------------

@app.route("/api/upload", methods=["POST"])
def upload():
    file = request.files.get("file")
    if not file or file.filename == "":
        return jsonify({"error": "No file provided"}), 400
    if not file.filename.lower().endswith(".pdf"):
        return jsonify({"error": "Please upload a .pdf file"}), 400

    file_id = uuid.uuid4().hex
    save_path = os.path.join(UPLOAD_DIR, f"{file_id}.pdf")
    file.save(save_path)

    try:
        page_sizes = engine.get_page_sizes(save_path)
    except Exception as e:
        os.remove(save_path)
        return jsonify({"error": f"Could not read PDF: {e}"}), 400

    with LOCK:
        FILES[file_id] = {
            "path": save_path,
            "name": file.filename,
            "page_count": len(page_sizes),
            "page_sizes": page_sizes,
            "edits": {},           # {"1": [edit, ...], ...}
            "deleted_pages": set(),
            "history": [],
        }

    return jsonify({
        "file_id": file_id,
        "name": file.filename,
        "page_count": len(page_sizes),
        "pages": page_sizes,
    })


@app.route("/api/spans/<file_id>/<int:page_num>")
def spans(file_id, page_num):
    doc = get_file_or_404(file_id)
    if page_num < 1 or page_num > doc["page_count"]:
        abort(404)

    lines = engine.extract_lines(doc["path"], page_num)
    edits_for_page = doc["edits"].get(str(page_num), [])
    edit_by_id = {e["id"]: e for e in edits_for_page}

    result = []
    for line in lines:
        e = edit_by_id.get(line["id"])
        if e and e["type"] == "delete":
            continue  # hide deleted original lines
        if e and e["type"] == "replace":
            result.append({**line, "text": e["text"], "font": e["font"],
                            "size": e["size"], "color": e["color"], "isAdded": False})
        else:
            result.append({**line, "isAdded": False})

    # Include user-added text boxes
    for e in edits_for_page:
        if e["type"] == "add":
            result.append({
                "id": e["id"], "text": e["text"], "bbox": e["bbox"],
                "font": e["font"], "size": e["size"], "color": e["color"],
                "isAdded": True,
            })

    return jsonify({
        "page_num": page_num,
        "width": doc["page_sizes"][page_num - 1]["width"],
        "height": doc["page_sizes"][page_num - 1]["height"],
        "deleted": page_num in doc["deleted_pages"],
        "spans": result,
    })


@app.route("/api/render/<file_id>/<int:page_num>")
def render(file_id, page_num):
    doc = get_file_or_404(file_id)
    if page_num < 1 or page_num > doc["page_count"]:
        abort(404)
    try:
        scale = float(request.args.get("scale", 1.6))
        scale = max(0.5, min(scale, 4.0))
    except ValueError:
        scale = 1.6

    edits_for_page = doc["edits"].get(str(page_num), [])
    deleted = page_num in doc["deleted_pages"]
    png_bytes = engine.render_page_with_edits(
        doc["path"], page_num, doc["page_sizes"], edits_for_page, deleted, scale=scale
    )
    return Response(png_bytes, mimetype="image/png")


@app.route("/api/edit/<file_id>", methods=["POST"])
def edit(file_id):
    doc = get_file_or_404(file_id)
    data = request.get_json(force=True)

    page_num = data.get("page")
    edit_id = data.get("id")
    etype = data.get("type")
    if page_num is None or not edit_id or etype not in ("replace", "delete", "add"):
        return jsonify({"error": "Malformed edit payload"}), 400

    with LOCK:
        push_history(doc)
        page_key = str(page_num)
        page_edits = doc["edits"].setdefault(page_key, [])
        page_edits[:] = [e for e in page_edits if e["id"] != edit_id]

        new_edit = {
            "id": edit_id,
            "type": etype,
            "bbox": data.get("bbox", [0, 0, 0, 0]),
            "text": data.get("text", ""),
            "font": data.get("font", "Helvetica"),
            "size": data.get("size", 12),
            "color": data.get("color", [0, 0, 0]),
        }
        # An empty "replace" is really a delete.
        if etype == "replace" and not new_edit["text"].strip():
            new_edit["type"] = "delete"
        page_edits.append(new_edit)

    return jsonify({"ok": True})


@app.route("/api/edit/<file_id>/<edit_id>", methods=["DELETE"])
def remove_edit(file_id, edit_id):
    doc = get_file_or_404(file_id)
    page_num = request.args.get("page")
    with LOCK:
        push_history(doc)
        page_key = str(page_num)
        if page_key in doc["edits"]:
            doc["edits"][page_key] = [e for e in doc["edits"][page_key] if e["id"] != edit_id]
    return jsonify({"ok": True})


@app.route("/api/page/<file_id>/<int:page_num>/delete", methods=["POST"])
def toggle_delete_page(file_id, page_num):
    doc = get_file_or_404(file_id)
    data = request.get_json(silent=True) or {}
    want_deleted = data.get("deleted", True)
    with LOCK:
        push_history(doc)
        if want_deleted:
            doc["deleted_pages"].add(page_num)
        else:
            doc["deleted_pages"].discard(page_num)
    return jsonify({"ok": True, "deleted": page_num in doc["deleted_pages"]})


@app.route("/api/state/<file_id>")
def state(file_id):
    doc = get_file_or_404(file_id)
    return jsonify({
        "page_count": doc["page_count"],
        "deleted_pages": sorted(doc["deleted_pages"]),
        "can_undo": len(doc["history"]) > 0,
    })


@app.route("/api/undo/<file_id>", methods=["POST"])
def undo(file_id):
    doc = get_file_or_404(file_id)
    with LOCK:
        if not doc["history"]:
            return jsonify({"ok": False, "message": "Nothing to undo"})
        snapshot = doc["history"].pop()
        doc["edits"] = snapshot["edits"]
        doc["deleted_pages"] = snapshot["deleted_pages"]
    return jsonify({"ok": True})


@app.route("/api/reset/<file_id>", methods=["POST"])
def reset(file_id):
    doc = get_file_or_404(file_id)
    with LOCK:
        push_history(doc)
        doc["edits"] = {}
        doc["deleted_pages"] = set()
    return jsonify({"ok": True})


@app.route("/api/save/<file_id>", methods=["POST"])
def save(file_id):
    doc = get_file_or_404(file_id)
    base_name = os.path.splitext(doc["name"])[0]
    out_name = f"{base_name}_edited.pdf"
    out_path = os.path.join(OUTPUT_DIR, f"{file_id}_edited.pdf")
    try:
        engine.save_document(doc["path"], doc["page_sizes"], doc["edits"], doc["deleted_pages"], out_path)
    except Exception as e:
        return jsonify({"error": f"Failed to save: {e}"}), 500
    return jsonify({"ok": True, "download_url": f"/api/download/{file_id}", "filename": out_name})


@app.route("/api/download/<file_id>")
def download(file_id):
    doc = get_file_or_404(file_id)
    out_path = os.path.join(OUTPUT_DIR, f"{file_id}_edited.pdf")
    if not os.path.exists(out_path):
        abort(404, description="Save the document first.")
    base_name = os.path.splitext(doc["name"])[0]
    return send_file(out_path, as_attachment=True, download_name=f"{base_name}_edited.pdf")


if __name__ == "__main__":
    print("\n  editMyPdf-lite running at http://127.0.0.1:5000\n")
    app.run(host="127.0.0.1", port=5000, debug=False)
