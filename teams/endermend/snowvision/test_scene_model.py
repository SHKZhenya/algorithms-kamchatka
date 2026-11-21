import unittest
import httpx
from pydantic import ValidationError
from scene_model import analyze_scene

class ModelTests(unittest.IsolatedAsyncioTestCase):
    async def test_validated_public_contract(self):
        payload=dict(schema_version='2',model='test',prompt_version='test',format_valid=True,
                     falling_snow='yes',snowfall_intensity='light',air_view='good')
        async with httpx.AsyncClient(transport=httpx.MockTransport(lambda req:httpx.Response(200,json=payload))) as client:
            result=await analyze_scene(client,'http://model.test',b'image')
            self.assertEqual(result['falling_snow'],'yes')
            payload['format_valid']='true'
            with self.assertRaises(ValidationError):
                await analyze_scene(client,'http://model.test',b'image')
