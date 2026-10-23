"""
Servidor MCP: conecta una IA (Claude Code u otro cliente MCP) con la
herramienta para leer corridas, ver capturas y redactar guías de uso.

Se inicia desde la raíz del proyecto con:
    .\\.venv\\Scripts\\python.exe mcp_server.py
"""

import sys

from mcp.server.mcpserver import Image, MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp.types import ToolAnnotations

from app.config import DEFAULT_PATHS
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
""".strip()


def _log(message: str) -> None:
    # stdout es el canal MCP: los mensajes de la reproducción van a stderr.
    print(message, file=sys.stderr, flush=True)


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
