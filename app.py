import re
from flask import Flask, render_template, request, jsonify
from flask_cors import CORS
import subprocess
import logging
import os

app = Flask(__name__)
CORS(app)  # Allow all origins

# Set up logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

@app.route('/')
def index():
    return render_template('index.html')

@app.route('/download', methods=['POST'])
def download():
    data = request.json

    url = data.get('url')
    path = data.get('path')
    
    try:
        if "spotify" in url:
            result = subprocess.run(["spotdl", "sync", url, "--output", path, "--save-file", os.path.join(path, "SyncData.spotdl")],
                                    check=True,
                                    stdout=subprocess.PIPE,  # Capture standard output
                                    stderr=subprocess.PIPE,  # Capture error output
                                    text=True  # Decode bytes to string
                                    )

    except subprocess.CalledProcessError as e:
        # Log errors from the SCDL command
        logger.error("SCDL command failed!")
        logger.error("Error Output: %s", e.stderr)
        return jsonify({"status": "error", "message": "Download failed", "error": e.stderr}), 500
    
    except Exception as e:
        # Catch other unexpected errors
        logger.exception("An unexpected error occurred.")
        return jsonify({"status": "error", "message": "An unexpected error occurred", "error": str(e)}), 500
    
     # Log and return successful output
    logger.info("Command executed successfully.")
    logger.info("Command Output: %s", result.stdout)
    return jsonify({"status": "success", "message": "Download complete at " + path, "output": result.stdout})
    
    
    # if not url:
    #     logger.error("No URL provided in the request.")
    #     return jsonify({"status": "error", "message": "URL is required"}), 400

    # output_dir = data.get('output_dir')

    # sync = data.get('sync')
    
    # try:
    #     # Run SCDL command and capture output

    #     if "soundcloud.com" in url:
    #         print("soundcloud link")
    #         result = subprocess.run(
    #         ['scdl', '-l', url, '--path', output_dir],
    #         check=True,
    #         stdout=subprocess.PIPE,  # Capture standard output
    #         stderr=subprocess.PIPE,  # Capture error output
    #         text=True  # Decode bytes to string
    #       )
            
    #     elif "spotify.com" in url:
    #         print("spotfiy link")
    #         result = subprocess.run(
    #         ['youtube-dl', '-i', '--extract-audio', '--audio-format', 'mp3', '--audio-quality', '0', '-o', output_dir + '/%(title)s.%(ext)s', url],
    #         check=True,
    #         stdout=subprocess.PIPE,  # Capture standard output
    #         stderr=subprocess.PIPE,  # Capture error output
    #         text=True  # Decode bytes to string
    #         )

        
        

    
    # except subprocess.CalledProcessError as e:
    #     # Log errors from the SCDL command
    #     logger.error("SCDL command failed!")
    #     logger.error("Error Output: %s", e.stderr)
    #     return jsonify({"status": "error", "message": "Download failed", "error": e.stderr}), 500
    
    # except Exception as e:
    #     # Catch other unexpected errors
    #     logger.exception("An unexpected error occurred.")
    #     return jsonify({"status": "error", "message": "An unexpected error occurred", "error": str(e)}), 500

if __name__ == '__main__':
    app.run(debug=True)