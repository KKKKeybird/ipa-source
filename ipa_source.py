#!/usr/bin/env python3
"""Private PlayCover feeds with local files, URLs and live OpenList redirects."""
from __future__ import annotations
import argparse, contextlib, hashlib, hmac, json, os, pathlib, plistlib, re, shutil, tempfile, urllib.error, urllib.parse, urllib.request, zipfile
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


def load(path, default=None):
    p=pathlib.Path(path)
    return json.loads(p.read_text()) if p.exists() else default


def save(path, value):
    p=pathlib.Path(path);p.parent.mkdir(parents=True,exist_ok=True)
    fd, name=tempfile.mkstemp(prefix=p.name+'.',dir=p.parent)
    try:
        with os.fdopen(fd,'w') as f:json.dump(value,f,indent=2,ensure_ascii=False)
        os.replace(name,p)
    finally:
        if os.path.exists(name):os.unlink(name)


def metadata(path):
    with zipfile.ZipFile(path) as z:
        names=[n for n in z.namelist() if re.fullmatch(r'Payload/[^/]+\.app/Info\.plist',n)]
        if len(names)!=1:raise ValueError('Expected exactly one Payload/*.app/Info.plist')
        if z.getinfo(names[0]).file_size>4*1024*1024:raise ValueError('Info.plist is too large')
        d=plistlib.loads(z.read(names[0]))
    result={'bundleID':d.get('CFBundleIdentifier',''),'name':d.get('CFBundleDisplayName') or d.get('CFBundleName',''),'version':str(d.get('CFBundleShortVersionString') or d.get('CFBundleVersion',''))}
    if not all(result.values()):raise ValueError('IPA metadata missing bundle ID, name or version')
    result['platforms']=d.get('CFBundleSupportedPlatforms',[])
    return result


def sha256(path):
    h=hashlib.sha256()
    with open(path,'rb') as f:
        for chunk in iter(lambda:f.read(1024*1024),b''):h.update(chunk)
    return h.hexdigest()


def remote_url(url):
    p=urllib.parse.urlsplit(url)
    if p.scheme not in ('https','http') or not p.hostname or p.username or p.password:raise ValueError('Expected HTTP(S) URL without embedded credentials')
    return url


def openlist(config,path,ua='IPA-Source/1.0'):
    req=urllib.request.Request(config['openlist_url'].rstrip('/')+'/api/fs/get',data=json.dumps({'path':path,'password':''}).encode(),headers={'Authorization':config['openlist_token'],'Content-Type':'application/json','User-Agent':ua})
    with urllib.request.urlopen(req,timeout=30) as r:d=json.load(r)
    if d.get('code')!=200 or not d.get('data',{}).get('raw_url'):raise RuntimeError('OpenList did not return a download link')
    return remote_url(d['data']['raw_url'])


def add(config,args):
    supplied=vars(args);m={}
    if args.inspect_from:m=metadata(args.inspect_from)
    elif args.file:m=metadata(args.file)
    elif args.url and not all(supplied.get(k) for k in ['bundle_id','name','version']):
        with tempfile.TemporaryDirectory() as temp:
            p=pathlib.Path(temp)/'input.ipa'
            req=urllib.request.Request(remote_url(args.url),headers={'User-Agent':'IPA-Source/1.0'})
            with urllib.request.urlopen(req,timeout=120) as r,p.open('wb') as f:shutil.copyfileobj(r,f,1024*1024)
            m=metadata(p)
    for cli,key in [('bundle_id','bundleID'),('name','name'),('version','version')]:
        if supplied.get(cli):m[key]=supplied[cli]
    if not all(m.get(k) for k in ['bundleID','name','version']):raise ValueError('Supply IPA metadata or --inspect-from')
    if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.-]{0,254}',m['bundleID']):raise ValueError('Invalid bundle ID')
    row={k:m[k] for k in ['bundleID','name','version']};row['itunesLookup']=args.itunes_lookup or '';row['platforms']=m.get('platforms',[])
    if args.file:
        root=pathlib.Path(config['files_root']);root.mkdir(parents=True,exist_ok=True);digest=sha256(args.file);dst=root/(digest+'.ipa')
        if not dst.exists():
            tmp=dst.with_suffix('.partial');shutil.copyfile(args.file,tmp);os.replace(tmp,dst)
        row.update(kind='file',source=str(dst),sha256=digest)
    elif args.url:row.update(kind='url',source=remote_url(args.url))
    elif args.openlist_path:
        if not args.openlist_path.startswith('/') or '..' in pathlib.PurePosixPath(args.openlist_path).parts:raise ValueError('Invalid OpenList path')
        row.update(kind='openlist',source=args.openlist_path)
    else:raise ValueError('Supply --file, --url or --openlist-path')
    catalog=load(config['catalog_path'],[]);catalog=[x for x in catalog if x['bundleID']!=row['bundleID']]+[row];save(config['catalog_path'],catalog)
    print(json.dumps({k:row[k] for k in ['bundleID','name','version']},ensure_ascii=False))


