import os
import threading
import json
import shutil

import webview
from flask import Flask, send_from_directory, jsonify, request, send_file
from openai import OpenAI

import halovision

# -------------------------------------------------------------
# PATHS (READ from bundle, WRITE to user Downloads)
# -------------------------------------------------------------
import tempfile
import sys

if getattr(sys, 'frozen', False):
    BASE_DIR = sys._MEIPASS
else:
    BASE_DIR = os.path.dirname(os.path.abspath(__file__))

TEMP_DIR = os.path.join(tempfile.gettempdir(), "HaloVision_Temp")
os.makedirs(TEMP_DIR, exist_ok=True)
# Use lower‑case "static" everywhere to match your folder
STATIC_DIR = os.path.join(BASE_DIR, "static")
SOUNDFONT_PATH = os.path.join(BASE_DIR, "default.sf2")

# Folder where the user can actually write
USER_DOWNLOADS = os.path.join(
    os.path.expanduser("~"),
    "Downloads",
    "HaloVision_Exports"
)
os.makedirs(USER_DOWNLOADS, exist_ok=True)

def copy_to_downloads(filename: str) -> str:
    src = os.path.join(TEMP_DIR, filename)
    dst = os.path.join(USER_DOWNLOADS, filename)
    os.makedirs(USER_DOWNLOADS, exist_ok=True)
    print("[COPY]", src, "->", dst)
    shutil.copy2(src, dst)
    return dst

# -------------------------------------------------------------
# PYWEBVIEW BRIDGE
# -------------------------------------------------------------

window = None  # will be set in __main__

class Api:
    def choose_save_path(self, suggested_name: str):
        result = window.create_file_dialog(
            webview.FileDialog.SAVE,
            save_filename=suggested_name
        )

        if not result:
            return {"status": "cancelled"}

        dest_path = result[0]
        dest_dir = os.path.dirname(dest_path) or "."

        if not os.access(dest_dir, os.W_OK):
            return {"status": "error", "message": "Selected folder is not writable."}

        return {"status": "success", "path": dest_path}

# -------------------------------------------------------------
# LM STUDIO / LOCAL LLM SETUP
# -------------------------------------------------------------

client = OpenAI(base_url="http://localhost:1234/v1", api_key="lm-studio")

SYSTEM_PROMPT = """You are a procedural music engineer. 
Translate the user's text/mood/concept into numerical parameters for a music synthesizer.
Output ONLY a raw, valid JSON object with these exact keys. No explanations.

{
  "seed_name": "A 1-3 word camelCase summary of the prompt",
  "tempo_min": integer (40 to 200, slow to fast),
  "tempo_max": integer (must be >= tempo_min),
  "density": float (0.1 to 1.5, sparse to cluttered),
  "pitch_offset": integer (-24 to +24, dark/deep to bright/high),
  "drum_bias": float (0.0 to 1.0, 0=no drums, 1=heavy drums),
  "polyphony": integer (1 to 6, single notes to huge chords),
  "articulation": float (0.5 to 4.0, staccato/short to legato/long),
  "instruments": integer (1 to 16, solo to orchestral)
}"""

def text_to_halovision_coord(user_text: str) -> str:
    try:
        response = client.chat.completions.create(
            model="local-model",
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": user_text}
            ],
            temperature=0.7
        )

        raw_content = response.choices[0].message.content
        print("[DEBUG] LM Studio raw content:", repr(raw_content))

        clean = raw_content
        clean = clean.replace("```json", "").replace("```", "")
        clean = clean.replace("<|im_end|>", "")
        clean = clean.strip()

        try:
            data = json.loads(clean)
        except Exception as parse_err:
            print("[ERROR] JSON parse failed:", parse_err)
            print("[ERROR] Cleaned content was:", clean)
            raise

        coord = (
            f"Store={data.get('seed_name', 'Custom')};"
            f"T={data.get('tempo_min', 60)}-{data.get('tempo_max', 120)};"
            f"Den={data.get('density', 1.0)};"
            f"P={data.get('pitch_offset', 0)};"
            f"Dr={data.get('drum_bias', 0.2)};"
            f"Poly={data.get('polyphony', 4)};"
            f"Art={data.get('articulation', 1.0)};"
            f"Inst={data.get('instruments', 5)}"
        )
        return coord

    except Exception as e:
        print("[LM STUDIO ERROR]", e)
        return "Store=FallbackError;T=80-120;Den=1.0;P=0;Dr=0.5;Poly=4;Art=1.0;Inst=4"

