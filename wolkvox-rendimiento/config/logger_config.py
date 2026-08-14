import logging
from .paths import ROOT_DIR
from datetime import datetime, timedelta
import os

def setup_logger():
    log_dir = ROOT_DIR / 'logs'
    log_dir.mkdir(parents=True, exist_ok=True)

    # Fecha de hoy para nombrar el archivo
    current_date = datetime.today().strftime('%Y-%m-%d')
    log_filename = f'app_{current_date}.log'
    log_path = log_dir / log_filename

    # Creamos el logger base
    logger = logging.getLogger()
    logger.setLevel(logging.INFO)

    # Limpiamos handlers previos si hay
    if logger.hasHandlers():
        logger.handlers.clear()

    # === LIMPIEZA: eliminar logs con más de 3 días de antigüedad ===
    keep_dates = { 
        (datetime.today() - timedelta(days=offset)).strftime('%Y-%m-%d') 
        for offset in range(3)
    }

    for file in os.listdir(log_dir):
        if file.startswith("app_") and file.endswith(".log"):
            file_date = file.replace("app_", "").replace(".log", "")
            if file_date not in keep_dates:
                try:
                    os.remove(log_dir / file)
                except Exception as e:
                    print(f"No se pudo eliminar el log antiguo {file}: {e}")

    # Handler para archivo (sobrescribir el del día)
    file_handler = logging.FileHandler(log_path, mode='a', encoding='utf-8')
    file_formatter = logging.Formatter('%(asctime)s | %(levelname)s | %(name)s | %(message)s', datefmt='%Y-%m-%d %H:%M:%S')
    file_handler.setFormatter(file_formatter)

    # Handler para consola
    console_handler = logging.StreamHandler()
    console_formatter = logging.Formatter('%(levelname)s | %(message)s')
    console_handler.setFormatter(console_formatter)

    # === FILTRO para ignorar mensajes relacionados con plausible.io ===
    class IgnorePlausibleWarnings(logging.Filter):
        def filter(self, record):
            return "plausible.io" not in record.getMessage()
    
    file_handler.addFilter(IgnorePlausibleWarnings())
    console_handler.addFilter(IgnorePlausibleWarnings())

    # === SILENCIAR logs de librerías externas innecesarias ===
    logging.getLogger("urllib3").setLevel(logging.ERROR)
    logging.getLogger("selenium").setLevel(logging.ERROR)
    logging.getLogger("requests").setLevel(logging.ERROR)
    logging.getLogger("websockets").setLevel(logging.ERROR)

    # Agregar handlers al logger base
    logger.addHandler(file_handler)
    logger.addHandler(console_handler)