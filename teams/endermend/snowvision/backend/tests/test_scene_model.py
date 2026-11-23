import unittest
import httpx
from pydantic import ValidationError
from snowvision.adapters.scene_model import analyze_scene

class ModelTests(unittest.IsolatedAsyncioTestCase):
    async def test_validated_public_contract(self):
        payload=dict(schema_version='2',model='test',prompt_version='test',format_valid=True,
                     falling_snow='yes',snowfall_intensity='light',air_view='good')
        async with httpx.AsyncClient(transport=httpx.MockTransport(lambda req:httpx.Response(200,json=payload))) as client:
            result=await analyze_scene(client,'http://model.test',b'image')
            self.assertEqual(result['falling_snow'],'yes')
            self.assertNotIn('snow_on_ground', result)
            payload.update(snow_on_ground='yes', ground_view='limited')
            result = await analyze_scene(client, 'http://model.test', b'image')
            self.assertEqual(result['snow_on_ground'], 'yes')
            self.assertEqual(result['ground_view'], 'limited')
            payload['format_valid']='true'
            with self.assertRaises(ValidationError):
                await analyze_scene(client,'http://model.test',b'image')
