"""logging utils module."""

import logging
from typing import Union


def get_logger(name: Union[str, None] = None) -> logging.Logger:
    logger = logging.getLogger(name)
    logger.setLevel(logging.INFO)
    logger.propagate = False

    handler = logging.StreamHandler()
    handler.setFormatter(
        logging.Formatter(
            '[slyme {levelname}] - {asctime} - "{filename}:{lineno}" - {message}',
            datefmt="%Y/%m/%d %H:%M:%S",
            style="{",
        )
    )
    logger.addHandler(handler)
    return logger
