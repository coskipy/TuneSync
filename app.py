from flask import Flask, render_template, request, jsonify
import subprocess

app = Flask(__name__)

@app.route('/')
def index():
    return render_template('index.html')

@app.route('/download', methods=['POST'])
def download():
    data = request.json
    playlist_url = data.get('url')
    output_dir = '/Users/pete/Desktop'  # Default to local desktop folder
    try:
        # Run SCDL command
        subprocess.run(['scdl', '-l', playlist_url, '--path', output_dir], check=True)
        return jsonify({"status": "success", "message": "Download complete"})
    except subprocess.CalledProcessError as e:
        return jsonify({"status": "error", "message": str(e)}), 500

if __name__ == '__main__':
    app.run(debug=True)
