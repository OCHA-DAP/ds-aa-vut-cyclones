import logging
import os


def load_boolean_env(var_name: str, default: bool) -> bool:
    var_value = os.getenv(var_name)
    if var_value is None:
        return default
    if var_value.lower() in ("true", "1", "yes"):
        return True
    elif var_value.lower() in ("false", "0", "no"):
        return False
    else:
        return default


def get_logger(name=__name__):
    logger = logging.getLogger(name)
    if not logger.handlers:
        logger.setLevel(logging.INFO)  # Or DEBUG/ERROR as needed

        handler = logging.StreamHandler()
        formatter = logging.Formatter(
            "%(asctime)s - %(name)s - %(levelname)s - %(message)s"
        )
        handler.setFormatter(formatter)
        logger.addHandler(handler)
        logger.propagate = False  # Prevent double logs if imported in __main__

    return logger