def handler(config):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self,*args):pass # feed capability and temporary URLs must never enter logs
        def reply(self,status,body=b'',headers=None):
            self.send_response(status)
            self.send_header('Cache-Control','no-store')
            for k,v in (headers or {}).items():self.send_header(k,v)
            self.send_header('Content-Length',str(len(body)));self.end_headers()
            if self.command!='HEAD':self.wfile.write(body)
        def json(self,status,value):self.reply(status,json.dumps(value,ensure_ascii=False).encode(),{'Content-Type':'application/json; charset=utf-8'})
        def file(self,path):
            p=pathlib.Path(path)
            if not p.is_file():return self.reply(404)
            size=p.stat().st_size;start=0;end=size-1;status=200
            rng=self.headers.get('Range')
            if rng:
                m=re.fullmatch(r'bytes=(\d*)-(\d*)',rng)
                if not m or not any(m.groups()):return self.reply(416,headers={'Content-Range':f'bytes */{size}'})
                a,b=m.groups()
                if a:start=int(a);end=min(int(b) if b else end,end)
                else:start=max(0,size-int(b))
                if start>=size or end<start:return self.reply(416,headers={'Content-Range':f'bytes */{size}'})
                status=206
            self.send_response(status);self.send_header('Content-Type','application/octet-stream');self.send_header('Accept-Ranges','bytes');self.send_header('Cache-Control','no-store');self.send_header('Content-Length',str(end-start+1))
            if status==206:self.send_header('Content-Range',f'bytes {start}-{end}/{size}')
            self.end_headers()
            if self.command=='HEAD':return
            with p.open('rb') as f:
                f.seek(start);remaining=end-start+1
                while remaining:
                    b=f.read(min(1024*1024,remaining))
                    if not b:break
                    self.wfile.write(b);remaining-=len(b)
        def do_HEAD(self):self.do_GET()
        def do_GET(self):
            try:self.route()
            except (BrokenPipeError,ConnectionResetError):pass
            except Exception:self.json(502,{'error':'Source temporarily unavailable; inspect server configuration or OpenList status'})
        def route(self):
            path=urllib.parse.urlsplit(self.path).path;parts=path.strip('/').split('/')
            if path=='/health':return self.json(200,{'status':'ok'})
            if parts[0]=='artifacts':
                auth=self.headers.get('Authorization','');expected='Bearer '+config['artifact_token']
                if not hmac.compare_digest(auth,expected):return self.reply(401)
                registry=load(config['artifact_registry'],{});key='/'.join(parts[1:]);item=registry.get(key)
                if not item:return self.reply(404)
                p=pathlib.Path(item['path']).resolve();root=pathlib.Path(config['artifact_root']).resolve()
                if not p.is_relative_to(root):return self.reply(404)
                return self.file(p)
            if len(parts)<3 or parts[0]!='s' or not hmac.compare_digest(parts[1],config['feed_token']):return self.reply(404)
            rows=load(config['catalog_path'],[]);base=config['public_base'].rstrip('/')+'/s/'+config['feed_token']
            if parts[2]=='source.json' and len(parts)==3:
                entries=[{k:r.get(k,'') for k in ['bundleID','name','version','itunesLookup']}|{'link':base+'/download/'+r['bundleID']+'.ipa'} for r in rows]
                return self.json(200,entries)
            if len(parts)==4 and parts[2]=='download' and parts[3].endswith('.ipa'):
                bid=parts[3][:-4];r=next((x for x in rows if x['bundleID']==bid),None)
                if not r:return self.reply(404)
                if r['kind']=='file':
                    p=pathlib.Path(r['source']).resolve()
                    if not p.is_relative_to(pathlib.Path(config['files_root']).resolve()):return self.reply(404)
                    return self.file(p)
                target=openlist(config,r['source'],self.headers.get('User-Agent','IPA-Source/1.0')) if r['kind']=='openlist' else remote_url(r['source'])
                return self.reply(302,headers={'Location':target})
            return self.reply(404)
    return Handler


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--config',default=os.environ.get('IPA_SOURCE_CONFIG','config.json'));sub=p.add_subparsers(dest='command',required=True)
    sub.add_parser('serve');sub.add_parser('feed-url')
    a=sub.add_parser('add');src=a.add_mutually_exclusive_group(required=True);src.add_argument('--file');src.add_argument('--url');src.add_argument('--openlist-path');a.add_argument('--inspect-from');a.add_argument('--bundle-id');a.add_argument('--name');a.add_argument('--version');a.add_argument('--itunes-lookup')
    args=p.parse_args();config=load(args.config)
    if not config:raise ValueError('Configuration file missing')
    if args.command=='serve':
        ThreadingHTTPServer(('0.0.0.0',config.get('port',8080)),handler(config)).serve_forever()
    elif args.command=='add':add(config,args)
    elif args.command=='feed-url':print(config['public_base'].rstrip('/')+'/s/'+config['feed_token']+'/source.json')
if __name__=='__main__':main()
