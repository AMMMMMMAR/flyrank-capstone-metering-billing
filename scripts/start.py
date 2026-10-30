"""One command to run the whole system: apply the migrations, then start the server.

Run it with:  python -m scripts.start
Optional environment variables: HOST (default 127.0.0.1) and PORT (default 8000).
Run it from the project folder (the folder that contains alembic.ini).
"""
import os

import uvicorn
from alembic import command
from alembic.config import Config


def main() -> None:
    command.upgrade(Config("alembic.ini"), "head")  # safe to repeat: nothing to do when up to date
    uvicorn.run(
        "app.main:app",
        host=os.environ.get("HOST", "127.0.0.1"),
        port=int(os.environ.get("PORT", "8000")),
    )


if __name__ == "__main__":
    main()
