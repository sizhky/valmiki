"""Command-line interface for Valmiki Ramayana Reader."""

import sys


def serve(reload=False):
    """Start the web server."""
    try:
        import uvicorn
    except ImportError:
        print("Error: uvicorn is required to run the web server.")
        print("Install it with: pip install uvicorn")
        sys.exit(1)
    
    from .app import app
    
    print("🕉️  Starting Valmiki Ramayana Reader...")
    print("📖 Visit: http://localhost:8000")
    if reload:
        print("🔄 Auto-reload enabled")
    print("⌨️  Press Ctrl+C to stop")
    print()
    
    try:
        uvicorn.run(
            "valmiki.app:app" if reload else app,
            host="0.0.0.0",
            port=8000,
            reload=reload
        )
    except KeyboardInterrupt:
        print("\n👋 Stopped server")


def main():
    """Main CLI entry point."""
    args = sys.argv[1:]
    
    # Check for reload flag
    reload = '--reload' in args or '-r' in args
    if reload:
        args = [a for a in args if a not in ['--reload', '-r']]
    
    if not args or args[0] in ['serve', 'start', 'run']:
        serve(reload=reload)
    elif args[0] in ['--help', '-h', 'help']:
        print("""
Valmiki Ramayana Reader - CLI

Usage:
    valmiki              Start the web server (default)
    valmiki serve        Start the web server
    valmiki start        Start the web server
    valmiki run          Start the web server
    valmiki --reload     Start with auto-reload (dev mode)
    valmiki -r           Start with auto-reload (dev mode)
    valmiki --help       Show this help message

Examples:
    valmiki              # Start server on http://localhost:8000
    valmiki --reload     # Start with auto-reload for development
    
Once started, visit http://localhost:8000 in your browser.
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
