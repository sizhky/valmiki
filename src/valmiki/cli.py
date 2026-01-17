"""Command-line interface for Valmiki Ramayana Reader."""

import sys


def serve(reload=True, host="0.0.0.0", port=8000):
    """Start the web server."""
    try:
        import uvicorn
    except ImportError:
        print("Error: uvicorn is required to run the web server.")
        print("Install it with: pip install uvicorn")
        sys.exit(1)
    
    from .app import app
    
    print("🕉️  Starting Valmiki Ramayana Reader...")
    print(f"📖 Visit: http://localhost:{port}")
    if reload:
        print("🔄 Auto-reload enabled")
    print(f"🌐 Listening on: {host}:{port}")
    print("⌨️  Press Ctrl+C to stop")
    print()
    
    try:
        uvicorn.run(
            "valmiki.app:app" if reload else app,
            host=host,
            port=port,
            reload=reload
        )
    except KeyboardInterrupt:
        print("\n👋 Stopped server")


def main():
    """Main CLI entry point."""
    args = sys.argv[1:]
    
    # Parse flags
    reload = True
    host = "0.0.0.0"
    port = 8000
    
    # Extract --host
    for i, arg in enumerate(args):
        if arg == '--host' and i + 1 < len(args):
            host = args[i + 1]
            args = args[:i] + args[i+2:]
            break
    
    # Extract --port
    for i, arg in enumerate(args):
        if arg == '--port' and i + 1 < len(args):
            try:
                port = int(args[i + 1])
            except ValueError:
                print(f"Error: Invalid port number '{args[i + 1]}'")
                sys.exit(1)
            args = args[:i] + args[i+2:]
            break
        elif arg.startswith('--port='):
            try:
                port = int(arg.split('=')[1])
            except ValueError:
                print(f"Error: Invalid port number '{arg.split('=')[1]}'")
                sys.exit(1)
            args = [a for a in args if not a.startswith('--port=')]
            break
    
    # Remove reload flags from args (no-op but keeps CLI compatible)
    args = [a for a in args if a not in ['--reload', '-r']]
    
    if not args or args[0] in ['serve', 'start', 'run']:
        serve(reload=reload, host=host, port=port)
    elif args[0] in ['--help', '-h', 'help']:
        print("""
Valmiki Ramayana Reader - CLI

Usage:
    valmiki              Start the web server (default)
    valmiki serve        Start the web server
    valmiki start        Start the web server
    valmiki run          Start the web server
    valmiki --port PORT  Specify port (default: 8000)
    valmiki --host HOST  Specify host (default: 0.0.0.0)
    valmiki --help       Show this help message

Examples:
    valmiki                      # Start server on http://localhost:8000
    valmiki --port 3000          # Start on port 3000
    valmiki --port=3000          # Alternative syntax
    valmiki --host 127.0.0.1     # Listen only on localhost
    valmiki --port 3000          # Combine options
    
Once started, visit http://localhost:PORT in your browser.
""")
    elif args[0] in ['--version', '-v', 'version']:
        from . import __version__
        print(f"valmiki {__version__}")
    else:
        print(f"Unknown command: {args[0]}")
        print("Run 'valmiki --help' for usage information")
        sys.exit(1)


if __name__ == '__main__':
    main()
