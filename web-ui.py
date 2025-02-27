import os
import json
from flask import Flask, render_template, request, redirect, url_for, flash, session
import threading
import logging
from datetime import datetime
import sys
import io
from contextlib import redirect_stdout

# Import the main script functionality
# Change this to match your actual script filename (without the .py extension)
import app13 as translator_script

app = Flask(__name__)
app.secret_key = os.urandom(24)  # For flash messages and session

# Ensure templates directory exists
os.makedirs('templates', exist_ok=True)

# Create templates/index.html
with open('templates/index.html', 'w') as f:
    f.write("""
<!DOCTYPE html>
<html>
<head>
    <title>Google Slides Translator</title>
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <link href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.0/dist/css/bootstrap.min.css" rel="stylesheet">
    <style>
        body { padding-top: 2rem; }
        .progress { height: 25px; }
        #consoleOutput {
            background-color: #f8f9fa;
            font-family: monospace;
            padding: 1rem;
            max-height: 400px;
            overflow-y: auto;
            white-space: pre-wrap;
            font-size: 0.85rem;
        }
        .alert-fixed {
            position: fixed;
            top: 0;
            left: 0;
            width: 100%;
            z-index: 9999;
            border-radius: 0;
        }
    </style>
</head>
<body>
    <div class="container">
        <h1 class="mb-4">Google Slides Translator</h1>
        
        {% with messages = get_flashed_messages(with_categories=true) %}
            {% if messages %}
                {% for category, message in messages %}
                    <div class="alert alert-{{ category }} alert-dismissible fade show" role="alert">
                        {{ message }}
                        <button type="button" class="btn-close" data-bs-dismiss="alert" aria-label="Close"></button>
                    </div>
                {% endfor %}
            {% endif %}
        {% endwith %}
        
        <div class="card mb-4">
            <div class="card-header">
                <h5>Translation Settings</h5>
            </div>
            <div class="card-body">
                <form method="post" action="/translate">
                    <div class="mb-3">
                        <label for="presentation_id" class="form-label">Google Slides Presentation ID</label>
                        <input type="text" class="form-control" id="presentation_id" name="presentation_id" required 
                               placeholder="e.g., 1rppKpwoUKmf65wAg7J9gSVpL279IBX4EmbWM7yGiS6s">
                        <div class="form-text">Find this in your Google Slides URL: https://docs.google.com/presentation/d/[PRESENTATION_ID]/edit</div>
                    </div>
                    <div class="row">
                        <div class="col-md-6">
                            <div class="mb-3">
                                <label for="source_language" class="form-label">Source Language</label>
                                <select class="form-select" id="source_language" name="source_language" required>
                                    <option value="en" selected>English</option>
                                    <option value="fr">French</option>
                                    <option value="es">Spanish</option>
                                    <option value="de">German</option>
                                    <option value="it">Italian</option>
                                    <option value="ja">Japanese</option>
                                    <option value="ko">Korean</option>
                                    <option value="zh">Chinese</option>
                                    <option value="ru">Russian</option>
                                    <option value="pt">Portuguese</option>
                                </select>
                            </div>
                        </div>
                        <div class="col-md-6">
                            <div class="mb-3">
                                <label for="target_language" class="form-label">Target Language</label>
                                <select class="form-select" id="target_language" name="target_language" required>
                                    <option value="en">English</option>
                                    <option value="fr">French</option>
                                    <option value="es">Spanish</option>
                                    <option value="de">German</option>
                                    <option value="it">Italian</option>
                                    <option value="ja" selected>Japanese</option>
                                    <option value="ko">Korean</option>
                                    <option value="zh">Chinese</option>
                                    <option value="ru">Russian</option>
                                    <option value="pt">Portuguese</option>
                                </select>
                            </div>
                        </div>
                    </div>
                    <div class="mb-3">
                        <label for="api_key" class="form-label">Anthropic API Key (optional)</label>
                        <input type="password" class="form-control" id="api_key" name="api_key" 
                               placeholder="If not provided, will use CLAUDE_API_KEY from environment">
                    </div>
                    <button type="submit" class="btn btn-primary">Start Translation</button>
                </form>
            </div>
        </div>
        
        {% if session.get('translation_running') %}
        <div class="card mb-4">
            <div class="card-header">
                <h5>Translation Progress</h5>
            </div>
            <div class="card-body">
                <div class="mb-3">
                    <label class="form-label">Progress:</label>
                    <div class="progress">
                        <div id="progressBar" class="progress-bar" role="progressbar" style="width: {{ session.get('progress', 0) }}%;" 
                             aria-valuenow="{{ session.get('progress', 0) }}" aria-valuemin="0" aria-valuemax="100">
                            {{ session.get('progress', 0) }}%
                        </div>
                    </div>
                </div>
                <div class="mb-3">
                    <label class="form-label">Console Output:</label>
                    <div id="consoleOutput" class="border rounded">{{ session.get('console_output', '') }}</div>
                </div>
                {% if session.get('result_url') %}
                <div class="mb-3">
                    <a href="{{ session.get('result_url') }}" target="_blank" class="btn btn-success">
                        Open Translated Presentation
                    </a>
                </div>
                {% endif %}
            </div>
        </div>
        {% endif %}
    </div>
    
    <script src="https://cdn.jsdelivr.net/npm/bootstrap@5.3.0/dist/js/bootstrap.bundle.min.js"></script>
    {% if session.get('translation_running') %}
    <script>
        // Auto-refresh to update progress
        setTimeout(function() {
            window.location.reload();
        }, 5000);  // Refresh every 5 seconds
    </script>
    {% endif %}
</body>
</html>
    """)

