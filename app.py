from flask import Flask, render_template, request, jsonify
from flask_cors import CORS
import subprocess
import os

app = Flask(__name__)
CORS(app)  # Allow all origins


@app.route('/')
def index():
    return render_template('index.html')

@app.route('/download', methods=['POST'])
def download():
    data = request.json
    playlist_url = data.get('url')
    if not playlist_url:
        return jsonify({"status": "error", "message": "URL is required"}), 400
    output_dir = '/Users/pete/Desktop'  # Default to local desktop folder
    try:
        # Run SCDL command
        subprocess.run(['scdl', '-l', playlist_url, '--path', output_dir], check=True)
        return jsonify({"status": "success", "message": "Download complete"})
    except subprocess.CalledProcessError as e:
        return jsonify({"status": "error", "message": str(e)}), 500

if __name__ == '__main__':
    app.run(debug=True)
