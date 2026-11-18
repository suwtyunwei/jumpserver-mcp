import uvicorn

from .config import settings
from .server import create_app


def main() -> None:
    app = create_app()
    uvicorn.run(app, host=settings.server_host, port=settings.server_port)


if __name__ == "__main__":
    main()
