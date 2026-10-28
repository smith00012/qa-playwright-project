"""
Servidor MCP: conecta una IA (Claude Code u otro cliente MCP) con la
herramienta para grabar flujos, leer corridas, ver capturas y redactar
guías de uso.

Se inicia desde la raíz del proyecto con:
    .\\.venv\\Scripts\\python.exe mcp_server.py
"""

import sys

from mcp.server.mcpserver import Image, MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp.types import ToolAnnotations

from app.config import DEFAULT_PATHS
from app.services.ai_recording import AiRecordingSession
from app.services.assistant_service import REDACTED, AssistantService


EXTRA_INSTRUCTIONS_FILE = DEFAULT_PATHS.data_dir / "instrucciones_ia.md"

INSTRUCTIONS = f"""
QA Test Recorder graba recorridos web, los reproduce revisando cada pantalla
(consola, red, accesibilidad, tiempos) y genera guías de uso paso a paso.

Reglas:
- Al reproducir con generar_guia, el freno de pago queda siempre activo: el
  flujo se detiene antes de pasos como "Continuar al pago". No se puede
  desactivar desde aquí; eso lo decide una persona en data/run_config.json.
- Los datos de prueba nunca llegan a la IA: aparecen como {REDACTED}. No
  intentes deducirlos ni pedirlos.
- Las capturas pueden mostrar datos personales. Pídelas con ver_captura solo
  cuando las necesites y no copies esos datos en los textos.
- La IA propone y la persona decide: antes de actualizar_textos_guia, muestra
  los textos propuestos y aplícalos cuando la persona los apruebe.
- Un flujo que falla porque no encuentra un elemento suele indicar que la
  página cambió y hay que regrabarlo; dilo así en vez de suponer un error del sitio.

Grabar un flujo (la IA navega y el grabador registra):
- iniciar_grabacion abre un navegador visible en el equipo de la persona.
- Usa ver_pantalla antes de cada acción: devuelve los controles con un número
  (ref). Actúa con hacer_clic, escribir o elegir_opcion usando ese número, y
  vuelve a llamar a ver_pantalla después, porque los números cambian.
- Recorre el camino que haría un usuario real. No hay teclas, atrás ni
  recargar: el flujo se reproduce paso por paso tal como se grabó.
- Para correos, documentos, contraseñas o tarjetas usa escribir con
  usar_dato_de_prueba=true. Para el resto puedes inventar datos ficticios.
- El freno de pago rechaza los clics de la IA en pasos bloqueados. Si eso
  pasa, o aparece un login, captcha o código, pide a la persona que siga en
  la ventana: lo que haga también se graba.
- Al terminar, terminar_grabacion guarda el flujo; luego puedes generar_guia.
""".strip()


def _log(message: str) -> None:
    # stdout es el canal MCP: los mensajes de la reproducción van a stderr.
    print(message, file=sys.stderr, flush=True)


def _keep_prints_off_the_wire() -> None:
    """
    El grabador usa print. Cuando llega la primera herramienta, MCP ya tomó
    su propia referencia al canal; desde ahí los print van a stderr. En
    Windows el SDK no siempre logra desviarlos solo y romperían los mensajes.
    """

    sys.stdout = sys.stderr


def _instructions() -> str:
    if EXTRA_INSTRUCTIONS_FILE.exists():
        extra = EXTRA_INSTRUCTIONS_FILE.read_text(encoding="utf-8").strip()
        if extra:
            return f"{INSTRUCTIONS}\n\nReglas de este proyecto:\n{extra}"
    return INSTRUCTIONS


def _answer(method, *args):
    """Los rechazos del servicio llegan a la IA con su motivo, para que pueda corregir el pedido."""

    try:
        return method(*args)
    except ValueError as error:
        raise ToolError(str(error)) from error


service = AssistantService(log_callback=_log)
recording = AiRecordingSession(log_callback=_log)
server = MCPServer("qa-test-recorder", instructions=_instructions())
READ_ONLY = ToolAnnotations(readOnlyHint=True, openWorldHint=False)


@server.tool(annotations=READ_ONLY)
def listar_flujos() -> list[dict]:
    """Flujos grabados, del más reciente al más antiguo. `flujo` es el id que usan las demás herramientas."""

    return _answer(service.list_flows)


