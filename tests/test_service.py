import argparse,contextlib,io,json,pathlib,plistlib,sys,tempfile,threading,unittest,urllib.error,urllib.request,zipfile
from unittest.mock import patch
sys.path.insert(0,str(pathlib.Path(__file__).parents[1]))
import ipa_source as s
class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self,*args,**kw):return None
class ServiceTest(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.root=pathlib.Path(self.tmp.name)
        self.cfg={'public_base':'https://feed.example','feed_token':'private-feed','artifact_token':'private-ci','catalog_path':str(self.root/'catalog.json'),'files_root':str(self.root/'files'),'artifact_registry':str(self.root/'registry.json'),'artifact_root':str(self.root/'artifacts')}
        with patch('socket.getfqdn',return_value='localhost'):self.server=s.ThreadingHTTPServer(('127.0.0.1',0),s.handler(self.cfg))
        self.thread=threading.Thread(target=self.server.serve_forever,daemon=True);self.thread.start();self.base='http://127.0.0.1:'+str(self.server.server_port)
    def tearDown(self):self.server.shutdown();self.server.server_close();self.thread.join();self.tmp.cleanup()
    def request(self,path,headers=None,method='GET'):
        req=urllib.request.Request(self.base+path,headers=headers or {},method=method)
        try:return urllib.request.build_opener(NoRedirect).open(req)
        except urllib.error.HTTPError as e:return e
    def test_private_feed_no_backend_credentials(self):
        s.save(self.cfg['catalog_path'],[{'bundleID':'example.app','name':'Example','version':'1.2','kind':'url','source':'https://private.example/file?sign=secret'}])
        self.assertEqual(self.request('/s/wrong/source.json').code,404)
        with self.request('/s/private-feed/source.json') as r:
            raw=r.read();row=json.loads(raw)[0]
            self.assertEqual(row['version'],'1.2');self.assertTrue(row['link'].endswith('.ipa'));self.assertNotIn(b'sign=secret',raw);self.assertNotIn(b'source',raw)
    def test_openlist_redirect_resolves_on_every_request_with_download_user_agent(self):
        s.save(self.cfg['catalog_path'],[{'bundleID':'example.app','kind':'openlist','source':'/115/ipa/app.ipa'}])
        with patch.object(s,'openlist',side_effect=['https://download.example/one','https://download.example/two']) as resolve:
            a=self.request('/s/private-feed/download/example.app.ipa',{'User-Agent':'PlayCover'});b=self.request('/s/private-feed/download/example.app.ipa',{'User-Agent':'PlayCover'})
            self.assertEqual(a.code,302);self.assertNotEqual(a.headers['Location'],b.headers['Location']);self.assertEqual(resolve.call_args.args[2],'PlayCover');a.close();b.close()
    def test_ci_artifact_requires_bearer_and_rejects_escape(self):
        root=pathlib.Path(self.cfg['artifact_root']);root.mkdir();p=root/'real.ipa';p.write_bytes(b'abcdefghij')
        s.save(self.cfg['artifact_registry'],{'run/real.ipa':{'path':str(p)},'run/outside.ipa':{'path':str(self.root/'catalog.json')}})
        self.assertEqual(self.request('/artifacts/run/real.ipa').code,401)
        auth={'Authorization':'Bearer private-ci','Range':'bytes=2-5'}
        with self.request('/artifacts/run/real.ipa',auth) as r:self.assertEqual(r.code,206);self.assertEqual(r.read(),b'cdef');self.assertEqual(r.headers['Content-Range'],'bytes 2-5/10')
        self.assertEqual(self.request('/artifacts/run/outside.ipa',auth).code,404)
    def test_local_ipa_metadata_import_and_head_range(self):
        p=self.root/'app.ipa'
        with zipfile.ZipFile(p,'w') as z:z.writestr('Payload/App.app/Info.plist',plistlib.dumps({'CFBundleIdentifier':'example.app','CFBundleDisplayName':'Example','CFBundleShortVersionString':'1.2','CFBundleSupportedPlatforms':['iPhoneOS']}))
        a=argparse.Namespace(file=str(p),url=None,openlist_path=None,inspect_from=None,bundle_id=None,name=None,version=None,itunes_lookup=None)
        with contextlib.redirect_stdout(io.StringIO()):s.add(self.cfg,a)
        with self.request('/s/private-feed/download/example.app.ipa',method='HEAD') as r:self.assertEqual(int(r.headers['Content-Length']),p.stat().st_size);self.assertEqual(r.read(),b'')
        self.assertEqual(self.request('/s/private-feed/download/example.app.ipa',{'Range':'bytes=9999999-'}).code,416)
    def test_non_http_url_rejected(self):
        for url in ['file:///etc/passwd','https://admin:password@example.com/file.ipa','javascript:alert(1)']:
            with self.assertRaises(ValueError):s.remote_url(url)
if __name__=='__main__':unittest.main()
