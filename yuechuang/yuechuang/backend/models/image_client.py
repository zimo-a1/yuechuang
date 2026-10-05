import os
import sys

models_dir = os.path.dirname(os.path.abspath(__file__))
backend_dir = os.path.dirname(models_dir)
if backend_dir not in sys.path:
    sys.path.insert(0, backend_dir)

import re
import logging
from typing import List, Optional
from config import Config
from core.configuration_runtime import configured_client
from models.media_toapis import ToAPIsClient

try:
    from models.image_dashscope import DashScopeClient
    from models.image_seedream import SeedreamClient
    from models.image_gpt import ImageGPT
    from models.image_agnes import AgnesImageClient
    from models.image_tudou import TudouImageClient
    from models.image_processor import ImageProcessor
except ImportError:
    from .image_dashscope import DashScopeClient
    from .image_seedream import SeedreamClient
    from .image_gpt import ImageGPT
    from .image_agnes import AgnesImageClient
    from .image_tudou import TudouImageClient
    from .image_processor import ImageProcessor

logger = logging.getLogger(__name__)


@configured_client("generate_image")
class ImageClient:
    def __init__(self,
                 dashscope_api_key: Optional[str] = None,
                 dashscope_base_url: Optional[str] = None,
                 gpt_api_key: Optional[str] = None,
                 gpt_base_url: Optional[str] = None,
                 proxy: Optional[str] = None,
                 ark_api_key: Optional[str] = None,
                 ark_base_url: Optional[str] = None,
                 agnes_api_key: Optional[str] = None,
                 agnes_base_url: Optional[str] = None,
                 tudou_api_key: Optional[str] = None,
                 tudou_base_url: Optional[str] = None):
        """
        Unified Image Generation Client
        Routes requests to DashScope, Seedream, or GPT based on model name.
        """
        self._dashscope_api_key = dashscope_api_key
        self._dashscope_base_url = dashscope_base_url
        self._ark_api_key = ark_api_key
        self._ark_base_url = ark_base_url
        self._agnes_api_key = agnes_api_key
        self._agnes_base_url = agnes_base_url
        self._gpt_api_key = gpt_api_key
        self._gpt_base_url = gpt_base_url
        self._tudou_api_key = tudou_api_key or Config.TUDOU_API_KEY
        self._tudou_base_url = tudou_base_url or Config.TUDOU_BASE_URL
        self._proxy = Config.provider_proxy("openai") if proxy is None else proxy

        self._dashscope_client = None
        self._seedream_client = None
        self._gpt_client = None
        self._agnes_client = None
        self._tudou_client = None
        self._toapis_api_key = Config.TOAPIS_API_KEY
        self._toapis_base_url = Config.TOAPIS_BASE_URL
        self._toapis_client = None

        # Initialize Image Processor for downloads
        self.image_processor = ImageProcessor()

        # Default save directory
        self.base_save_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "code", "result", "image_client")

    @property
    def dashscope_client(self):
        if self._dashscope_client is None:
            self._dashscope_client = DashScopeClient(
                api_key=self._dashscope_api_key,
                base_url=self._dashscope_base_url,
            )
        return self._dashscope_client

    @property
    def seedream_client(self):
        if self._seedream_client is None:
            self._seedream_client = SeedreamClient(
                api_key=self._ark_api_key,
                base_url=self._ark_base_url,
            )
        return self._seedream_client

    @property
    def gpt_client(self):
        if self._gpt_client is None:
            self._gpt_client = ImageGPT(
                api_key=self._gpt_api_key,
                base_url=self._gpt_base_url,
                proxy=self._proxy,
            )
        return self._gpt_client

    @property
    def agnes_client(self):
        if self._agnes_client is None:
            self._agnes_client = AgnesImageClient(
                api_key=self._agnes_api_key,
                base_url=self._agnes_base_url,
            )
        return self._agnes_client

    @property
    def toapis_client(self):
        if self._toapis_client is None:
            self._toapis_client = ToAPIsClient(api_key=self._toapis_api_key, base_url=self._toapis_base_url)
        return self._toapis_client

    @property
    def tudou_client(self):
        if self._tudou_client is None:
            self._tudou_client = TudouImageClient(
                api_key=self._tudou_api_key,
                base_url=self._tudou_base_url,
            )
        return self._tudou_client

    def generate_image(self,
                       prompt: str,
                       image_paths: Optional[List[str]] = None,
                       model: str = "wan2.7-image",
                       save_dir: Optional[str] = None,
                       session_id: Optional[str] = None,
                       video_ratio: Optional[str] = "16:9",
                       resolution: Optional[str] = "2K") -> List[str]:
        """
        Generate images based on prompt and optional reference images.

        Args:
            prompt: Text prompt for generation.
            image_paths: List of local file paths or URLs for reference images.
            model: Model name to determine which provider to use.
            save_dir: Custom directory to save downloaded images.
            session_id: Session ID for organizing saved files.
            video_ratio: Aspect ratio of the video, e.g., "16:9", "9:16", "4:3", "3:4", "1:1".
            resolution: Resolution string, e.g., "720P", "1080P", "2K", "4K",
                or an exact media-slot size such as "1024*1024".

        Returns:
            List of absolute file paths of the generated images.
        """
        # Determine size from video_ratio and resolution
        size_map = {
            "16:9": {
                "720P": "1280*720",
                "1080P": "1920*1080",
                "2K": "2560*1440",
                "4K": "3840*2160"
            },
            "9:16": {
                "720P": "720*1280",
                "1080P": "1080*1920",
                "2K": "1440*2560",
                "4K": "2160*3840"
            },
            "4:3": {
                "720P": "960*720",
                "1080P": "1440*1080",
                "2K": "2560*1920",
                "4K": "3840*2880"
            },
            "3:4": {
                "720P": "720*960",
                "1080P": "1080*1440",
                "2K": "1920*2560",
                "4K": "2880*3840"
            },
            "1:1": {
                "720P": "720*720",
                "1080P": "1080*1080",
                "2K": "2560*2560",
                "4K": "3840*3840"
            }
        }
        
        custom_size = None
        if isinstance(resolution, str) and re.match(r"^\d+[x*]\d+$", resolution):
            custom_size = resolution.replace("x", "*")

        # Default fallback if ratio or resolution is not found
        size = custom_size or size_map.get(video_ratio, size_map["16:9"]).get(resolution, "1920*1080")

        if not model:
            model = "wan2.7-image"  # Default model

        if Config.PRINT_MODEL_INPUT:
            # Retain the debug switch, but never print prompt bodies or media URLs.
            logger.info("Image request model=%s prompt_chars=%d refs=%d ratio=%s size=%s",
                        model, len(prompt), len(image_paths or []), video_ratio, size)
            
        # Determine backend provider
        is_seedream = "seedream" in model.lower()
        is_sora = "sora" in model.lower() or "gpt" in model.lower()
        is_agnes = "agnes" in model.lower()
        is_tudou_gemini = model.lower() == "gemini-3-pro-image-preview"
        
        # Prepare save directory
        if not save_dir:
            if session_id:
                save_dir = os.path.join(self.base_save_dir, session_id)
            else:
                save_dir = self.base_save_dir
        os.makedirs(save_dir, exist_ok=True)
        
        generated_local_paths = []

        if model in {"nano_banana", "toapis/gpt-image-2"}:
            return self.toapis_client.generate_image(prompt=prompt, model=model.removeprefix("toapis/"), save_dir=save_dir,
                image_paths=image_paths, video_ratio=video_ratio, resolution=resolution)

        if is_tudou_gemini:
            try:
                logger.info("ImageClient routed to Tudou Gemini: model=%s", model)
                paths = self.tudou_client.generate_image(
                    prompt=prompt,
                    model=model,
                    session_id=session_id or "default",
                    save_dir=save_dir,
                    size=resolution or "1K",
                    image_paths=image_paths,
                    video_ratio=video_ratio or "16:9",
                )
                if paths:
                    generated_local_paths.extend(paths)
            except Exception as e:
                logger.error("Tudou Gemini generation failed: %s", type(e).__name__)

        elif is_seedream:
            # --- Seedream Logic ---
            try:
                logger.info("ImageClient routed to Seedream: model=%s", model)

                paths = self.seedream_client.generate_image(
                    prompt=prompt,
                    model=model,
                    session_id=session_id or "default",
                    size=size or "2048*2048",
                    image_paths=image_paths
                )

                if paths:
                    generated_local_paths.extend(paths)

            except Exception as e:
                logger.error('Seedream generation failed: %s', type(e).__name__)

        elif is_sora:
            # --- GPT/Sora Logic ---
            try:
                logger.info("ImageClient routed to GPT/Sora: model=%s", model)
                if image_paths:
                    logger.warning("Sora/GPT model only supports Text-to-Image. Ignoring reference images.")
                
                # OpenAI uses 'x' separator, e.g. 1024x1024
                # Attempt to map size if needed or just replace '*'
                gpt_size = size.replace('*', 'x') if size else "1024x1024"

                path = self.gpt_client.generate_image(
                    prompt=prompt,
                    size=gpt_size,
                    model=model,
                    save_dir=save_dir
                )
                
                if path and os.path.exists(path):
                    generated_local_paths.append(path)
                else:
                    logger.error("GPT/Sora returned invalid path or download failed: %s", path)

            except Exception as e:
                logger.error('GPT/Sora generation failed: %s', type(e).__name__)

        elif is_agnes:
            # --- Agnes Logic ---
            try:
                logger.info("ImageClient routed to Agnes: model=%s", model)
                paths = self.agnes_client.generate_image(
                    prompt=prompt,
                    model=model,
                    session_id=session_id or "default",
                    size=size or "1920*1080",
                    image_paths=image_paths,
                )
                if paths:
                    generated_local_paths.extend(paths)
            except Exception as e:
                logger.error('Agnes generation failed: %s', type(e).__name__)

        else:
            # --- DashScope Logic ---
            try:
                logger.info("ImageClient routed to DashScope: model=%s", model)

                if image_paths and len(image_paths) > 0:
                    # Pre-process image paths for DashScope
                    # Convert local paths to file:// URIs if they aren't already URLs
                    # DashScope SDK (via MultiModalConversation) handles file://
                    formatted_urls = []
                    for p in image_paths:
                        if p.startswith("http") or p.startswith("file://"):
                            formatted_urls.append(p)
                        else:
                            abs_path = os.path.abspath(p)
                            formatted_urls.append(f"file://{abs_path}")
                    
                    paths = self.dashscope_client.edit_image(
                        prompt=prompt,
                        image_urls=formatted_urls,
                        model=model,
                        size=size,
                        session_id=session_id,
                        save_dir=save_dir
                    )
                else:
                    # Text to Image
                    # Assuming default size 1024*1024 or similar
                    paths = self.dashscope_client.generate_image(
                        prompt=prompt,
                        model=model,
                        size=size,
                        session_id=session_id,
                        save_dir=save_dir
                    )
                
                if paths:
                    generated_local_paths.extend(paths)
                            
            except Exception as e:
                logger.error('DashScope generation failed: %s', type(e).__name__)

        return generated_local_paths