@server.tool(annotations=READ_ONLY)
def listar_corridas(flujo: str | None = None, limite: int = 20) -> list[dict]:
    """Reproducciones guardadas en data/runs, de la más reciente a la más antigua. Filtra por id o nombre de flujo."""

    return _answer(service.list_runs, flujo, limite)


@server.tool(annotations=READ_ONLY)
def leer_corrida(corrida: str) -> dict:
    """Resultado de una reproducción: estado, paso fallido y error, pantallas con tiempos, errores de red y consola agrupados y reglas de accesibilidad."""

    return _answer(service.read_run, corrida)


@server.tool(annotations=READ_ONLY)
def leer_guia(corrida: str) -> dict:
    """
    Guía de uso de una corrida: pantallas, capturas y pasos con su tipo de
    control, etiqueta visible y texto actual. `paso_flujo` es la clave para
    cambiar el texto de un paso u ocultarlo.
    """

    return _answer(service.read_guide, corrida)


@server.tool(annotations=READ_ONLY)
def ver_captura(corrida: str, archivo: str) -> Image:
    """Muestra una captura de la corrida (por ejemplo guia_001_pasos.png, con los pasos numerados). Puede contener datos personales."""

    return Image(path=_answer(service.capture_path, corrida, archivo))


@server.tool(annotations=ToolAnnotations(destructiveHint=False, idempotentHint=True, openWorldHint=False))
def actualizar_textos_guia(corrida: str, cambios: dict) -> dict:
    """
    Cambia los textos de la guía del flujo de esa corrida y la regenera
    (HTML y Word). Solo se envía lo que cambia:
    {"titulo": str, "introduccion": str,
     "pantallas": {"1": {"titulo": str, "nota": str}},
     "pasos": {"<paso_flujo>": "Texto con **resaltado**"},
     "ocultar": [<paso_flujo>, ...],
     "final": {"titulo": str, "nota": str}}.
    "ocultar" reemplaza la lista completa. La versión anterior queda como respaldo.
    """

    return _answer(service.update_guide_texts, corrida, cambios)


@server.tool(annotations=ToolAnnotations(destructiveHint=False, openWorldHint=True))
def generar_guia(
    flujo: str,
    ambiente: str | None = None,
    navegador: str = "chromium",
    dispositivo: str = "Escritorio",
) -> dict:
    """
    Reproduce el flujo en segundo plano y genera su guía de uso. Tarda varios
    minutos. El freno de pago queda activo aunque el ambiente lo desactive.
    Navegadores: chromium, firefox, webkit. Dispositivos: Escritorio,
    iPhone 13, Pixel 7, iPad (gen 7).
    """

    return _answer(service.generate_guide, flujo, ambiente, navegador, dispositivo)


@server.tool(annotations=ToolAnnotations(destructiveHint=False, openWorldHint=True))
def iniciar_grabacion(
    url: str,
    nombre: str,
    ambiente: str | None = None,
    dispositivo: str = "Escritorio",
) -> dict:
    """
    Abre un navegador visible en la URL y empieza a grabar un flujo nuevo.
    El ambiente elige los datos de prueba; el freno de pago queda activo
    igual. Abrir el navegador puede tardar hasta un minuto.
    Dispositivos: Escritorio, iPhone 13, Pixel 7, iPad (gen 7). Un teléfono se
    emula completo (user-agent móvil y táctil); reproduce luego con el mismo
    dispositivo en generar_guia, porque el sitio cambia de diseño.
    """

    _keep_prints_off_the_wire()
    return service.redact(_answer(recording.start, url, nombre, ambiente, dispositivo))


@server.tool(annotations=READ_ONLY)
def ver_pantalla() -> dict:
    """
    Pantalla actual de la grabación: URL, título, encabezados, texto visible y
    los controles con su número (ref), tipo, etiqueta y si están a la vista.
    """

    return service.redact(_answer(recording.screen))


@server.tool(annotations=READ_ONLY)
def captura_grabacion() -> Image:
    """Captura de lo que muestra ahora el navegador de la grabación. Úsala solo si ver_pantalla no alcanza."""

    return Image(data=_answer(recording.screenshot), format="png")


