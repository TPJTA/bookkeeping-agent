import logging
import threading

import uvicorn

from src.channels.feishu.app import main as feishu_main
from src.channels.web.app import app as web_app
from src.config import CONFIG

logger = logging.getLogger(__name__)


def main() -> None:
    logging.basicConfig(level=logging.INFO)
    threading.Thread(target=feishu_main, daemon=True, name="feishu-ws").start()
    logger.info("starting web server on %s:%s", CONFIG.web_host, CONFIG.web_port)
    uvicorn.run(web_app, host=CONFIG.web_host, port=CONFIG.web_port)


if __name__ == "__main__":
    main()
