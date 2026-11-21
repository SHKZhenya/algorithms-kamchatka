import gzip,hashlib,importlib.util,json,shutil,subprocess,tempfile,unittest
from pathlib import Path
MODULE=Path(__file__).with_name('context.py')
spec=importlib.util.spec_from_file_location('context',MODULE);context=importlib.util.module_from_spec(spec);spec.loader.exec_module(context)

class ContextTests(unittest.TestCase):
    def test_known_tokens_are_redacted(self):
        token='gho_'+'a'*30
        self.assertNotIn(token,context.redact('token='+token))
        self.assertNotIn('secret-value-123456',context.redact('API_KEY=secret-value-123456'))

    def test_real_post_commit_captures_without_recursive_commit_or_dirty_tree(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d)
            (root/'tools/endermend-context').mkdir(parents=True)
            shutil.copyfile(MODULE,root/'tools/endermend-context/context.py')
            shutil.copytree(context.ROOT/'.githooks',root/'.githooks')
            (root/'.githooks/post-commit').chmod(0o755)
            (root/'teams/endermend/context').mkdir(parents=True)
            (root/'teams/endermend/context/CURRENT.md').write_text('Known current context\n')
            (root/'.gitignore').write_text('/.context-local/\n')
            def git(*args):return subprocess.check_output(['git',*args],cwd=root,text=True,stderr=subprocess.STDOUT)
            git('init');git('config','user.name','Context Test');git('config','user.email','test@example.invalid')
            git('config','core.hooksPath','.githooks');git('add','.');git('commit','-m','test hook')
            head=git('rev-parse','HEAD').strip();manifest=json.loads((root/'.context-local/latest.json').read_text())
            payload=gzip.decompress((root/manifest['archive']).read_bytes())
            self.assertEqual(manifest['head'],head)
            self.assertEqual(hashlib.sha256(payload).hexdigest(),manifest['sha256'])
            self.assertEqual(git('rev-list','--count','HEAD').strip(),'1')
            self.assertEqual(git('status','--porcelain').strip(),'')
            self.assertIn(b'Known current context',payload)

if __name__=='__main__':unittest.main()
