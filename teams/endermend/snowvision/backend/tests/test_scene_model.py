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

    async def test_local_visual_model_contract_and_icing_uncertainty(self):
        payload = dict(schema_version='2',model='test',prompt_version='test',format_valid=True,
                       falling_snow='uncertain',snowfall_intensity='uncertain',air_view='limited',
                       ground_view='good',snow_on_ground='yes',road_view='limited',
                       possible_icing='uncertain',visual_evidence='Блеск не позволяет отличить воду от льда')
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
        self.assertEqual(result['possible_icing'], 'uncertain')
        self.assertEqual(result['snow_on_ground'], 'yes')
        self.assertEqual(result['prompt_version'], 'snowvision-visual-1')