@server.tool(annotations=ToolAnnotations(destructiveHint=False, openWorldHint=True))
def hacer_clic(ref: str) -> dict:
    """Pulsa el control con ese número de ver_pantalla. Se graba como un clic."""

    return service.redact(_answer(recording.click, ref))


@server.tool(annotations=ToolAnnotations(destructiveHint=False, openWorldHint=True))
def escribir(ref: str, texto: str | None = None, usar_dato_de_prueba: bool = False) -> dict:
    """
    Escribe en el campo con ese número. Envía `texto` o
    usar_dato_de_prueba=true para que se escriba el valor de
    data/test_data.json sin mostrarlo.
    """

    return service.redact(_answer(recording.type_text, ref, texto, usar_dato_de_prueba))


@server.tool(annotations=ToolAnnotations(destructiveHint=False, openWorldHint=True))
def elegir_opcion(ref: str, opcion: str) -> dict:
    """Elige una opción de una lista (por su texto, como aparece en `opciones` de ver_pantalla)."""

    return service.redact(_answer(recording.select_option, ref, opcion))


@server.tool(annotations=ToolAnnotations(destructiveHint=False, openWorldHint=False))
def terminar_grabacion(guardar: bool = True) -> dict:
    """Cierra el navegador y guarda el flujo grabado (o lo descarta con guardar=false)."""

    return service.redact(_answer(recording.finish, guardar))


@server.prompt()
def grabar_flujo(objetivo: str, url: str) -> str:
    """Grabar un flujo nuevo navegando el sitio como lo haría un usuario."""

    return f"""
Graba un flujo que cumpla este objetivo: {objetivo}
Empieza en {url}.

1. Propón un nombre corto para el flujo e inicia la grabación con iniciar_grabacion.
2. Avanza como un usuario real: ver_pantalla, una acción, ver_pantalla otra vez.
   Antes de cada acción, dime en una línea qué vas a hacer.
3. Usa datos de prueba para correos, documentos, contraseñas y tarjetas, y datos
   ficticios para lo demás.
4. Si el freno de pago te detiene o aparece algo que no puedes resolver (login,
   captcha, código), avísame y espera a que lo haga yo en la ventana.
5. Cuando cumplas el objetivo, termina la grabación, muéstrame los pasos
   guardados y pregúntame si genero la guía.
""".strip()


@server.prompt()
def mejorar_guia(corrida: str) -> str:
    """Redactar una guía de uso clara a partir de una corrida con guía."""

    return f"""
Mejora la guía de uso de la corrida {corrida} para usuarios finales.

1. Lee la guía con leer_guia y mira cada captura marcada con ver_captura.
2. Para cada pantalla, propone un título que diga la tarea ("Inicia sesión",
   no el título técnico de la página) y una nota corta que explique para qué
   sirve o qué hay que tener a mano.
3. Reescribe cada paso: tuteo, verbo al inicio, el control con el nombre que
   se ve en pantalla entre **, y el propósito solo cuando ayuda. Describe solo
   lo que se ve en la captura; no inventes comportamientos.
4. Propone ocultar los pasos que no aportan (cerrar banners, clics repetidos).
5. Escribe una introducción: qué se logra y qué se necesita antes de empezar.
6. No incluyas datos personales, correos ni valores escritos.
7. Muéstrame el borrador completo por pantalla. Cuando lo apruebe, aplícalo con
   actualizar_textos_guia y dime dónde quedó la guía.
""".strip()


@server.prompt()
def resumir_corrida(corrida: str) -> str:
    """Resumen de una reproducción para el equipo: ejecutivo y técnico."""

    return f"""
Lee la corrida {corrida} con leer_corrida y escribe dos resúmenes:

- Ejecutivo (para quien no es técnico): qué se probó, si llegó al final y
  qué hay que atender primero, en 4 o 5 líneas.
- Técnico (para quien corrige): paso fallido y causa probable cruzando el
  error con la consola y la red, hallazgos que son ruido de terceros y los
  problemas de accesibilidad más graves con su corrección.

Mira una captura solo si hace falta para entender una falla.
""".strip()


def main() -> None:
    server.run()


if __name__ == "__main__":
    main()
