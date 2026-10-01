# Written by KiymaliLahmacun

import os
import sys

import uvicorn
from dotenv import load_dotenv

load_dotenv()


def main():
    host = os.getenv("HOST", "0.0.0.0")
    port = int(os.getenv("PORT", 8000))

    print("=" * 50)
    print("  AI Stress Test Server")
    print(f"  http://localhost:{port}")
    print("=" * 50)

    try:
        uvicorn.run(
            "server:app",
            host=host,
            port=port,
            log_level="info",
            access_log=True,
        )
    except KeyboardInterrupt:
        print("\n[Server] Shutting down gracefully...")
        sys.exit(0)


if __name__ == "__main__":
    main()
