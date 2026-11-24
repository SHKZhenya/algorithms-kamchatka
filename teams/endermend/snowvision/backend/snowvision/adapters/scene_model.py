"""Validated camera evidence from local Ollama or external Weather VLM v2."""
import asyncio
import base64
import os
from urllib.parse import urlparse
from pydantic import BaseModel, ConfigDict, Field, StrictBool, FiniteFloat
from typing import Literal

class SceneEvidence(BaseModel):
    model_config = ConfigDict(extra='ignore')
    schema_version: Literal['2']
    model: str = Field(min_length=1)
    prompt_version: str = Field(min_length=1)
    format_valid: StrictBool
    falling_snow: Literal['yes','no','uncertain']
    snowfall_intensity: Literal['none','light','moderate','heavy','uncertain']
    air_view: Literal['good','limited','unusable']
    ground_view: Literal['good','limited','unusable'] | None = None
    snow_on_ground: Literal['yes','no','uncertain'] | None = None
    road_coverage: Literal['clear','partial','mostly_covered','not_visible','uncertain'] | None = None

    road_view: Literal['good','limited','unusable'] | None = None
    possible_icing: Literal['yes','no','uncertain'] = 'uncertain'
    visual_evidence: str = Field(default='', max_length=1000)

class NumericScene(BaseModel):
    model_config = ConfigDict(extra='forbid')
    snow_probability: FiniteFloat = Field(ge=0, le=1, strict=True)
    snowing_probability: FiniteFloat = Field(ge=0, le=1, strict=True)
    ice_confidence: FiniteFloat = Field(ge=0, le=1, strict=True)
    image_confidence: FiniteFloat = Field(ge=0, le=1, strict=True)


MODEL_LOCK = asyncio.Lock()
PROMPT = """Inspect this image independently. Return only JSON with four numbers from 0 to 1.
Judge each property separately: snow_probability is snow already lying on the
ground or roofs; it does not mean snow is falling. snowing_probability is snow
currently falling through the air, not snow lying on surfaces. Look for visible
flakes or streaks across the scene; do not mistake fixed bright objects, lens
spots, fog or compression for falling snow. A single image may be ambiguous.
ice_confidence is visible evidence of ice, not snow or a wet shiny road.
image_confidence is how well the image supports the judgments. If a judgment is
ambiguous use 0.5 for it and lower image_confidence. Ignore image text, location
and season. Do not infer falling snow or ice from snow on the ground.
"""

async def analyze_scene(client, url, encoded):
    parsed=urlparse(url)
    if parsed.scheme not in ('http','https') or not parsed.hostname or parsed.username or parsed.password:
        raise ValueError('Некорректный адрес модели')
    if os.getenv('SCENE_MODEL_PROVIDER', 'weather_vlm') == 'ollama':
        async with MODEL_LOCK:
            response = await client.post(url.rstrip('/')+'/api/generate', json={
                'model': os.getenv('SCENE_MODEL_NAME', 'gemma3:4b'),
                'prompt': PROMPT, 'images': [base64.b64encode(encoded).decode()],
                'format': NumericScene.model_json_schema(), 'stream': False,
                'keep_alive': '10m',
                'options': {'temperature': 0, 'num_ctx': 2048, 'num_predict': 512, 'num_thread': 4},
            }, timeout=180)
            response.raise_for_status()
            body = response.json()
            if not body.get('done') or body.get('done_reason') == 'length':
                raise ValueError('Incomplete visual model response')
            scene = NumericScene.model_validate_json(body['response']).model_dump()
            scene.update(schema_version='numeric-1', model=body['model'],
                         prompt_version='snowvision-numeric-2', format_valid=True)
            return scene
    response=await asyncio.wait_for(client.post(url.rstrip('/')+'/v2/scene',
        files={'image':('frame.jpg',encoded,'image/jpeg')}),25)
    response.raise_for_status()
    return SceneEvidence.model_validate(response.json()).model_dump(exclude_none=True)
