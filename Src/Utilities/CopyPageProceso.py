import os
import sys
import time
import json
import shutil
import threading
import logging
from pathlib import Path
from datetime import datetime, timedelta
from concurrent.futures import ThreadPoolExecutor
from Src.Integrations.WhatsApp import WhatsApp
from Src.Utilities.logger import setup_logger

def resource_path(filename):
    if hasattr(sys, "_MEIPASS"):
        return os.path.join(sys._MEIPASS, filename)
    return os.path.abspath(filename)


def formato_bytes(bytes_):
    if bytes_ >= 1024**3:
        return f"{bytes_ / (1024**3):.2f} GB"
    elif bytes_ >= 1024**2:
        return f"{bytes_ / (1024**2):.2f} MB"
    elif bytes_ >= 1024:
        return f"{bytes_ / 1024:.2f} KB"
    return f"{bytes_} B"


def enviar_correo_error(archivo_error, smtp_service, asunto):
    logger = logging.getLogger("Tareas.CopyPageProceso")
    try:
        mensaje = (
            "Estimados,\n\n"
            "Se adjunta el resultado del proceso de copia con los errores encontrados.\n\n"
            "Saludos."
        )

        smtp_service.send_mail(
            subject=asunto,
            to=smtp_service.error_recipients,
            message=mensaje,
            attachments=archivo_error
        )

        logger.info(f"Correo enviado a {smtp_service.error_recipients}")

    except Exception as e:
        logger.error(f"Fallo al enviar correo: {e}")


def construir_mensaje_resumen(tareas_ok, tareas_error):
    ahora = datetime.now()
    fecha = ahora.strftime('%d-%m-%Y')
    hora = ahora.strftime('%H:%M')
    
    mensaje = (
        f"*Resumen de Copiado Pegado de PROD a DESA*\n\n"
        f"fecha: {fecha}\n"
        f"hora: {hora}\n\n"
        f"*Copiado de archivos*\n"
        f"----------------------------------------\n"
        f"*Estado De Copiado*\n"
    )

    all_tareas = tareas_ok + tareas_error
    for i, t in enumerate(all_tareas, 1):
        if t in tareas_ok:
            mensaje += f"{i}. [EXITO ]: {t['nombre']}\n"
        else:
            num_errores = t.get("num_errores", 0)
            mensaje += f"{i}. [ERROR ]: {t['nombre']} ({num_errores} errores)\n"
    
    mensaje += "---------------------------------------\n"
    mensaje += "*Detalle De Incremento*\n"
    
    for i, t in enumerate(all_tareas, 1):
        bytes_val = t.get("bytes_copiados", 0)
        gb_val = bytes_val / (1024**3)
        mensaje += f"{i}. incremento: {gb_val:,.3f} (Gbytes)\n"
        
    mensaje += "--------------------------------------\n"
    mensaje += "*Espacio de Memoria*\n"
    
    drives_processed = set()
    drive_idx = 1
    for t in all_tareas:
        destino = t.get("destino")
        if not destino:
            continue
            
        drive_letter = os.path.splitdrive(destino)[0]
        if not drive_letter and destino.startswith("\\\\"):
            parts = destino.split('\\')
            if len(parts) > 3:
                drive_letter = f"\\\\{parts[2]}\\{parts[3]}"
            else:
                drive_letter = destino
        
        if drive_letter and drive_letter not in drives_processed:
            try:
                usage = shutil.disk_usage(destino)
                free_gb = usage.free / (1024**3)
                display_drive = drive_letter.replace(":", "") if ":" in drive_letter else drive_letter
                mensaje += f"{drive_idx}. [{display_drive}]: {free_gb:,.2f} (Gbytes)\n"
                drives_processed.add(drive_letter)
                drive_idx += 1
            except:
                pass
                
    return mensaje


async def enviar_whatsapp_resumen_tareas(tareas_exitosas, tareas_con_errores, wa_config):
    try:
        if not wa_config.get("Activo"):
            return
        chats = wa_config.get("numero", [])
        if not chats:
            return

        enviar_con_texto = wa_config.get("texto", True)
        archivos_envio = []

        for t_err in tareas_con_errores:
            if t_err.get('log_path') and os.path.exists(t_err['log_path']):
                archivos_envio.append(t_err['log_path'])

        sender = WhatsApp()
        if not await sender.conectar():
            logging.getLogger("Tareas.CopyPageProceso").error("No se pudo conectar a WhatsApp para enviar el resumen.")
            return

        mensaje = construir_mensaje_resumen(tareas_exitosas, tareas_con_errores) if enviar_con_texto else None

        if mensaje:
            project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..'))
            mensajes_dir = os.path.join(project_root, "Mensajes")
            os.makedirs(mensajes_dir, exist_ok=True)
            ruta_mensaje = os.path.join(mensajes_dir, f"Resumen-copypage.txt")
            with open(ruta_mensaje, "w", encoding="utf-8") as f:
                f.write(mensaje)

        for chat in chats:
            await sender.enviar(chat, mensaje=mensaje, archivos=archivos_envio)
        
        await sender.cerrar()

    except Exception as e:
        logging.getLogger("Tareas.CopyPageProceso").error(f"Fallo al enviar mensaje de WhatsApp: {e}")


