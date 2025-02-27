#!/usr/bin/env python3
"""
Launcher script that allows running either the CLI version or Web UI
"""
import sys
import os
import argparse

def main():
    parser = argparse.ArgumentParser(description="Google Slides Translator")
    parser.add_argument("--web", action="store_true", help="Launch the web UI instead of CLI")
    parser.add_argument("--port", type=int, default=5000, help="Port for web UI (default: 5000)")
    parser.add_argument("--host", default="127.0.0.1", help="Host for web UI (default: 127.0.0.1)")
    
    args = parser.parse_args()
    
    if args.web:
        print(f"Starting Web UI on http://{args.host}:{args.port}")
        # Import the web UI module
        from web_ui import app
        app.run(host=args.host, port=args.port, debug=False)
    else:
        # Import and run the CLI script
        import app10
        if __name__ == "__main__":
            # The script will execute from its main block

if __name__ == "__main__":
    main()
