"""
ai.py — Claude API integration for the health app.

Isolates the `anthropic` dependency: meal-photo analysis (vision → structured
calories/macros) and a short AI daily health summary. The API key lives only in
the app-data dir (settings DB or the ANTHROPIC_API_KEY env var) — never in the
repo (which is public). Photos are sent to the Claude API, billed to the user's
own Anthropic account.
"""

import base64
import io
import json
import os
from typing import List

DEFAULT_MODEL = "claude-opus-4-8"

MEAL_PROMPT = (
    "You are a nutrition estimator. Look at this photo of a meal and estimate its "
    "nutrition. Identify the foods on the plate and give your best single estimate "
    "of total calories and grams of protein, carbohydrates, and fat for the whole "
    "meal shown. Be realistic about portion sizes. These are estimates, not exact "
    "values."
)


def _client(api_key):
    import anthropic
    key = api_key or os.environ.get("ANTHROPIC_API_KEY")
    if not key:
        raise RuntimeError(
            "No Anthropic API key set. Add one in Settings (get a key at "
            "console.anthropic.com), or set ANTHROPIC_API_KEY.")
    return anthropic.Anthropic(api_key=key)


def _encode_image(path, max_edge=1568):
    """Downscale to <= max_edge on the long side and JPEG-encode, to keep image
    token cost down. Returns base64 string."""
    from PIL import Image
    img = Image.open(path)
    if img.mode != "RGB":
        img = img.convert("RGB")
    w, h = img.size
    scale = max_edge / float(max(w, h))
    if scale < 1:
        img = img.resize((int(w * scale), int(h * scale)))
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=85)
    return base64.standard_b64encode(buf.getvalue()).decode("utf-8")


def analyze_meal_photo(image_path, notes="", model=None, api_key=None):
    """Send a meal photo to Claude and return structured nutrition for storage:
    {calories, protein_g, carbs_g, fat_g, ai_description, ai_items_json}."""
    from pydantic import BaseModel

    class MealNutrition(BaseModel):
        calories: int
        protein_g: float
        carbs_g: float
        fat_g: float
        description: str
        items: List[str]

    client = _client(api_key)
    data = _encode_image(image_path)
    text = MEAL_PROMPT
    if notes:
        text += f"\n\nThe person added these notes about the plate: {notes}"

    resp = client.messages.parse(
        model=model or DEFAULT_MODEL,
        max_tokens=1024,
        messages=[{"role": "user", "content": [
            {"type": "image", "source": {"type": "base64",
                                         "media_type": "image/jpeg", "data": data}},
            {"type": "text", "text": text},
        ]}],
        output_format=MealNutrition,
    )
    m = resp.parsed_output
    return {
        "calories": m.calories,
        "protein_g": m.protein_g,
        "carbs_g": m.carbs_g,
        "fat_g": m.fat_g,
        "ai_description": m.description,
        "ai_items_json": json.dumps(m.items),
    }


def daily_health_summary(facts_text, model=None, api_key=None):
    """Short plain-English synthesis of a day's sleep + activity + diet, with a
    couple of concrete suggestions. Returns the text."""
    client = _client(api_key)
    resp = client.messages.create(
        model=model or DEFAULT_MODEL,
        max_tokens=600,
        system=(
            "You are a concise, supportive, evidence-based health companion. Given "
            "one day's sleep, activity, and diet data, write 3-5 sentences "
            "connecting them (what likely helped or hurt), then 2-3 concrete, "
            "specific suggestions for tomorrow. Plain language, no medical "
            "diagnosis, no hedging filler."),
        messages=[{"role": "user", "content": facts_text}],
    )
    return "".join(b.text for b in resp.content if b.type == "text").strip()
