import asyncio
import base64
import re
from abc import ABC, abstractmethod
from io import BytesIO
from pathlib import Path

import httpx
from components.prompts import IMAGE_DESCRIBER
from components.utils import get_vlm_semaphore, load_config
from langchain_core.messages import HumanMessage
from langchain_openai import ChatOpenAI
from openai import BadRequestError
from PIL import Image
from tqdm.asyncio import tqdm
from utils.external_resource_errors import is_external_resource_error
from utils.logger import get_logger
from utils.ssrf import guard_request, is_blocked_url_literal

logger = get_logger()
config = load_config()


def ensure_png_compatible_mode(image: Image.Image) -> Image.Image:
    """Convert incompatible PIL image modes to PNG-saveable modes."""
    if image.mode in ("CMYK", "YCbCr", "LAB"):
        return image.convert("RGB")
    if image.mode in ("P", "LA", "PA"):
        return image.convert("RGBA")
    return image


class BaseLoader(ABC):
    # Class-level compiled regex patterns (shared across all instances)
    HTTP_IMAGE_PATTERN = re.compile(r"!\[(.*?)\]\((https?://[^)]+)\)")
    DATA_URI_IMAGE_PATTERN = re.compile(r"!\[(.*?)\]\((data:image/[^;]+;base64,[^)]+)\)")
    MIN_IMAGE_PIXELS = 784  # Qwen2.5-VL min_pixels threshold
    # Cap remote image fetches to bound memory use.
    MAX_REMOTE_IMAGE_BYTES = 20 * 1024 * 1024
    REMOTE_IMAGE_TIMEOUT = 10.0

    def __init__(self, **kwargs) -> None:
        self.page_sep = "[PAGE_SEP]"
        self.config = kwargs.get("config")
        settings: dict = self.config.vlm.model_dump()
        model_settings = {
            "temperature": 0.2,
            "max_retries": 3,
            "timeout": 60,
            # "extra_body": {"chat_template_kwargs": {"enable_thinking": False}},
        }
        settings.update(model_settings)

        self.image_captioning = self.config.loader.image_captioning
        self.image_captioning_url = self.config.loader.image_captioning_url

        self.vlm_endpoint = ChatOpenAI(**settings).with_retry(stop_after_attempt=2)

    @abstractmethod
    async def aload_document(
        self,
        file_path: str | Path,
        metadata: dict | None = None,
        save_markdown: bool = False,
    ):
        pass

    def save_content(self, text_content: str, path: str):
        path = re.sub(r"\..*", ".md", path)
        with open(path, "w", encoding="utf-8") as f:
            f.write(text_content)
        logger.debug(f"Document saved to {path}")

    def _pil_image_to_base64(self, image: Image.Image) -> str:
        """Convert PIL Image to base64 string."""
        buffered = BytesIO()
        try:
            image = ensure_png_compatible_mode(image)
            image.save(buffered, format="PNG")
        except Exception as e:
            logger.warning("Failed to convert image to PNG", error=str(e), mode=getattr(image, "mode", "unknown"))
            return ""
        return base64.b64encode(buffered.getvalue()).decode()

    def _is_http_url(self, data: str) -> bool:
        """Check if string is an HTTP/HTTPS URL."""
        return isinstance(data, str) and data.startswith(("http://", "https://"))

    def _is_data_uri(self, data: str) -> bool:
        """Check if string is a data URI."""
        return isinstance(data, str) and data.startswith("data:image/")

    async def _fetch_remote_image_as_data_uri(self, url: str) -> str | None:
        """Fetch a remote image URL with SSRF protection, return it as a data URI.

        Returns None if the URL is unsafe, unreachable or not an image. We fetch
        the image ourselves (rejecting non-global hosts and redirects) and pass
        only the bytes to the VLM, so a poisoned document can't make the VLM hit
        an internal URL.
        """
        if is_blocked_url_literal(url):
            logger.warning("Blocked non-global image URL for captioning", url=url)
            return None
        try:
            timeout = httpx.Timeout(self.REMOTE_IMAGE_TIMEOUT)
            async with httpx.AsyncClient(
                timeout=timeout,
                follow_redirects=False,
                event_hooks={"request": [guard_request]},
            ) as client:
                # Stream so we stop once the size cap is hit.
                async with client.stream("GET", url) as resp:
                    resp.raise_for_status()
                    content_type = resp.headers.get("content-type", "")
                    if not content_type.startswith("image/"):
                        logger.warning("Remote image URL is not an image", url=url, content_type=content_type)
                        return None
                    buf = bytearray()
                    async for chunk in resp.aiter_bytes():
                        buf.extend(chunk)
                        if len(buf) > self.MAX_REMOTE_IMAGE_BYTES:
                            logger.warning("Remote image exceeds size cap", url=url, size=len(buf))
                            return None
                    data = bytes(buf)
        except Exception as e:
            logger.warning("Failed to fetch remote image for captioning", url=url, error=str(e)[:200])
            return None

        mime = content_type.split(";", 1)[0].strip()
        return f"data:{mime};base64,{base64.b64encode(data).decode()}"

    async def get_image_description(
        self,
        image_data: Image.Image | str,
    ) -> str:
        """
        Creates a description for an image using the LLM model.

        Args:
            image_data: Can be one of:
                - PIL.Image object
                - str: HTTP/HTTPS URL
                - str: data URI (data:image/...;base64,...)

        Returns:
            str: Description of the image wrapped in XML tags
        """
        # Early exit for small PIL images (below VLM min_pixels threshold)
        if isinstance(image_data, Image.Image):
            w, h = image_data.size
            if w * h < self.MIN_IMAGE_PIXELS:
                logger.debug("Skipping image below minimum size", size=f"{w}x{h}")
                return "<image_description>\n\nImage too small for captioning\n\n</image_description>"

        async with get_vlm_semaphore():
            try:
                # Determine the type of image data and create appropriate message content
                if isinstance(image_data, Image.Image):
                    # Convert PIL Image to base64
                    img_b64 = self._pil_image_to_base64(image_data)
                    if not img_b64:
                        return "<image_description>\n\nFailed to convert image\n\n</image_description>"
                    image_url = f"data:image/png;base64,{img_b64}"

                elif self._is_http_url(image_data):
                    # Fetch the image ourselves (SSRF-guarded) and send a data URI.
                    image_url = await self._fetch_remote_image_as_data_uri(image_data)
                    if not image_url:
                        return "<image_description>\n\nImage URL unavailable or blocked\n\n</image_description>"
                    logger.debug(f"Fetched HTTP image URL in-process: {image_data}")

                elif self._is_data_uri(image_data):
                    # Handle data URI - use as-is
                    image_url = image_data
                    logger.debug(f"Processing data URI: {image_data[:50]}...")

                else:
                    # Handle raw base64 string (assume it's base64 encoded image)
                    if isinstance(image_data, str):
                        try:
                            # Try to decode to verify it's valid base64
                            base64.b64decode(image_data)
                            image_url = f"data:image/png;base64,{image_data}"
                            logger.debug("Processing raw base64 string")
                        except Exception:
                            logger.error(f"Invalid image data type or format: {type(image_data)}")
                            return """\n<image_description>\nInvalid image data format\n</image_description>\n"""
                    else:
                        logger.error(f"Unsupported image data type: {type(image_data)}")
                        return """\n<image_description>\nUnsupported image data type\n</image_description>\n"""

                # Create message for LLM
                message = HumanMessage(
                    content=[
                        {
                            "type": "image_url",
                            "image_url": {"url": image_url},
                        },
                        {"type": "text", "text": IMAGE_DESCRIBER},
                    ]
                )

                # Get description from LLM
                response = await self.vlm_endpoint.ainvoke([message])
                image_description = response.content

            except BadRequestError as e:
                # VLM returned 400 - log as warning without stack trace
                logger.warning("VLM rejected image captioning request", error=str(e)[:300])
                image_description = ""

            except Exception as e:
                is_external, status_code, url = is_external_resource_error(e)
                if is_external:
                    # Log external resource errors as warnings, not exceptions
                    # These are expected when VLM cannot fetch external URLs
                    log_msg = "Failed to fetch external image resource"
                    log_extra = {"error": str(e)[:200]}
                    if status_code:
                        log_extra["http_status"] = status_code
                    if url:
                        log_extra["url"] = url
                    elif self._is_http_url(str(image_data)):
                        log_extra["url"] = str(image_data)
                    logger.warning(log_msg, **log_extra)
                else:
                    logger.exception("Error while generating image description", error=str(e))
                image_description = ""

            return f"""<image_description>\n\n{image_description}\n\n</image_description>"""

    async def caption_images(self, images: list[Image.Image], desc: str = "Captioning images") -> list[str]:
        """Generate captions for a list of PIL images concurrently.

        Args:
            images: List of PIL Image objects to caption.
            desc: Description for the progress bar.

        Returns:
            List of captions in the same order as input images.
        """
        if not images:
            return []

        tasks = [self.get_image_description(image_data=img) for img in images]
        try:
            results = await tqdm.gather(*tasks, desc=desc)
        except asyncio.CancelledError:
            for task in tasks:
                if hasattr(task, "cancel"):
                    task.cancel()
            raise
        return results

    async def replace_markdown_images_with_captions(
        self,
        content: str,
        caption_http_urls: bool | None = None,
        caption_data_uris: bool = True,
        desc: str = "Captioning images",
    ) -> str:
        """Find markdown image references and replace with VLM-generated captions.

        Args:
            content: Markdown content containing ![alt](url) image references.
            caption_http_urls: Whether to caption HTTP/HTTPS URLs.
                If None, uses config value `loader.image_captioning_url`.
            caption_data_uris: Whether to caption data URI images.
            desc: Description for the progress bar.

        Returns:
            Content with image references replaced by captions.
        """
        if not self.image_captioning:
            return content

        # Determine URL captioning setting
        if caption_http_urls is None:
            caption_http_urls = self.image_captioning_url

        # Find all images
        http_matches = self.HTTP_IMAGE_PATTERN.findall(content)
        data_uri_matches = self.DATA_URI_IMAGE_PATTERN.findall(content)

        logger.debug(
            "Found images in markdown",
            http_images=len(http_matches),
            data_uri_images=len(data_uri_matches),
        )

        # Build tasks dict mapping markdown syntax to coroutine
        tasks = {}

        if caption_http_urls:
            for alt, url in http_matches:
                markdown_syntax = f"![{alt}]({url})"
                tasks[markdown_syntax] = self.get_image_description(url)

        if caption_data_uris:
            for alt, data_uri in data_uri_matches:
                markdown_syntax = f"![{alt}]({data_uri})"
                tasks[markdown_syntax] = self.get_image_description(data_uri)

        if not tasks:
            return content

        # Execute all captioning tasks concurrently
        try:
            captions = await tqdm.gather(*tasks.values(), desc=desc)
            image_to_caption = dict(zip(tasks.keys(), captions))

            # Replace images with captions
            logger.debug("Replacing image references", image_count=len(image_to_caption))
            for md_syntax, caption in image_to_caption.items():
                content = content.replace(md_syntax, caption)

        except asyncio.CancelledError:
            logger.warning("Image captioning cancelled")
            raise

        return content
