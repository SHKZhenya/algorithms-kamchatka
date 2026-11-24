import unittest
import os
from unittest.mock import patch
import httpx
from pydantic import ValidationError
from snowvision.adapters.scene_model import analyze_scene

class ModelTests(unittest.IsolatedAsyncioTestCase):
    @patch.dict(os.environ, {"SCENE_MODEL_PROVIDER": "weather_vlm"})
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
            payload['road_coverage'] = 'partial'
            result = await analyze_scene(client, 'http://model.test', b'image')
            self.assertEqual(result['road_coverage'], 'partial')
            payload['road_coverage'] = 'ice'
            with self.assertRaises(ValidationError):
                await analyze_scene(client, 'http://model.test', b'image')
            payload['road_coverage'] = 'uncertain'
            payload['format_valid']='true'
            with self.assertRaises(ValidationError):
                await analyze_scene(client,'http://model.test',b'image')

    async def test_local_visual_model_numeric_contract(self):
        payload = dict(snow_probability=0.8, snowing_probability=0.3,
                       ice_confidence=0.5, image_confidence=0.7)
        def reply(request):
            import json
            sent = json.loads(request.content)
            self.assertEqual(request.url.path, '/api/generate')
            self.assertEqual(sent['options']['num_ctx'], 2048)
            self.assertTrue(sent['images'])
            return httpx.Response(200,json={'done': True, 'model': 'gemma3:4b',
                                            'response': json.dumps(payload)})
        with patch.dict(os.environ, {'SCENE_MODEL_PROVIDER': 'ollama'}):
            async with httpx.AsyncClient(transport=httpx.MockTransport(reply)) as client:
                result = await analyze_scene(client, 'http://model.test', b'image')
        self.assertEqual(result['snow_probability'], 0.8)
        self.assertEqual(result['snowing_probability'], 0.3)
        self.assertEqual(result['prompt_version'], 'snowvision-numeric-1')

    async def test_numeric_model_rejects_invalid_values(self):
        import json
        payload = dict(snow_probability=0.8, snowing_probability=0.3,
                       ice_confidence=0.5, image_confidence=0.7)
        def reply(request):
            return httpx.Response(200, json={'done': True, 'model': 'test', 'response': json.dumps(payload)})
        with patch.dict(os.environ, {'SCENE_MODEL_PROVIDER': 'ollama'}):
            async with httpx.AsyncClient(transport=httpx.MockTransport(reply)) as client:
                for bad in (-0.1, 1.1, '0.5', True, float('nan')):
                    payload['ice_confidence'] = bad
                    with self.subTest(bad=bad), self.assertRaises(ValidationError):
                        await analyze_scene(client, 'http://model.test', b'image')
                del payload['ice_confidence']
                with self.assertRaises(ValidationError):
                    await analyze_scene(client, 'http://model.test', b'image')