# Global variables to track translation process
translation_state = {
    'running': False,
    'progress': 0,
    'console_output': '',
    'result_url': None
}

# Custom stream handler to capture console output
class StringIOHandler(logging.StreamHandler):
    def __init__(self):
        self.string_io = io.StringIO()
        super().__init__(self.string_io)
    
    def get_output(self):
        self.flush()
        return self.string_io.getvalue()

# Create a custom stdout capture
class CaptureStdout:
    def __init__(self):
        self.buffer = io.StringIO()
    
    def __enter__(self):
        self.old_stdout = sys.stdout
        sys.stdout = self.buffer
        return self
    
    def __exit__(self, exc_type, exc_val, exc_tb):
        sys.stdout = self.old_stdout
    
    def get_output(self):
        return self.buffer.getvalue()

# Modify the tqdm class to update progress in our state
class WebUITqdm:
    def __init__(self, total, **kwargs):
        self.total = total
        self.n = 0
        self.description = kwargs.get('desc', '')
    
    def update(self, n):
        self.n += n
        if self.total > 0:
            translation_state['progress'] = int(100 * self.n / self.total)
    
    def set_description(self, desc):
        self.description = desc
    
    def close(self):
        pass

# Modified translate function that updates progress
def translate_with_progress(presentation_id, source_language, target_language, api_key=None):
    global translation_state
    
    # Reset state
    translation_state['running'] = True
    translation_state['progress'] = 0
    translation_state['console_output'] = 'Starting translation...\n'
    translation_state['result_url'] = None
    
    # Set API key if provided
    if api_key:
        os.environ["CLAUDE_API_KEY"] = api_key
    
    # Capture stdout
    with CaptureStdout() as capture:
        try:
            # Replace tqdm with our custom implementation
            original_tqdm = translator_script.tqdm
            translator_script.tqdm = WebUITqdm
            
            # Run the translation process
            slides_service, drive_service = translator_script.authenticate_google()
            extracted_text, slide_metadata = translator_script.extract_text(slides_service, presentation_id)
            translated_texts = translator_script.translate_text(extracted_text, slide_metadata, source_language, target_language)
            new_presentation_id = translator_script.update_slides(slides_service, drive_service, presentation_id, translated_texts, target_language)
            
            # Create the presentation URL
            presentation_url = f"https://docs.google.com/presentation/d/{new_presentation_id}/edit"
            translation_state['result_url'] = presentation_url
            
            # Restore original tqdm
            translator_script.tqdm = original_tqdm
            
            return True, presentation_url
            
        except Exception as e:
            error_msg = f"Error during translation: {str(e)}"
            print(error_msg)
            return False, error_msg
        finally:
            # Update console output
            translation_state['console_output'] += capture.get_output()
            translation_state['running'] = False

@app.route('/')
def index():
    # Update session with current translation state
    session['translation_running'] = translation_state['running']
    session['progress'] = translation_state['progress']
    session['console_output'] = translation_state['console_output']
    session['result_url'] = translation_state['result_url']
    return render_template('index.html')

@app.route('/translate', methods=['POST'])
def start_translation():
    if translation_state['running']:
        flash('Translation is already in progress', 'warning')
        return redirect(url_for('index'))
    
    presentation_id = request.form.get('presentation_id')
    source_language = request.form.get('source_language')
    target_language = request.form.get('target_language')
    api_key = request.form.get('api_key')
    
    if not presentation_id or not source_language or not target_language:
        flash('Please fill all required fields', 'danger')
        return redirect(url_for('index'))
    
    # Start translation in a separate thread to not block the web server
    thread = threading.Thread(
        target=translate_with_progress,
        args=(presentation_id, source_language, target_language, api_key)
    )
    thread.daemon = True
    thread.start()
    
    flash('Translation started successfully', 'success')
    return redirect(url_for('index'))

@app.route('/progress')
def get_progress():
    return {
        'running': translation_state['running'],
        'progress': translation_state['progress'],
        'console_output': translation_state['console_output'],
        'result_url': translation_state['result_url']
    }

if __name__ == '__main__':
    print("Starting Web UI on http://127.0.0.1:5000")
    print("Press Ctrl+C to stop")
    app.run(debug=True)
