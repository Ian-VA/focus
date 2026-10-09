import base64
import io
from dataclasses import dataclass, field
from typing import Any, Callable

from PIL import Image

MAX_IMAGE_PIXELS = 1_000_000
MAX_IMAGE_EDGE = 1568
JPEG_QUALITY = 75


@dataclass
class Screenshot:
    data: bytes
    media_type: str
    width: int
    height: int
    label: str = ""

    @classmethod
    def from_image(cls, image: Image.Image, label: str = "") -> "Screenshot":
        scale = min(1.0, (MAX_IMAGE_PIXELS / (image.width * image.height)) ** 0.5)
        width = min(MAX_IMAGE_EDGE, int(image.width * scale))
        height = min(MAX_IMAGE_EDGE, int(image.height * scale))
        image.thumbnail((width, height))
        if image.mode != "RGB":
            image = image.convert("RGB")
        buffer = io.BytesIO()
        image.save(buffer, format="JPEG", quality=JPEG_QUALITY)
        return cls(buffer.getvalue(), "image/jpeg", image.width, image.height, label)

    @property
    def b64(self) -> str:
        return base64.standard_b64encode(self.data).decode("ascii")

    @property
    def data_url(self) -> str:
        return f"data:{self.media_type};base64,{self.b64}"


@dataclass
class ToolResult:
    text: str
    images: list[Screenshot] = field(default_factory=list)
    is_error: bool = False


@dataclass
class Tool:
    name: str
    description: str
    parameters: dict[str, Any]
    handler: Callable[..., ToolResult]


def tool(description: str, parameters: dict[str, Any] | None = None):
    """turn a handler function into a model-facing Tool named after the function"""

    def wrap(handler: Callable[..., ToolResult]) -> Tool:
        return Tool(handler.__name__, description, parameters or schema(), handler)

    return wrap


def schema(required: tuple[str, ...] = (), **properties: dict[str, Any]) -> dict[str, Any]:
    """JSON schema for a tool's arguments"""
    return {
        "type": "object",
        "properties": properties,
        "required": list(required),
        "additionalProperties": False,
    }
