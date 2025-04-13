import logging

__logger: logging.Logger = None

def setup_logging(file: str):
    """配置日志记录"""
    global __logger  # Ensure the global variable is updated
    print(f"zoudao日志文件: {file}")
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
        handlers=[
            logging.FileHandler(file, encoding='utf-8'),
            logging.StreamHandler()
        ]
    )
    __logger = logging.getLogger()

def logI(msg: str, flush: bool = False):
    """记录信息级别日志"""
    if __logger is None:
        raise RuntimeError("Logger is not initialized. Call setup_logging() first.")
    __logger.info(msg)
    if flush:
        for handler in __logger.handlers:
            handler.flush()


