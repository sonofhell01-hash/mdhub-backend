from __future__ import annotations

import os
import re
import socket
import subprocess
import sys
import time
from contextlib import contextmanager, nullcontext
from dataclasses import dataclass
from pathlib import Path

from src.core.config import settings
from src.services.whatsapp.phone_utils import format_brazil_whatsapp


class WhatsAppSendError(RuntimeError):
    pass


class WhatsAppSendUncertainError(WhatsAppSendError):
    """O clique pode ter sido aceito; repetir automaticamente seria inseguro."""


@dataclass
class WhatsAppSendResult:
    status: str
    telefone: str
    attempts: int
    message: str


def sanitize_for_chromedriver(text: str) -> str:
    """Remove non-BMP characters that ChromeDriver cannot type with send_keys."""
    if not text:
        return ""
    return "".join(char for char in str(text) if ord(char) <= 0xFFFF)


@contextmanager
def _interprocess_send_lock(timeout: float = 30.0):
    """Impede API e worker de controlarem a mesma aba simultaneamente."""
    profile = Path(settings.whatsapp_chrome_profile)
    if not profile.is_absolute():
        profile = Path(__file__).resolve().parents[4] / profile
    profile.mkdir(parents=True, exist_ok=True)
    lock_path = profile.parent / "whatsapp_send.lock"
    handle = open(lock_path, "a+b")
    handle.seek(0, os.SEEK_END)
    if handle.tell() == 0:
        handle.write(b"0")
        handle.flush()

    deadline = time.monotonic() + timeout
    acquired = False
    try:
        if os.name == "nt":
            import msvcrt

            while time.monotonic() < deadline:
                try:
                    handle.seek(0)
                    msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
                    acquired = True
                    break
                except OSError:
                    time.sleep(0.1)
        else:
            import fcntl

            while time.monotonic() < deadline:
                try:
                    fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                    acquired = True
                    break
                except BlockingIOError:
                    time.sleep(0.1)
        if not acquired:
            raise WhatsAppSendError("Outro envio do WhatsApp esta em andamento; tente novamente em instantes.")
        yield
    finally:
        if acquired:
            handle.seek(0)
            if os.name == "nt":
                import msvcrt

                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl

                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        handle.close()


