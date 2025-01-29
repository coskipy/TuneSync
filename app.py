import json
import os
import subprocess
import logging
from pathlib import Path
from flask import Flask, render_template, request, jsonify, Response, stream_with_context
from flask_cors import CORS

app = Flask(__name__)
CORS(app)
syncedDirsJson = "syncedDirs.json"

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

@app.route('/')
def index():
    return render_template('index.html')

def add_path_to_json(file_name, new_path):
    """Add a path to the JSON file if it doesn't exist"""
    try:
        playlist_paths = []
        if Path(file_name).exists():
            with open(file_name, 'r') as f:
                content = f.read().strip()
                if content:
                    playlist_paths = json.loads(content)
        
        if new_path not in playlist_paths:
            playlist_paths.append(new_path)
            with open(file_name, 'w') as f:
                json.dump(playlist_paths, f, indent=4)
            return True
        return False
    except Exception as e:
        logger.error(f"Error updating sync file: {e}")
        return False

def stream_generator(command):
    """Generator function to yield process output line by line in SSE format"""
    try:
        process = subprocess.Popen(
            command,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            bufsize=1,
            universal_newlines=True
        )

        # Yield a start event
        yield "event: start\ndata: Operation started\n\n"

        # Read both stdout and stderr simultaneously
        while True:
            stdout_line = process.stdout.readline()
            stderr_line = process.stderr.readline()

            if not stdout_line and not stderr_line and process.poll() is not None:
                break

            if stdout_line:
                yield f"data: {stdout_line.strip()}\n\n"
            if stderr_line:
                yield f"data: ERROR: {stderr_line.strip()}\n\n"

        # Check final return code
        if process.returncode != 0:
            yield f"data: ERROR: Process exited with code {process.returncode}\n\n"

        # Yield a completion event
        yield "event: done\ndata: SYNC_COMPLETE\n\n"

    except Exception as e:
        logger.error(f"Streaming error: {e}")
        yield f"data: FATAL ERROR: {str(e)}\n\n"

@app.route('/download', methods=['GET'])
def download():
    """Handle download requests with SSE streaming"""
    url = request.args.get('url')
    path = request.args.get('path')

    if not url or not path:
        return jsonify({"status": "error", "message": "Missing URL or path"}), 400

    try:
        # Create sync file if needed
        if "/playlist/" in url or "/sets/" in url:
            if not add_path_to_json(syncedDirsJson, path):
                logger.info(f"Path {path} already in sync list")
            cmd = ["spotdl", "sync", url, "--output", path, "--save-file", os.path.join(path, "SyncData.spotdl")]
        else:
            cmd = ["spotdl", "download", url, "--output", path]

        # Stream the output
        return Response(stream_with_context(stream_generator(cmd)), content_type='text/event-stream')

    except Exception as e:
        logger.error(f"Download failed: {e}")
        return jsonify({"status": "error", "message": str(e)}), 500

@app.route('/sync-all', methods=['GET'])
def sync_all():
    """Handle sync-all requests with SSE streaming"""
    if not os.path.exists(syncedDirsJson):
        return jsonify({"status": "error", "message": "No directories to sync"}), 400

    try:
        with open(syncedDirsJson, 'r') as f:
            playlist_paths = json.load(f)
    except json.JSONDecodeError:
        return jsonify({"status": "error", "message": "Corrupted sync file"}), 500

    # Validate paths and build commands
    valid_paths = []
    commands = []
    for path in playlist_paths:
        sync_file = os.path.join(path, "SyncData.spotdl")
        if os.path.isdir(path) and os.path.exists(sync_file):
            commands.append(["spotdl", "sync", sync_file, "--output", path])
            valid_paths.append(path)
        else:
            logger.warning(f"Invalid path: {path}")

    # Update sync file with valid paths
    try:
        with open(syncedDirsJson, 'w') as f:
            json.dump(valid_paths, f, indent=4)
    except Exception as e:
        logger.error(f"Failed to update sync file: {e}")

    if not commands:
        return jsonify({"status": "error", "message": "No valid directories to sync"}), 400

    # Create combined generator for all commands
    def generate():
        yield "event: start\ndata: Full sync started\n\n"
        for cmd in commands:
            yield from stream_generator(cmd)
        yield "event: done\ndata: SYNC_COMPLETE\n\n"

    return Response(stream_with_context(generate()), content_type='text/event-stream')

@app.route('/sync-selected', methods=['GET'])
def sync_selected():
    """Handle sync-selected requests with SSE streaming"""
    try:
        paths = json.loads(request.args.get('paths', '[]'))
        if not paths:
            return jsonify({"status": "error", "message": "No paths provided"}), 400

        commands = []
        for path in paths:
            sync_file = os.path.join(path, "SyncData.spotdl")
            if os.path.exists(sync_file) and os.path.isdir(path):
                commands.append(["spotdl", "sync", sync_file, "--output", path])
        
        if not commands:
            return jsonify({"status": "error", "message": "No valid paths to sync"}), 400

        def generate():
            yield "event: start\ndata: Selected sync started\n\n"
            for cmd in commands:
                yield from stream_generator(cmd)
            yield "event: done\ndata: SYNC_COMPLETE\n\n"

        return Response(stream_with_context(generate()), content_type='text/event-stream')

    except Exception as e:
        logger.error(f"Sync selected failed: {e}")
        return jsonify({"status": "error", "message": str(e)}), 500

if __name__ == '__main__':
    app.run(debug=True)