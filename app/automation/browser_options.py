from dataclasses import dataclass


BROWSERS = ("chromium", "firefox", "webkit")
DESKTOP = "Escritorio"
DESKTOP_VIEWPORT = {"width": 1440, "height": 980}
DEVICE_PRESETS = (DESKTOP, "iPhone 13", "Pixel 7", "iPad (gen 7)")


@dataclass(frozen=True)
class BrowserOptions:
    """Navegador y dispositivo con los que se reproduce un flujo."""

    browser: str = "chromium"
    device: str = DESKTOP
    headless: bool = False

    def __post_init__(self) -> None:
        if self.browser not in BROWSERS:
            raise ValueError(
                f"Navegador no soportado: {self.browser}. Use uno de: {', '.join(BROWSERS)}."
            )

    @property
    def label(self) -> str:
        mode = "segundo plano" if self.headless else "visible"
        return f"{self.browser} · {self.device} · {mode}"


def context_options(options: BrowserOptions, devices: dict) -> dict:
    """
    Opciones de new_context() para el dispositivo elegido.

    Escritorio usa el mismo tamaño de ventana que el grabador. Los
    dispositivos salen del catálogo de Playwright; Firefox no admite
    is_mobile, así que se quita y se mantiene el resto de la emulación.
    """

    if options.device == DESKTOP:
        return {"viewport": dict(DESKTOP_VIEWPORT)}

    if options.device not in devices:
        raise ValueError(f"Dispositivo no reconocido por Playwright: {options.device}.")

    descriptor = dict(devices[options.device])
    descriptor.pop("default_browser_type", None)
    if options.browser == "firefox":
        descriptor.pop("is_mobile", None)
    return descriptor
