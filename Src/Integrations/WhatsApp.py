from playwright.async_api import async_playwright
import asyncio
import time
import os
import base64
import mimetypes
from Src.Utilities.logger import setup_logger

class WhatsApp:

    def __init__(self):
        self._playwright = None
        self._browser = None
        self._context = None
        self._page = None
        self.logger = setup_logger("Integracion", "WhatsApp")

    async def conectar(self):
        if await self._pagina_activa():
            return True

        await self._limpiar_recursos()

        try:
            self.logger.info("Intentando conectar con el navegador (CDP: 9222)...")
            self._playwright = await async_playwright().start()
            
            try:
                self._browser = await self._playwright.chromium.connect_over_cdp("http://localhost:9222")
            except Exception as e:
                self.logger.error(f"No se pudo conectar al puerto 9222. ¿Está el navegador abierto? {e}")
                await self.cerrar()
                return False

            if not self._browser.contexts:
                self.logger.error("El navegador no tiene contextos activos.")
                await self.cerrar()
                return False

            self._context = self._browser.contexts[0]
            
            self._page = None
            for p in self._context.pages:
                if "whatsapp.com" in p.url:
                    self._page = p
                    break
            
            if not self._page:
                self._page = self._context.pages[0] if self._context.pages else await self._context.new_page()

            await self._page.bring_to_front()
            return await self._validar_whatsapp()

        except Exception as e:
            self.logger.error(f"Error fatal en conexión WhatsApp: {e}")
            await self.cerrar()
            return False

    async def _pagina_activa(self):
        try:
            return self._page and not self._page.is_closed()
        except:
            return False

    @property
    def page(self):
        return self._page

    async def _limpiar_recursos(self):
        self._browser = None
        self._context = None
        self._page = None

    async def cerrar(self):
        try:
            if self._browser:
                await self._browser.close()
            if self._playwright:
                await self._playwright.stop()
        except Exception as e:
            self.logger.warning(f"Aviso al cerrar: {e}")
        finally:
            self._playwright = None
            self._browser = None
            self._context = None
            self._page = None

    async def _validar_whatsapp(self):
        try:
            await self.page.wait_for_selector('div#side', timeout=5000)
            return True
        except:
            return await self._abrir_whatsapp()

    async def _abrir_whatsapp(self):
        if "whatsapp.com" not in self.page.url:
            await self.page.goto("https://web.whatsapp.com")

        try:
            await self.page.wait_for_selector('div#side', timeout=60000)
            return True
        except:
            self.logger.error("No se pudo abrir WhatsApp")
            return False

    async def _buscar_chat(self, nombre):
        page = self._page

        try:
            await page.wait_for_selector('#side', timeout=30000)
            await page.wait_for_selector('div[data-testid="loading"]', state="hidden", timeout=10000)
        except:
            pass

        search_selectors = [
            '#side div[contenteditable="true"]',
            'div[contenteditable="true"][data-tab="3"]',
            'div[data-testid="chat-list-search"]',
            'div[role="textbox"][aria-placeholder*="Busc"]',
            'div[role="textbox"][aria-label*="Busc"]'
        ]
        
        try:
            await page.click('#side', timeout=2000)
        except:
            pass

        try:
            await page.keyboard.press("Control+Alt+/")
            await page.wait_for_timeout(800)
        except:
            pass

        search = None
        for sel in search_selectors:
            try:
                el = page.locator(sel).first
                if await el.is_visible():
                    search = el
                    break
            except:
                continue
                
        if search:
            try:
                await search.click(force=True)
            except:
                pass

        await page.wait_for_timeout(500)
        
        await page.keyboard.press('Control+A')
        await page.keyboard.press('Backspace')
        await page.wait_for_timeout(300)

        await page.keyboard.type(nombre, delay=60)
        await page.wait_for_timeout(2000)

        chat_selector = f'span[title="{nombre}"]'
        try:
            contact = page.locator(chat_selector).first
            await contact.wait_for(state="visible", timeout=7000)
            await contact.click()
        except:
            self.logger.warning(f"No se encontró contacto visualmente con título '{nombre}', intentando Enter.")
            await page.keyboard.press('Enter')

        await page.wait_for_timeout(1500)
        await self._esperar_chat_abierto(nombre)

    async def _esperar_chat_abierto(self, nombre):
        try:
            header = self._page.locator('#main header')
            await header.wait_for(timeout=10000)
            
            titulo_elemento = header.locator('span[dir="auto"]').first
            await titulo_elemento.wait_for(state="visible", timeout=5000)
            titulo_actual = await titulo_elemento.text_content()
            
            if titulo_actual and nombre.lower() in titulo_actual.lower():
                self.logger.info(f"Confirmado: Chat '{titulo_actual}' correctamente abierto.")
                return True
            else:
                self.logger.warning(f"El chat abierto es '{titulo_actual}', pero se buscaba '{nombre}'.")
                return False
        except Exception as e:
            self.logger.warning(f"No se pudo confirmar visualmente el nombre del chat: {e}")
            return False

    async def _input_chat(self):
        selectores = [
            'div.lexical-rich-text-input div[contenteditable="true"]',
            'div[data-testid="conversation-compose-box-input"]',
            'div[contenteditable="true"][data-tab="10"]',
            '#main footer div[contenteditable="true"]',
            'div[title="Escribe un mensaje"]',
            'footer div[role="textbox"]'
        ]
        
        for sel in selectores:
            try:
                el = self._page.locator(sel).first
                await el.wait_for(state="visible", timeout=3000)
                return el
            except:
                continue
        
        return self._page.locator('div[contenteditable="true"]').last

    async def _enviar_texto(self, texto):
        box = await self._input_chat()
        await box.click()
        await box.fill("")
        await self._page.keyboard.insert_text(texto)
        await self._page.keyboard.press("Enter")

    async def _copiar_archivos_al_portapapeles(self, rutas):
        import subprocess
        script_lines = [
            "Add-Type -AssemblyName System.Windows.Forms",
            "[System.Windows.Forms.Clipboard]::Clear()",
            "$files = New-Object System.Collections.Specialized.StringCollection"
        ]
        for r in rutas:
            abs_path = os.path.abspath(r).replace("'", "''")
            script_lines.append(f"$files.Add('{abs_path}')")
        
        script_lines.append("[System.Windows.Forms.Clipboard]::SetFileDropList($files)")
        ps_code = "; ".join(script_lines)
        
        cmd = ["powershell", "-NoProfile", "-Command", ps_code]
        try:
            subprocess.run(cmd, creationflags=0x08000000)
        except Exception as e:
            self.logger.error(f"Error copiando al portapapeles: {e}")

    async def _enviar_archivos(self, rutas, mensaje=None):
        rutas_validas = [r for r in rutas if os.path.exists(r)]
        if not rutas_validas:
            self.logger.warning("Aviso: Ninguna ruta de archivo es válida.")
            return

        page = self._page
        input_box = await self._input_chat()
        await input_box.click()
        await page.wait_for_timeout(500)

        await self._copiar_archivos_al_portapapeles(rutas_validas)
        await page.wait_for_timeout(800)

        await page.keyboard.press("Control+V")
        await page.wait_for_timeout(3500)

        if mensaje:
            caption_selectors = [
                'div[data-tab="10"]',
                'div[data-tab="6"]',
                'div[contenteditable="true"][role="textbox"]',
            ]
            for sel in caption_selectors:
                try:
                    cap = page.locator(sel).last
                    if await cap.is_visible():
                        await cap.click()
                        await page.keyboard.insert_text(mensaje)
                        await page.wait_for_timeout(500)
                        break
                except:
                    continue

        await self._click_enviar()
        await page.wait_for_timeout(2000)

    async def _click_enviar(self):
        try:
            btn = self._page.locator('span[data-icon="send"]').last
            await btn.click()
        except:
            await self._page.keyboard.press("Enter")

    async def _esperar_envio(self):
        await asyncio.sleep(2)
        try:
            await self._page.locator('span[data-icon="msg-time"]').wait_for(state="hidden", timeout=10000)
        except:
            pass

    async def enviar(self, chat, mensaje=None, archivos=None):
        if not await self.conectar():
            return False
        try:
            await self._buscar_chat(chat)

            if archivos:
                await self._enviar_archivos(archivos, mensaje)
            elif mensaje:
                await self._enviar_texto(mensaje)

            await self._esperar_envio()
            return True

        except Exception as e:
            import traceback
            self.logger.critical(f"================ ERROR EN WHATSAPP ================")
            self.logger.critical(f"Error: {str(e)}")
            self.logger.critical(traceback.format_exc())
            return False

    async def mensaje(self, chat, texto):
        return await self.enviar(chat, mensaje=texto)

    async def archivo(self, chat, ruta, texto=""):
        return await self.enviar(chat, mensaje=texto, archivos=[ruta])

    async def varios(self, chat, rutas, texto=""):
        return await self.enviar(chat, mensaje=texto, archivos=rutas)

    async def cerrar_sesion(self):
        await self.cerrar()

    async def close(self):
        await self.cerrar()

WhatsAppSender = WhatsApp
