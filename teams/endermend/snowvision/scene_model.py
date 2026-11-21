"""Optional Weather VLM v2 adapter, matching Eazyan's public scene contract."""
import asyncio
from urllib.parse import urlparse
from pydantic import BaseModel, ConfigDict, Field, StrictBool
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

async def analyze_scene(client, url, encoded):
    parsed=urlparse(url)
    if parsed.scheme not in ('http','https') or not parsed.hostname or parsed.username or parsed.password:
        raise ValueError('Некорректный адрес модели')
    response=await asyncio.wait_for(client.post(url.rstrip('/')+'/v2/scene',
        files={'image':('frame.jpg',encoded,'image/jpeg')}),25)
    response.raise_for_status()
    return SceneEvidence.model_validate(response.json()).model_dump()