# -------------------------------------------------------------
# FLASK APP SETUP
# -------------------------------------------------------------

app = Flask(__name__, static_folder=STATIC_DIR)

@app.route('/')
def serve_ui():
    return send_from_directory(STATIC_DIR, 'index.html')

# -------------------------------------------------------------
# 0. GENERATE FROM NATURAL TEXT
# -------------------------------------------------------------

@app.route('/api/generate_from_text', methods=['POST'])
def generate_from_text():
    data = request.json or {}
    user_text = data.get('text', '')
    use_ai = data.get('use_ai', False)

    if not user_text:
        return jsonify({"status": "error", "message": "No text provided."}), 400

    try:
        if use_ai:
            coord_string = text_to_halovision_coord(user_text)
            suggested_name = f"{coord_string.split(';')[0].replace('Store=', '')}.wav"
        else:
            clean_text = user_text.replace(";", "").replace("=", "")[:30]
            coord_string = f"Store={clean_text};Room=Random;Rack=1;Crate=1;Sleeve=1"
            suggested_name = f"Random_{clean_text}.wav"

        res = halovision.process_gui_request(coord_string, export_audio=True, audio_format='wav', out_dir=TEMP_DIR)

        file_name_mid = os.path.basename(res['midi_path'])
        file_name_wav = os.path.basename(res['audio_path']) if res.get('audio_path') else None

        # copy out of static
        copy_to_downloads(file_name_mid)
        if file_name_wav:
            copy_to_downloads(file_name_wav)

        return jsonify({
            "status": "success",
            "seed": res['seed'],
            "coord_string": coord_string,
            "mid_url": f"/api/download?file={file_name_mid}",
            "wav_url": f"/api/download?file={file_name_wav}" if file_name_wav else None,
            "suggested_name": suggested_name,
            "used_ai": use_ai
        })
    except Exception as e:
        print("[ERROR] generate_from_text:", e)
        return jsonify({"status": "error", "message": str(e)}), 500

# -------------------------------------------------------------
# 1. GENERATE FROM RAW STRING
# -------------------------------------------------------------

@app.route('/api/generate_raw', methods=['POST'])
def generate_raw():
    data = request.json or {}
    coord_string = data.get('coord_string')

    try:
        res = halovision.process_gui_request(
            coord_string,
            export_audio=True,
            audio_format='wav',
            out_dir=TEMP_DIR          # <-- add this
        )

        print("[DEBUG] generate_raw result:", res)

        file_name_mid = os.path.basename(res['midi_path'])
        file_name_wav = os.path.basename(res['audio_path']) if res.get('audio_path') else None

        copy_to_downloads(file_name_mid)
        if file_name_wav:
            copy_to_downloads(file_name_wav)

        suggested_name = "HaloVision_Export.mid"
        payload = {
            "status": "success",
            "seed": res['seed'],
            "mid_url": f"/api/download?file={file_name_mid}",
            "wav_url": f"/api/download?file={file_name_wav}" if file_name_wav else None,
            "suggested_name": suggested_name
        }
        print("[DEBUG] generate_raw payload:", payload)

        return jsonify(payload)

    except Exception as e:
        print("[ERROR] generate_raw:", e)
        return jsonify({"status": "error", "message": str(e)}), 500

# -------------------------------------------------------------
# 1B. LEGACY GENERATE FROM SEED
# -------------------------------------------------------------

@app.route('/api/generate', methods=['POST'])
def generate_from_seed():
    data = request.json or {}
    seed = data.get('seed', {})
    fmt = data.get('format', 'mid')

    try:
        coord_string = (
            f"Store={seed.get('store', 'HaloVision')};"
            f"Room={seed.get('room', 'Main')};"
            f"Rack={seed.get('rack', '1')};"
            f"Crate={seed.get('crate', '1')};"
            f"Sleeve={seed.get('sleeve', '1')}"
        )

        res = halovision.process_gui_request(
            coord_string,
            export_audio=True,
            audio_format='wav',
            out_dir=TEMP_DIR
        )

        file_name_mid = os.path.basename(res['midi_path'])
        file_name_wav = os.path.basename(res['audio_path']) if res.get('audio_path') else None

        copy_to_downloads(file_name_mid)
        if file_name_wav:
            copy_to_downloads(file_name_wav)

        if fmt == 'wav':
            chosen = file_name_wav if file_name_wav else file_name_mid
        else:
            chosen = file_name_mid

        suggested_name = f"HaloVision_Export.{'wav' if fmt == 'wav' and file_name_wav else 'mid'}"

        return jsonify({
            "status": "success",
            "download_url": f"/api/download?file={chosen}",
            "suggested_name": suggested_name
        })
    except Exception as e:
        print("[ERROR] generate_from_seed:", e)
        return jsonify({"status": "error", "message": str(e)}), 500

