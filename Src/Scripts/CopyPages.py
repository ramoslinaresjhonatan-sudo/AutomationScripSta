import os
import sys
import json
import asyncio

root_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..'))
if root_dir not in sys.path:
    sys.path.append(root_dir)

import Src.Utilities.CopyPageProceso as CopyPageProceso
from Src.Integrations.Correo import Correo
from Src.Utilities.logger import setup_logger

config_path = os.path.join(root_dir, "Config", "copypage.json")
correo_config_path = os.path.join(root_dir, "Config", "Correo.json")

with open(config_path, "r", encoding="utf-8") as f:
    config = json.load(f)

correo_cfg = {}
if os.path.exists(correo_config_path):
    with open(correo_config_path, "r", encoding="utf-8") as f:
        correo_cfg = json.load(f)

correo_service = Correo(
    server=correo_cfg.get("server"),
    port=correo_cfg.get("port"),
    email_address=correo_cfg.get("email_address"),
    display_name=correo_cfg.get("email_address"),
    error_recipients=";".join(config.get("logsTareas", []))
)

whatsapp_cfg = config.get("whatsapp", {})
asunto_correo = config.get("asuntoCorreo", "Resultado del proceso de copia")

async def main():
    logger = setup_logger("Tareas", "CopyPages")
    logger.info("Iniciando proceso de copia de páginas...")
    
    tareas_exitosas = []
    tareas_con_errores = []

    for i, item in enumerate(config["datos"]):
        nombre = item["nombre"]
        origen = item["carpeta_origen"]
        destino = item["carpeta_destino"]
        dias = item.get("dias_para_considerar")
        solo_qvd = item.get("solo_QVD", False)

        error_log_path, tiene_errores, bytes_copiados = CopyPageProceso.copiar_archivos_modificados(
            nombre,
            origen,
            destino,
            dias_para_considerar=dias,
            solo_qvd=solo_qvd
        )

        if error_log_path:
            if tiene_errores:
                 logger.error(f"Enviando reporte de errores para tarea '{nombre}'...")
                 CopyPageProceso.enviar_correo_error(error_log_path, correo_service, asunto_correo)
            
            tarea_info = {
                "nombre": nombre,
                "bytes_copiados": bytes_copiados,
                "destino": destino,
                "log_path": error_log_path if tiene_errores else None,
                "num_errores": len(todos_los_errores) if todos_los_errores else 0
            }
            
            if tiene_errores:
                 tareas_con_errores.append(tarea_info)
            else:
                 tareas_exitosas.append(tarea_info)

    if tareas_exitosas or tareas_con_errores:
        logger.info("Enviando resumen agrupado por WhatsApp...")
        await CopyPageProceso.enviar_whatsapp_resumen_tareas(tareas_exitosas, tareas_con_errores, whatsapp_cfg)

    logger.info("Proceso completado")

if __name__ == "__main__":
    asyncio.run(main())