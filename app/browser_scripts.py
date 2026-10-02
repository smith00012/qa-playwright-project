"""Scripts que se inyectan en las páginas durante la grabación y la reproducción."""

# window.print() abre un diálogo que bloquea la página: la grabación deja de
# recibir eventos y las capturas esperan hasta que alguien lo cierre.
DISABLE_PRINT_SCRIPT = """
window.print = function () {
    console.info("QA: se omitió el diálogo de impresión.");
};
"""
