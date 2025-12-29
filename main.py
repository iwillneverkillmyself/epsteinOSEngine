"""Main entry point for running the API server."""
import uvicorn
import argparse
from config import Config

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Run the OCR RAG API server")
    parser.add_argument(
        "--host",
        default=Config.API_HOST,
        help=f"Host to bind to (default: {Config.API_HOST})"
    )
    parser.add_argument(
        "--port",
        type=int,
        default=Config.API_PORT,
        help=f"Port to bind to (default: {Config.API_PORT})"
    )
    parser.add_argument(
        "--no-reload",
        action="store_true",
        help="Disable auto-reload"
    )
    
    args = parser.parse_args()
    
    uvicorn.run(
        "api.main:app",
        host=args.host,
        port=args.port,
        reload=not args.no_reload
    )

