"""
Vision: describe a screenshot or image with the first working brain in the vision chain.
Works with OpenAI compatible vision models and with Anthropic models.
"""
from __future__ import annotations

from core import llm


async def analyze_image(
    image_base64: str,
    question: str = "Describe this screen in detail. List all visible UI elements, buttons, text, and their approximate positions.",
    screen_width: int = None,
    screen_height: int = None,
) -> str:
    """Analyze a base64 encoded PNG (raw or data: URL) and return a description."""
    prompt = question
    if screen_width and screen_height:
        prompt = f"Screen size: {screen_width}x{screen_height}px. {question}"
    return await llm.describe_image(image_base64, prompt)