def crear_rutas_logs(nombre):
    project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..'))
    storage = os.path.join(project_root, "Storage")
    os.makedirs(storage, exist_ok=True)
    
    err = os.path.join(storage, f"ListaArchivoErrores-{nombre}.txt")
    return err


def validar_archivo(archivo, solo_qvd, dias, fecha_actual):
    if not archivo.is_file():
        return False

    if solo_qvd and not archivo.name.lower().endswith(".qvd"):
        return False

    if dias is not None:
        mtime = datetime.fromtimestamp(archivo.stat().st_mtime)
        if mtime < fecha_actual - timedelta(days=dias):
            return False
    return True


def copiar_archivos_modificados(nombre, origen, destino, dias_para_considerar=None, solo_qvd=False):
    try:
        inicio = time.time()
        fecha_actual = datetime.now()
        origen_path = Path(origen).resolve()
        destino_path = Path(destino).resolve()

        os.makedirs(destino, exist_ok=True)

        err_path = crear_rutas_logs(nombre)
        logger = setup_logger("CopyPage", nombre)
        
        total_bytes_copiados = 0
        copiados, omitidos = 0, 0
        errores = {}
        todos_los_errores = []
        
        _, _, libre = shutil.disk_usage(destino)
        
        lock = threading.Lock()
        dir_cache = {str(destino_path)}

        def proceso_archivo(archivo):
            nonlocal libre, copiados, omitidos, total_bytes_copiados
            try:
                try:
                    if destino_path in archivo.resolve().parents or archivo.resolve() == destino_path:
                        return None
                except Exception:
                    pass

                stat = archivo.stat()
                if not stat.st_size and not archivo.is_file():
                    return None

                if solo_qvd and not archivo.name.lower().endswith(".qvd"):
                    return None

                if dias_para_considerar is not None:
                    mtime = datetime.fromtimestamp(stat.st_mtime)
                    if mtime < fecha_actual - timedelta(days=dias_para_considerar):
                        return None

                rel_path = os.path.relpath(archivo, origen)
                destino_ruta = os.path.join(destino, rel_path)
                
                parent_dir = os.path.dirname(destino_ruta)
                if parent_dir not in dir_cache:
                    with lock:
                        if parent_dir not in dir_cache:
                            os.makedirs(parent_dir, exist_ok=True)
                            dir_cache.add(parent_dir)

                tam_origen = stat.st_size
                tam_dest = os.path.getsize(destino_ruta) if os.path.exists(destino_ruta) else -1

                if tam_origen == tam_dest:
                    with lock:
                        omitidos += 1
                    return f"OMITIDO {archivo}"

                diff = max(tam_origen - (tam_dest if tam_dest > 0 else 0), 0)

                with lock:
                    if libre < diff:
                        errores.setdefault("DISCO LLENO", []).append(str(archivo))
                        todos_los_errores.append(str(archivo))
                        return f"ERROR espacio {archivo}"

                shutil.copy2(str(archivo), str(destino_ruta))
                
                with lock:
                    copiados += 1
                    libre -= diff
                    total_bytes_copiados += tam_origen
                return f"COPIADO {archivo} ({formato_bytes(tam_origen)})"

            except Exception as e:
                with lock:
                    errores.setdefault("GENERAL", []).append(str(archivo))
                    todos_los_errores.append(str(archivo))
                return f"ERROR {archivo} {e}"

        logger.info(f"Escaneando archivos en {origen}...")
        archivos_a_procesar = list(Path(origen).rglob("*"))
        logger.info(f"Iniciando escaneo de {len(archivos_a_procesar)} elementos")
        
        with ThreadPoolExecutor(max_workers=10) as executor:
            resultados = list(executor.map(proceso_archivo, archivos_a_procesar))
            
            for res in resultados:
                if res:
                    logger.info(res)

        if errores:
            with open(err_path, "w", encoding="utf-8") as f_err:
                json.dump(todos_los_errores, f_err, indent=4)

        logger.info(f"Tarea '{nombre}' finalizada: {copiados} copiados, {omitidos} omitidos, {len(todos_los_errores)} errores.")
        logger.info(f"Tiempo invertido: {time.time() - inicio:.2f}s")

        return err_path, bool(errores), total_bytes_copiados

    except Exception as e:
        logging.getLogger("CopyPage").error(f"Fallo general en la copia de archivos: {e}")
        return None, False, 0