# -------------------------------------------------------------
# 2. GENERATE FROM HVCOORD
# -------------------------------------------------------------

@app.route('/api/generate_hvcoord', methods=['POST'])
def generate_hvcoord():
    data = request.json or {}
    hvcoord = data.get('hvcoord', '').strip()

    try:
        coord_string = halovision.hvcoord_to_coord(hvcoord)
        res = halovision.process_gui_request(
            coord_string,
            export_audio=True,
            audio_format='wav',
            out_dir=TEMP_DIR
        )

        file_name_mid = os.path.basename(res['midi_path'])
        file_name_wav = os.path.basename(res['audio_path']) if res.get('audio_path') else None

        copy_to_downloads(file_name_mid)
        if file_name_wav:
            copy_to_downloads(file_name_wav)

        return jsonify({
            "status": "success",
            "coord_string": coord_string,
            "mid_url": f"/api/download?file={file_name_mid}",
            "wav_url": f"/api/download?file={file_name_wav}" if file_name_wav else None,
            "suggested_name": "HaloVision_Decoded.mid"
        })

    except Exception:
        return jsonify({"status": "error", "message": "Invalid or corrupt HVCOORD seed."}), 400

# -------------------------------------------------------------
# 3. DECODE HVMIDI
# -------------------------------------------------------------

@app.route('/api/decode_hvmidi', methods=['POST'])
def decode_hvmidi():
    data = request.json or {}
    hvmidi = data.get('hvmidi', '').strip()

    try:
        out_dir = TEMP_DIR
        if not os.path.exists(out_dir):
            os.makedirs(out_dir)

        file_name = "hv_reconstructed_1to1.mid"
        out_path = os.path.join(out_dir, file_name)

        halovision.hvmidi_to_midi(hvmidi, out_path)

        copy_to_downloads(file_name)

        return jsonify({
            "status": "success",
            "mid_url": f"/api/download?file={file_name}",
            "suggested_name": file_name
        })
    except Exception:
        return jsonify({"status": "error", "message": "Invalid or corrupt HVMIDI seed."}), 400

# -------------------------------------------------------------
# 4. ENCODE MIDI TO HVMIDI
# -------------------------------------------------------------

@app.route('/api/encode_midi', methods=['POST'])
def encode_midi():
    if 'file' not in request.files:
        return jsonify({"status": "error", "message": "No file uploaded."}), 400

    file = request.files['file']
    if file.filename == '':
        return jsonify({"status": "error", "message": "No file selected."}), 400

    try:
        temp_path = os.path.join(TEMP_DIR, "temp_upload.mid")
        file.save(temp_path)

        hvmidi_string = halovision.midi_to_hvmidi(temp_path)

        if os.path.exists(temp_path):
            os.remove(temp_path)

        return jsonify({
            "status": "success",
            "hvmidi_string": hvmidi_string
        })
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500

# -------------------------------------------------------------
# NEW DOWNLOAD ENDPOINT (from USER_DOWNLOADS)
# -------------------------------------------------------------

@app.route('/api/download')
def api_download():
    filename = request.args.get("file")
    if not filename:
        return jsonify({"status": "error", "message": "No file specified"}), 400

    file_path = os.path.join(USER_DOWNLOADS, filename)
    if not os.path.exists(file_path):
        return jsonify({"status": "error", "message": "File not found"}), 404

    return send_file(file_path, as_attachment=True)

# Optional: keep old route for dev
# @app.route('/download/<filename>')
# def download_file(filename):
#     return send_file(os.path.join(STATIC_DIR, filename), as_attachment=True)

# -------------------------------------------------------------
# SERVER START
# -------------------------------------------------------------

def start_server():
    if not os.path.exists(STATIC_DIR):
        os.makedirs(STATIC_DIR)
    app.run(host='127.0.0.1', port=5000, debug=False)

if __name__ == '__main__':
    t = threading.Thread(target=start_server)
    t.daemon = True
    t.start()

    api = Api()
    window = webview.create_window(
        'HaloVision Systems - HVMIDI',
        'http://127.0.0.1:5000',
        width=1280,
        height=720,
        js_api=api
    )
    webview.start()
