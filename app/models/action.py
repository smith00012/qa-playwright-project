from dataclasses import asdict, dataclass


@dataclass
class ElementInfo:
    tag: str | None = None
    text: str | None = None
    role: str | None = None
    name: str | None = None
    id: str | None = None
    placeholder: str | None = None
    aria_label: str | None = None
    title: str | None = None
    selector: str | None = None


@dataclass
class TestAction:
    sequence: int
    action_type: str
    timestamp: str
    url: str
    element: ElementInfo | None = None
    value: str | None = None
    screenshot: str | None = None
    # URL del iframe cuando la acción ocurre dentro de uno (por ejemplo,
    # los campos de tarjeta de una pasarela de pago).
    frame_url: str | None = None

    def to_dict(self) -> dict:
        return asdict(self)