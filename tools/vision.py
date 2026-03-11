"""
Vision LLM — analyze screenshots from phone or desktop.
Uses the first available vision-capable provider.
"""
from __future__ import annotations
import config
from core.llm import get_vision_client


async def analyze_image(
    image_base64: str,
    question: str = "Describe this screen in detail. List all visible UI elements, buttons, text, and their approximate positions.",
    screen_width: int = None,
    screen_height: int = None,
) -> str:
    """Analyze a base64-encoded image and return a description."""
    client, model = await get_vision_client()

    context = question
    if screen_width and screen_height:
        context = f"Screen size: {screen_width}x{screen_height}px. {question}"

    img_data = image_base64
    if "," in image_base64:
        img_data = image_base64.split(",", 1)[1]

    response = await client.chat.completions.create(
        model=model,
        messages=[{
            "role": "user",
            "content": [
                {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{img_data}"}},
                {"type": "text", "text": context},
            ],
        }],
        max_tokens=1024,
    )
    return response.choices[0].message.content