class WhatsAppWebSender:
    """Controlled WhatsApp Web sender for server-side queue processing."""

    def __init__(self) -> None:
        self.driver = None
        self.wait = None
        self.port = settings.whatsapp_chrome_debug_port
        self.current_phone = None

    def send(
        self,
        *,
        telefone: str,
        mensagem: str,
        acquire_lock: bool = True,
    ) -> WhatsAppSendResult:
        phone = format_brazil_whatsapp(telefone)
        if not phone:
            raise WhatsAppSendError("Telefone invalido para WhatsApp.")

        lock_context = _interprocess_send_lock() if acquire_lock else nullcontext()
        with lock_context:
            last_error = ""
            for attempt in range(1, settings.whatsapp_max_attempts + 1):
                try:
                    self._ensure_driver()
                    self._send_once(phone, mensagem)
                    return WhatsAppSendResult(
                        status="enviado",
                        telefone=phone,
                        attempts=attempt,
                        message="Mensagem enviada e confirmada pelo WhatsApp Web.",
                    )
                except WhatsAppSendUncertainError:
                    raise
                except Exception as exc:
                    last_error = self._short_error(exc)
                    time.sleep(0.6)

        raise WhatsAppSendError(last_error or "Falha desconhecida no envio WhatsApp.")

    def _short_error(self, exc: Exception) -> str:
        raw = str(exc or "").strip()
        message = raw.splitlines()[0] if raw else "erro sem detalhes"
        return f"{exc.__class__.__name__}: {message}"[:500]

    def _ensure_driver(self) -> None:
        if self.driver:
            try:
                _ = self.driver.window_handles
                return
            except Exception:
                self.driver = None
                self.wait = None
                self.current_phone = None

        self._ensure_chrome()

        try:
            from selenium import webdriver
            from selenium.webdriver.support.ui import WebDriverWait
        except ImportError as exc:
            raise WhatsAppSendError("Dependencias Selenium nao instaladas no ambiente do servidor.") from exc

        options = webdriver.ChromeOptions()
        options.add_experimental_option("debuggerAddress", f"127.0.0.1:{self.port}")
        self.driver = webdriver.Chrome(options=options)
        self.wait = WebDriverWait(self.driver, 15)
        self._switch_to_whatsapp_tab()

        if not self._wait_login(settings.whatsapp_wait_initial_seconds):
            raise WhatsAppSendError("WhatsApp Web nao esta logado. Abra a sessao e escaneie o QR Code.")

    def _ensure_chrome(self) -> None:
        if self._is_port_open(self.port):
            return

        chrome_path = self._find_chrome()
        if not chrome_path:
            raise WhatsAppSendError("Google Chrome nao encontrado no servidor.")

        profile = Path(settings.whatsapp_chrome_profile)
        if not profile.is_absolute():
            profile = Path(__file__).resolve().parents[4] / profile
        profile.mkdir(parents=True, exist_ok=True)

        cmd = [
            chrome_path,
            f"--remote-debugging-port={self.port}",
            f"--user-data-dir={str(profile)}",
            "https://web.whatsapp.com",
        ]
        subprocess.Popen(
            cmd,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0,
        )
        time.sleep(4)
        if not self._is_port_open(self.port):
            raise WhatsAppSendError("Chrome abriu, mas a porta de debug nao ficou disponivel.")

    def _send_once(self, phone: str, message: str) -> None:
        from selenium.webdriver.common.by import By
        from selenium.webdriver.support import expected_conditions as EC
        from selenium.common.exceptions import StaleElementReferenceException, TimeoutException

        self._switch_to_whatsapp_tab()
        if self.current_phone != phone and not self._current_tab_has_phone(phone):
            self.driver.get(f"https://web.whatsapp.com/send?phone={phone}")
            self.current_phone = phone
        else:
            self.current_phone = phone

        message = sanitize_for_chromedriver(message)
        self._find_message_box()
        self._clear_message_box()
        self._type_message(message)
        time.sleep(0.35)
        typed_text = self._message_box_text()
        if self._compact_text(typed_text) != self._compact_text(message):
            divergent_text = typed_text
            self._clear_message_box()
            raise WhatsAppSendError(
                "Campo do WhatsApp ficou com texto divergente; envio bloqueado para evitar mensagem duplicada. "
                f"Tamanho esperado: {len(message)}; observado: {len(divergent_text)}."
            )

        send_button = None
        page_hidden = bool(self.driver.execute_script("return document.hidden"))
        send_xpaths = (
            '//button[@aria-label="Enviar" or @aria-label="Send"]',
            '//span[@data-icon="send"]/ancestor::button',
            '//footer//button[.//span[@data-icon="send"]]',
        )
        for xpath in send_xpaths:
            try:
                condition = (
                    EC.presence_of_element_located((By.XPATH, xpath))
                    if page_hidden
                    else EC.element_to_be_clickable((By.XPATH, xpath))
                )
                send_button = self.wait.until(condition)
                break
            except (StaleElementReferenceException, TimeoutException):
                continue
        if send_button is None:
            raise WhatsAppSendError("Botao de envio nao encontrado no WhatsApp Web.")

        try:
            if page_hidden:
                self.driver.execute_script("arguments[0].click();", send_button)
            else:
                send_button.click()
        except StaleElementReferenceException:
            if self._wait_editor_empty(timeout=1.5):
                return
            send_button = None
            for xpath in send_xpaths:
                try:
                    condition = (
                        EC.presence_of_element_located((By.XPATH, xpath))
                        if page_hidden
                        else EC.element_to_be_clickable((By.XPATH, xpath))
                    )
                    send_button = self.wait.until(condition)
                    break
                except (StaleElementReferenceException, TimeoutException):
                    continue
            if send_button is None:
                raise WhatsAppSendError("Botao de envio foi recriado e nao pode ser recuperado.")
            if page_hidden:
                self.driver.execute_script("arguments[0].click();", send_button)
            else:
                send_button.click()

        if not self._wait_editor_empty(timeout=8):
            raise WhatsAppSendUncertainError(
                "Clique em Enviar realizado, mas o WhatsApp nao confirmou a limpeza do campo. "
                "A tentativa nao sera repetida automaticamente."
            )

    def _legacy_clear_message_box(self, input_box) -> None:
        from selenium.webdriver.common.keys import Keys

        last_text = ""
        for _attempt in range(4):
            input_box.click()
            input_box.send_keys(Keys.CONTROL, "a")
            input_box.send_keys(Keys.BACKSPACE)
            input_box.send_keys(Keys.DELETE)
            time.sleep(0.35)
            last_text = self._message_box_text(input_box)
            if not self._compact_text(last_text):
                # O editor do WhatsApp pode restaurar o rascunho de forma
                # assíncrona. Só aceitamos a limpeza se continuar vazio.
                time.sleep(0.45)
                last_text = self._message_box_text(input_box)
                if not self._compact_text(last_text):
                    return
        raise WhatsAppSendError(f"Nao consegui limpar o rascunho do WhatsApp antes do envio: {last_text[:80]}")

    def _legacy_type_message(self, input_box, message: str) -> None:
        """Digita usando eventos reais para manter o estado interno do editor sincronizado."""
        from selenium.webdriver.common.keys import Keys

        input_box.click()
        lines = message.split("\n")
        for index, line in enumerate(lines):
            if line:
                input_box.send_keys(line)
            if index < len(lines) - 1:
                # Enter sozinho envia a mensagem no WhatsApp Web.
                input_box.send_keys(Keys.SHIFT, Keys.ENTER)

    def _legacy_message_box_text(self, input_box) -> str:
        try:
            return self.driver.execute_script(
                """
                const box = arguments[0];
                return (box.innerText || box.textContent || "").trim();
                """,
                input_box,
            ) or ""
        except Exception:
            return ""

    def _find_message_box(self, *, timeout: float | None = None):
        from selenium.webdriver.common.by import By
        from selenium.webdriver.support import expected_conditions as EC
        from selenium.webdriver.support.ui import WebDriverWait
        from selenium.common.exceptions import TimeoutException

        waiter = self.wait if timeout is None else WebDriverWait(self.driver, timeout)
        xpaths = (
            '//footer//div[@contenteditable="true"][@role="textbox"]',
            '//footer//div[@contenteditable="true"]',
            '//div[@contenteditable="true"][@data-tab="10"]',
        )
        for xpath in xpaths:
            try:
                # Chrome 150 passa a reportar os elementos como "nao visiveis"
                # quando a janela/RDP esta oculta, embora o editor continue no
                # DOM, com dimensoes e apto a receber eventos do WebDriver.
                # Exigir visibility aqui fazia o servidor parar de enviar ao
                # desconectar a sessao remota.
                return waiter.until(EC.presence_of_element_located((By.XPATH, xpath)))
            except TimeoutException:
                continue
        raise WhatsAppSendError("Campo de mensagem nao encontrado no WhatsApp Web.")

    def _clear_message_box(self) -> None:
        from selenium.webdriver.common.keys import Keys
        from selenium.webdriver.common.action_chains import ActionChains
        from selenium.common.exceptions import StaleElementReferenceException

        if self.driver.execute_script("return document.hidden"):
            for _attempt in range(3):
                input_box = self._find_message_box()
                self.driver.execute_script(
                    """
                    arguments[0].focus();
                    const range = document.createRange();
                    range.selectNodeContents(arguments[0]);
                    const selection = window.getSelection();
                    selection.removeAllRanges();
                    selection.addRange(range);
                    """,
                    input_box,
                )
                # Uma string vazia e ignorada pelo CDP. Um espaco substitui o
                # rascunho inteiro e e considerado vazio pela nossa validacao.
                self.driver.execute_cdp_cmd("Input.insertText", {"text": " "})
                time.sleep(0.2)
                if not self._compact_text(self._message_box_text()):
                    return
            raise WhatsAppSendError("Nao consegui limpar o rascunho do WhatsApp com a aba oculta.")

        last_text = ""
        for _attempt in range(4):
            try:
                input_box = self._find_message_box()
                if self.driver.execute_script("return document.hidden"):
                    self.driver.execute_script("arguments[0].focus(); arguments[0].click();", input_box)
                    actions = ActionChains(self.driver)
                else:
                    actions = ActionChains(self.driver).click(input_box)
                actions.key_down(Keys.CONTROL).send_keys("a").key_up(Keys.CONTROL)
                actions.send_keys(Keys.BACKSPACE).send_keys(Keys.DELETE).perform()
            except StaleElementReferenceException:
                continue
            time.sleep(0.2)
            last_text = self._message_box_text()
            if not self._compact_text(last_text):
                time.sleep(0.25)
                last_text = self._message_box_text()
                if not self._compact_text(last_text):
                    return
        raise WhatsAppSendError(f"Nao consegui limpar o rascunho do WhatsApp antes do envio: {last_text[:80]}")

    def _type_message(self, message: str) -> None:
        """Usa uma unica sequencia de teclado e preserva as quebras de linha."""
        from selenium.webdriver.common.keys import Keys
        from selenium.webdriver.common.action_chains import ActionChains

        input_box = self._find_message_box()
        if self.driver.execute_script("return document.hidden"):
            self.driver.execute_script(
                """
                arguments[0].focus();
                const range = document.createRange();
                range.selectNodeContents(arguments[0]);
                const selection = window.getSelection();
                selection.removeAllRanges();
                selection.addRange(range);
                """,
                input_box,
            )
            self.driver.execute_cdp_cmd("Input.insertText", {"text": message})
            return
        else:
            actions = ActionChains(self.driver).click(input_box)
        lines = message.split("\n")
        for index, line in enumerate(lines):
            if line:
                actions.send_keys(line)
            if index < len(lines) - 1:
                actions.key_down(Keys.SHIFT).send_keys(Keys.ENTER).key_up(Keys.SHIFT)
        actions.perform()

    def _message_box_text(self, *, timeout: float | None = None) -> str:
        from selenium.common.exceptions import StaleElementReferenceException

        for _attempt in range(3):
            input_box = self._find_message_box(timeout=timeout)
            try:
                return self.driver.execute_script(
                    """
                    const box = arguments[0];
                    return (box.innerText || box.textContent || "").trim();
                    """,
                    input_box,
                ) or ""
            except StaleElementReferenceException:
                continue
        raise WhatsAppSendError("O editor do WhatsApp foi recriado repetidamente durante a leitura.")

    def _wait_editor_empty(self, *, timeout: float) -> bool:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            try:
                if not self._compact_text(self._message_box_text(timeout=0.8)):
                    return True
            except Exception:
                pass
            time.sleep(0.15)
        return False

    def _compact_text(self, value: str) -> str:
        return re.sub(r"\s+", " ", str(value or "")).strip()

    def _current_tab_has_phone(self, phone: str) -> bool:
        if not self.driver:
            return False
        try:
            current_url = self.driver.current_url or ""
        except Exception:
            return False
        return phone in re.sub(r"\D+", "", current_url)

    def _switch_to_whatsapp_tab(self) -> None:
        if not self.driver:
            return
        for handle in self.driver.window_handles:
            self.driver.switch_to.window(handle)
            current_url = (self.driver.current_url or "").lower()
            if "web.whatsapp.com" in current_url:
                try:
                    self.driver.execute_cdp_cmd("Page.bringToFront", {})
                except Exception:
                    pass
                return

        self.driver.switch_to.new_window("tab")
        self.driver.get("https://web.whatsapp.com")

    def _wait_login(self, timeout: int) -> bool:
        deadline = time.time() + timeout
        while time.time() < deadline:
            if self._is_logged_in():
                return True
            time.sleep(1)
        return False

    def _is_logged_in(self) -> bool:
        if not self.driver:
            return False
        checks = (
            '//div[@data-tab="1"]',
            '//div[@aria-label="Lista de conversas"]',
            '//div[@contenteditable="true"]',
        )
        for xpath in checks:
            try:
                self.driver.find_element("xpath", xpath)
                return True
            except Exception:
                continue
        try:
            self.driver.find_element("xpath", '//canvas[@aria-label="Scan me!"]')
            return False
        except Exception:
            return False

    def _is_port_open(self, port: int) -> bool:
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        try:
            return sock.connect_ex(("127.0.0.1", port)) == 0
        finally:
            sock.close()

    def _find_chrome(self) -> str | None:
        paths = [
            r"C:\Program Files\Google\Chrome\Application\chrome.exe",
            r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
            str(Path.home() / r"AppData\Local\Google\Chrome\Application\chrome.exe"),
        ]
        for path in paths:
            if os.path.exists(path):
                return path
        return None
