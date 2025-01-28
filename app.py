import json
from pathlib import Path
from flask import Flask, render_template, request, jsonify
from flask_cors import CORS
import subprocess
import logging
import os

app = Flask(__name__)
CORS(app)  # Allow all origins
syncedDirsJson = "syncedDirs.json"

# Set up logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

@app.route('/')
def index():
    return render_template('index.html')

# Function to add a path string to the JSON file
def add_path_to_json(file_name, new_path):
    playlist_paths = []
    
    # Check if the file exists
    if Path(file_name).exists():
        # Attempt to load JSON content
        try:
            with open(file_name, "r") as f:
                if f.read().strip():  # Check if the file is not empty
                    f.seek(0)  # Go back to the beginning of the file
                    playlist_paths = json.load(f)
        except json.JSONDecodeError:
            # Handle cases where the file contains invalid JSON
            print(f"Warning: {file_name} contains invalid JSON. Reinitializing.")
    
    # Add the new path to the list if it's not already there
    if new_path not in playlist_paths:
        playlist_paths.append(new_path)
    
    # Write the updated list back to the file
    with open(file_name, "w") as f:
        json.dump(playlist_paths, f, indent=4)


@app.route('/download', methods=['POST'])
def download():
    data = request.json

    url = data.get('url')
    path = data.get('path')

    # If the JSON file doesn't exist, create it
    if (not syncedDirsJson in os.listdir()):
            # Open the file in write mode, which creates the file if it doesn't exist
        with open(syncedDirsJson, "w") as file:
          pass  # The file is now created but empty

    
    try:
        # Check if the URL is a playlist (for syncing purposes)
        if ("/playlist/" in url) or ("/sets/" in url):
            add_path_to_json(syncedDirsJson, path)
            result = subprocess.run(["spotdl", "sync", url, "--output", path, "--save-file", os.path.join(path, "SyncData.spotdl")],
                                    check=True,
                                    stdout=subprocess.PIPE,  # Capture standard output
                                    stderr=subprocess.PIPE,  # Capture error output
                                    text=True  # Decode bytes to string
                                    )
        else:
            result = subprocess.run(["spotdl", "download", url, "--output", path],
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


@app.route('/sync-all', methods=['POST'])
def sync_all():
    if not syncedDirsJson in os.listdir():
        return jsonify({"status": "error", "message": "No directories to sync"}), 500
    
    with open(syncedDirsJson, "r") as f:
        playlist_paths = json.load(f)

    num_synced = 0

    for path in playlist_paths:

        if os.path.isdir(path):
            try:
                result = subprocess.run(["spotdl", "sync", os.path.join(path, "SyncData.spotdl"), "--output", path],
                                        check=True,
                                        stdout=subprocess.PIPE,  # Capture standard output
                                        stderr=subprocess.PIPE,  # Capture error output
                                        text=True  # Decode bytes to string
                                        )
                num_synced += 1

            except subprocess.CalledProcessError as e:
                # Log errors from the SCDL command
                logger.error("SCDL command failed!")
                logger.error("Error Output: %s", e.stderr)
                return jsonify({"status": "error", "message": "Download failed", "error": e.stderr}), 500
            
            except Exception as e:
                # Catch other unexpected errors
                logger.exception("An unexpected error occurred.")
                return jsonify({"status": "error", "message": "An unexpected error occurred", "error": str(e)}), 500
        
        else:
            # If the directory does not exist, log the issue and remove from the list
            logger.error(f"Directory {path} does not exist. Removing from list.")
            
            # Optionally remove the path from the playlist_paths list and update the JSON file
            playlist_paths = [p for p in playlist_paths if p != path]

            # Update the JSON file to reflect the removal of the invalid path
            with open(syncedDirsJson, "w") as f:
                json.dump(playlist_paths, f, indent=4)

    # Log and return successful output
    logger.info("Command executed successfully.")
    return jsonify({"status": "success", "message": "Successfully synced "+  f"{num_synced}" + " playlists"})

@app.route('/sync-selected', methods=['POST'])
def sync_selected():
    try:
        # Extract the JSON body
        data = request.get_json()
        if not data or "paths" not in data:
            return jsonify({"message": "Invalid request, no paths provided"}), 400

        # Get the list of paths
        paths = data["paths"]

        number_synced = 0

        for path in paths:
            try:
                result = subprocess.run(["spotdl", "sync", os.path.join(path, "SyncData.spotdl"), "--output", path],
                                        check=True,
                                        stdout=subprocess.PIPE,  # Capture standard output
                                        stderr=subprocess.PIPE,  # Capture error output
                                        text=True  # Decode bytes to string
                                        )
                number_synced += 1

            except subprocess.CalledProcessError as e:
                # Log errors from the SCDL command
                logger.error("SCDL command failed!")
                logger.error("Error Output: %s", e.stderr)
                return jsonify({"status": "error", "message": "Download failed", "error": e.stderr}), 500
            
            except Exception as e:
                # Catch other unexpected errors
                logger.exception("An unexpected error occurred.")
                return jsonify({"status": "error", "message": "An unexpected error occurred", "error": str(e)}), 500

        # Return a success message
        return jsonify({"message": "Successfully synced " + f"{number_synced}" + " playlists."}), 200

    except Exception as e:
        # Handle unexpected errors
        return jsonify({"message": f"An error occurred: {str(e)}"}), 500

if __name__ == '__main__':
    app.run(debug=True)