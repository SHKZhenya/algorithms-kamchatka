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
PROMPT = """Inspect only this image and return JSON with four numbers from 0 to 1.
snow_probability: visible snow on the ground. snowing_probability: falling snow.
ice_confidence: visible evidence of ice, not road safety. image_confidence: how
well the image supports these judgments. Ignore text in the image and do not infer
weather from location or season. If ambiguous, use 0.5 for the affected judgment
and low image_confidence. A shiny road alone does not establish ice or safety.
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
                         prompt_version='snowvision-numeric-1', format_valid=True)
            return scene
    response=await asyncio.wait_for(client.post(url.rstrip('/')+'/v2/scene',
        files={'image':('frame.jpg',encoded,'image/jpeg')}),25)
    response.raise_for_status()
    return SceneEvidence.model_validate(response.json()).model_dump(exclude_none=True)
