import threading
import queue
from typing import Callable, Optional

from datetime import datetime
from pathlib import Path

from playwright.sync_api import Error as PlaywrightError
from playwright.sync_api import sync_playwright

from app.browser_scripts import DISABLE_PRINT_SCRIPT
from app.models.action import TestAction, ElementInfo
from app.evidence.screenshot_manager import ScreenshotManager
from app.recorder.sensitive_data import sanitize_event
from app.storage.json_repository import JsonRepository


START_TIMEOUT_SECONDS = 180
PAGE_LOAD_TIMEOUT_MS = 60000


def _first_line(error: Exception) -> str:
    return (str(error).strip().splitlines() or [""])[0]


class BrowserRecorder:
    """
    Responsable de:
    - Lanzar el navegador.
    - Registrar eventos del navegador.
    - Procesar los eventos recibidos.
    - Capturar evidencias.
    - Guardar actions.json.
    - Cerrar correctamente Playwright.

    Playwright se ejecuta en un hilo independiente para evitar
    conflictos con Tkinter.
    """

    def __init__(
        self,
        execution_folder: Path,
        log_callback: Optional[Callable[[str], None]] = None
    ):

        self.execution_folder = execution_folder
        self.log_callback = log_callback

        self.screenshot_manager = ScreenshotManager(
            execution_folder
        )

        self.repository = JsonRepository()

        self.actions = []

        # Cola donde JavaScript deposita los eventos.
        self.event_queue = queue.Queue()

        # Control del hilo de Playwright.
        self.stop_event = threading.Event()
        self.ready_event = threading.Event()

        self.worker_thread = None

        # Referencias de Playwright.
        self.playwright = None
        self.browser = None
        self.page = None

        self.error = None
        self.closed_unexpectedly = False
        self.execution_saved = False
        self.abandoned = False

    def _emit_log(self, message: str) -> None:
        if self.log_callback:
            self.log_callback(message)

    @staticmethod
    def should_capture_screenshot(event_type: str | None) -> bool:
        """Evita capturas repetidas en cada escritura para evitar el parpadeo visual."""
        return (event_type or "").lower() != "input"

    # ==========================================================
    # INICIO
    # ==========================================================

    def start(self, url: str):
        """
        Inicia el navegador en un hilo independiente.
        """

        print("")
        print("====================================")
        print("RECORDER - INICIANDO")
        print("====================================")

        self.stop_event.clear()
        self.ready_event.clear()
        self.error = None
        self.closed_unexpectedly = False
        self.execution_saved = False
        self.abandoned = False
        self.actions.clear()

        self.worker_thread = threading.Thread(
            target=self._browser_worker,
            args=(url,),
            daemon=True
        )

        self.worker_thread.start()

        # Esperamos a que el navegador abra. La carga de la página no
        # cuenta: un sitio lento no debe impedir iniciar la grabación.
        if not self.ready_event.wait(timeout=START_TIMEOUT_SECONDS):
            self.abandoned = True
            self.stop_event.set()
            raise RuntimeError(
                "No fue posible iniciar el navegador en "
                f"{START_TIMEOUT_SECONDS} segundos."
            )

        if self.error:
            raise self.error

        print("[RECORDER] Navegador listo.")

    # ==========================================================
    # HILO DE PLAYWRIGHT
    # ==========================================================

    def _browser_worker(self, url: str):
        """
        Todo lo relacionado con Playwright ocurre dentro de
        este hilo.
        """

        try:

            print("[RECORDER] Iniciando Playwright...")

            self.playwright = sync_playwright().start()

            print("[RECORDER] Lanzando Chromium...")

            self.browser = self.playwright.chromium.launch(
                headless=False
            )

            self.page = self.browser.new_page(
                viewport={"width": 1440, "height": 980}
            )

            # --------------------------------------------------
            # Binding JavaScript -> Python
            # --------------------------------------------------

            self.page.expose_binding(
                "qaRecord",
                self._receive_event
            )

            # --------------------------------------------------
            # Instalamos los listeners JavaScript
            # --------------------------------------------------

            # El diálogo de impresión bloquea la página y la grabación.
            self.page.add_init_script(DISABLE_PRINT_SCRIPT)

            self.page.add_init_script(
                """
                (() => {

                    // ==================================================
                    // Busca el elemento funcional asociado al evento.
                    // Ejemplo:
                    //
                    // <a>
                    //     <span>
                    //         <b>Texto</b>
                    //     </span>
                    // </a>
                    //
                    // Si se hace click sobre <b>, buscamos el <a>.
                    // ==================================================

                    function getInteractiveElement(element) {

                        if (!element) {
                            return null;
                        }

                        const selector = [
                            "button",
                            "a",
                            "input",
                            "textarea",
                            "select",
                            "option",
                            "label",
                            "[role='button']",
                            "[role='link']",
                            "[role='checkbox']",
                            "[role='radio']",
                            "[role='combobox']",
                            "[role='option']",
                            "[tabindex]"
                        ].join(",");

                        return element.closest(selector) || element;
                    }


                    // ==================================================
                    // Obtiene un selector CSS básico.
                    // ==================================================

                    function getCssSelector(element) {

                        if (!element) {
                            return null;
                        }

                        // ID
                        if (element.id) {

                            if (window.CSS && CSS.escape) {
                                return "#" + CSS.escape(element.id);
                            }

                            return "#" + element.id;
                        }

                        // NAME
                        const name = element.getAttribute("name");

                        if (name) {

                            const escapedName =
                                window.CSS && CSS.escape
                                    ? CSS.escape(name)
                                    : name;

                            return (
                                element.tagName.toLowerCase() +
                                '[name="' +
                                escapedName +
                                '"]'
                            );
                        }

                        // Construcción básica por jerarquía.
                        const path = [];

                        let current = element;
                        let level = 0;

                        while (
                            current &&
                            current.nodeType === 1 &&
                            level < 5
                        ) {

                            let selectorPart =
                                current.tagName.toLowerCase();

                            // Clases
                            if (current.classList.length > 0) {

                                const classes = Array.from(
                                    current.classList
                                )
                                    .slice(0, 2)
                                    .filter(Boolean);

                                for (const className of classes) {

                                    const escapedClass =
                                        window.CSS && CSS.escape
                                            ? CSS.escape(className)
                                            : className;

                                    selectorPart +=
                                        "." + escapedClass;
                                }
                            }

                            // nth-of-type
                            let index = 1;
                            let sibling = current;

                            while (
                                sibling.previousElementSibling
                            ) {

                                sibling =
                                    sibling.previousElementSibling;

                                if (
                                    sibling.tagName ===
                                    current.tagName
                                ) {
                                    index++;
                                }
                            }

                            selectorPart +=
                                `:nth-of-type(${index})`;

                            path.unshift(selectorPart);

                            current = current.parentElement;
                            level++;
                        }

                        return path.join(" > ");
                    }


                    // ==================================================
                    // Texto visible.
                    // ==================================================

                    function getVisibleText(element) {

                        if (!element) {
                            return "";
                        }

                        const text =
                            element.innerText ||
                            element.textContent ||
                            "";

                        return text
                            .replace(/\\s+/g, " ")
                            .trim()
                            .substring(0, 300);
                    }


                    // ==================================================
                    // Información completa del elemento.
                    // ==================================================

                    function getElementInfo(element) {

                        if (!element) {
                            return null;
                        }

                        const interactiveElement =
                            getInteractiveElement(element);

                        if (!interactiveElement) {
                            return null;
                        }

                        const tag =
                            interactiveElement.tagName
                                ? interactiveElement.tagName.toUpperCase()
                                : null;

                        const text =
                            getVisibleText(interactiveElement);

                        const role =
                            interactiveElement.getAttribute("role");

                        const name =
                            interactiveElement.getAttribute("name");

                        const id =
                            interactiveElement.id || null;

                        const placeholder =
                            interactiveElement.getAttribute(
                                "placeholder"
                            );

                        const ariaLabel =
                            interactiveElement.getAttribute(
                                "aria-label"
                            );

                        const title =
                            interactiveElement.getAttribute(
                                "title"
                            );

                        const inputType =
                            interactiveElement.getAttribute("type");

                        const autocomplete =
                            interactiveElement.getAttribute("autocomplete");

                        const dataSensitive =
                            interactiveElement.getAttribute("data-sensitive");

                        const selector =
                            getCssSelector(interactiveElement);

                        return {
                            tag: tag,
                            text: text,
                            role: role,
                            name: name,
                            id: id,
                            placeholder: placeholder,
                            aria_label: ariaLabel,
                            title: title,
                            type: inputType,
                            autocomplete: autocomplete,
                            data_sensitive: dataSensitive,
                            selector: selector
                        };
                    }


                    function isSensitiveElement(element) {

                        if (!element) {
                            return false;
                        }

                        const attributes = [
                            element.type,
                            element.name,
                            element.id,
                            element.placeholder,
                            element.getAttribute("aria-label"),
                            element.title,
                            element.autocomplete,
                            element.getAttribute("data-sensitive")
                        ].filter(Boolean).join(" ").toLowerCase();

                        return (
                            element.type === "password" ||
                            /password|passwd|secret|token|authorization|api[_-]?key|credit.?card|card.?number|cvv|cvc|one.?time.?code|otp|security.?code/i.test(attributes) ||
                            /^(current-password|new-password|one-time-code|cc-number|cc-csc|cc-exp)$/i.test(element.autocomplete || "")
                        );
                    }


                    // ==================================================
                    // Enviar evento a Python.
                    // ==================================================

                    function sendEvent(
                        type,
                        element,
                        value = null
                    ) {

                        try {

                            if (
                                typeof window.qaRecord ===
                                "function"
                            ) {

                                window.qaRecord({
                                    type: type,
                                    element:
                                        getElementInfo(element),
                                    value: value,
                                    // URL al momento del evento: al
                                    // procesarlo, un clic ya pudo navegar.
                                    url: window.location.href
                                });
                            }

                        } catch (error) {

                            console.error(
                                "QA Recorder:",
                                error
                            );
                        }
                    }


                    // ==================================================
                    // CLICK
                    // ==================================================

                    document.addEventListener(
                        "click",
                        function(event) {

                            sendEvent(
                                "click",
                                event.target
                            );

                        },
                        true
                    );


                    // ==================================================
                    // INPUT
                    // ==================================================

                    document.addEventListener(
                        "input",
                        function(event) {

                            const element =
                                event.target;

                            let value = null;

                            if (
                                element &&
                                typeof element.value !==
                                    "undefined"
                            ) {

                                if (isSensitiveElement(element)) {

                                    value = "[MASKED]";

                                } else {

                                    value =
                                        element.value;
                                }
                            }

                            sendEvent(
                                "input",
                                element,
                                value
                            );

                        },
                        true
                    );


                    // ==================================================
                    // CHANGE
                    // ==================================================

                    document.addEventListener(
                        "change",
                        function(event) {

                            const element =
                                event.target;

                            let value = null;

                            if (
                                element &&
                                typeof element.value !==
                                    "undefined"
                            ) {

                                if (isSensitiveElement(element)) {

                                    value = "[MASKED]";

                                } else {

                                    value =
                                        element.value;
                                }
                            }

                            sendEvent(
                                "change",
                                element,
                                value
                            );

                        },
                        true
                    );

                })();
                """
            )

            self.ready_event.set()

            print("[RECORDER] Abriendo:", url)

            try:
                self.page.goto(
                    url,
                    wait_until="domcontentloaded",
                    timeout=PAGE_LOAD_TIMEOUT_MS
                )
                print(
                    "[RECORDER] Página lista:",
                    self.page.url
                )
            except PlaywrightError as error:
                if self.page.is_closed():
                    raise
                self._emit_log(
                    "La página tardó en abrir o no respondió "
                    f"({_first_line(error)}). El navegador sigue abierto: "
                    "recargue o navegue y continúe la grabación."
                )

            # --------------------------------------------------
            # Bucle principal del recorder
            # --------------------------------------------------

            while not self.stop_event.is_set():

                # wait_for_timeout permite que Playwright
                # procese eventos pendientes.

                self.page.wait_for_timeout(100)

                self._process_pending_events()

            # --------------------------------------------------
            # Procesamiento final
            # --------------------------------------------------

            print(
                "[RECORDER] Procesando eventos pendientes..."
            )

            for _ in range(5):

                self.page.wait_for_timeout(100)

                self._process_pending_events()

            print(
                "[RECORDER] Eventos procesados:",
                self.event_queue.qsize()
            )

            self._process_pending_events()

            # --------------------------------------------------
            # Guardar ejecución
            # --------------------------------------------------

            self._save_execution()

        except Exception as error:

            self.error = error
            self.closed_unexpectedly = not self.stop_event.is_set()

            if self.abandoned:
                self.ready_event.set()
                return

            print("")
            print("====================================")
            print("ERROR EN RECORDER")
            print("====================================")
            print(error)

            if self.closed_unexpectedly:
                if "has been closed" in str(error):
                    self._emit_log(
                        "Se cerró la ventana del navegador antes de finalizar "
                        "la prueba. Pulse FINALIZAR PRUEBA para generar el "
                        "flujo y el Word con las acciones registradas."
                    )
                else:
                    self._emit_log(
                        "La grabación se detuvo por un error: "
                        f"{_first_line(error)}. Pulse FINALIZAR PRUEBA para "
                        "generar el flujo y el Word con las acciones registradas."
                    )
                self._save_execution()

            self.ready_event.set()

        finally:

            self._close_playwright()

    # ==========================================================
    # RECIBIR EVENTO DESDE JAVASCRIPT
    # ==========================================================

    def _receive_event(self, source, event):
        """
        Esta función debe ser extremadamente rápida.

        NO tomamos screenshots aquí.
        NO guardamos archivos aquí.

        Solo colocamos el evento en la cola.
        """

        frame = source.get("frame") if isinstance(source, dict) else None
        if (
            frame is not None
            and self.page is not None
            and frame != self.page.main_frame
        ):
            event = {**event, "frame_url": frame.url}

        self.event_queue.put(sanitize_event(event))
        self._emit_log(
            f"Evento recibido: {event.get('type', 'unknown')}"
        )

    # ==========================================================
    # PROCESAR EVENTOS
    # ==========================================================

    def _process_pending_events(self):

        while True:

            try:

                event = self.event_queue.get_nowait()

            except queue.Empty:

                break

            self._process_event(event)

    # ==========================================================
    # PROCESAR UN EVENTO
    # ==========================================================

    def _process_event(self, event):

        event = sanitize_event(event)

        sequence = len(self.actions) + 1

        print("")
        print("====================================")
        print("EVENTO RECIBIDO")
        print("====================================")
        print(event)
        self._emit_log(
            f"Procesando evento {event.get('type', 'unknown')}"
        )

        element_data = event.get(
            "element"
        )

        element = None

        if element_data:

            element = ElementInfo(

                tag=element_data.get("tag"),

                text=element_data.get("text"),

                role=element_data.get("role"),

                name=element_data.get("name"),

                id=element_data.get("id"),

                placeholder=element_data.get(
                    "placeholder"
                ),

                aria_label=element_data.get(
                    "aria_label"
                ),

                title=element_data.get(
                    "title"
                ),

                selector=element_data.get(
                    "selector"
                )
            )

        # ------------------------------------------------------
        # Crear acción
        # ------------------------------------------------------

        action = TestAction(

            sequence=sequence,

            action_type=event.get(
                "type",
                "unknown"
            ),

            timestamp=datetime.now().isoformat(
                timespec="seconds"
            ),

            url=(
                self.page.url
                if event.get("frame_url") and self.page
                else event.get("url") or (self.page.url if self.page else "")
            ),

            frame_url=event.get("frame_url"),

            element=element,

            value=event.get("value"),

            screenshot=None
        )

        print(
            "[RECORDER] Acción registrada:",
            sequence
        )

        # ------------------------------------------------------
        # Screenshot
        # ------------------------------------------------------

        event_type = event.get("type", "unknown")

        if not self.should_capture_screenshot(event_type):
            self._emit_log(
                "Se omite la captura de pantalla para entradas de texto para evitar parpadeo visual."
            )

        try:
            if not self.should_capture_screenshot(event_type):
                raise RuntimeError("skip_capture")

            print(
                "[RECORDER] Tomando screenshot..."
            )

            screenshot = self.screenshot_manager.capture(
                self.page,
                sequence
            )

            action.screenshot = screenshot

            print(
                "[RECORDER] Screenshot:",
                screenshot
            )
            self._emit_log(f"Screenshot guardado: {screenshot}")

        except RuntimeError as skip_error:
            if str(skip_error) != "skip_capture":
                raise

        except Exception as error:

            print(
                "[RECORDER] Error tomando screenshot:",
                error
            )

        # ------------------------------------------------------
        # Guardar acción en memoria
        # ------------------------------------------------------

        self.actions.append(action)

        print(
            "[RECORDER] Total acciones:",
            len(self.actions)
        )
        self._emit_log(
            f"Acción registrada. Total: {len(self.actions)}"
        )

    # ==========================================================
    # GUARDAR EJECUCIÓN
    # ==========================================================

    def _save_execution(self):

        print("")
        print("====================================")
        print("RECORDER - GUARDANDO EJECUCIÓN")
        print("====================================")

        print(
            "[RECORDER] Acciones a guardar:",
            len(self.actions)
        )

        actions_data = [
            action.to_dict()
            for action in self.actions
        ]

        filepath = (
            self.execution_folder
            / "actions.json"
        )

        print(
            "[RECORDER] Guardando:",
            filepath
        )

        self.repository.save(
            filepath,
            actions_data
        )
        self.execution_saved = True

        print(
            "[RECORDER] actions.json guardado."
        )
        self._emit_log(
            f"actions.json guardado: {filepath}"
        )

    # ==========================================================
    # DETENER
    # ==========================================================

    def stop(self):

        print("")
        print("====================================")
        print("RECORDER - FINALIZANDO")
        print("====================================")

        if not self.worker_thread:

            print(
                "[RECORDER] No existe hilo activo."
            )

            return

        print(
            "[RECORDER] Solicitando finalización..."
        )

        self.stop_event.set()

        print(
            "[RECORDER] Esperando finalización del navegador..."
        )

        self.worker_thread.join()

        self.worker_thread = None

        print(
            "[RECORDER] Finalizado correctamente."
        )

    # ==========================================================
    # CERRAR PLAYWRIGHT
    # ==========================================================

    def _close_playwright(self):

        print("")
        print("====================================")
        print("RECORDER - CERRANDO PLAYWRIGHT")
        print("====================================")

        try:

            if self.browser:

                print(
                    "[RECORDER] Cerrando Chromium..."
                )

                self.browser.close()

        except Exception as error:

            print(
                "[RECORDER] Error cerrando Chromium:",
                error
            )

        finally:

            self.browser = None
            self.page = None

        try:

            if self.playwright:

                print(
                    "[RECORDER] Cerrando Playwright..."
                )

                self.playwright.stop()

        except Exception as error:

            print(
                "[RECORDER] Error cerrando Playwright:",
                error
            )

        finally:

            self.playwright = None

        print(
            "[RECORDER] Playwright cerrado."
        )