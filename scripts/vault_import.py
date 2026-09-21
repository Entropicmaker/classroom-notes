#!/usr/bin/env python3
"""Read-only lookup/plan, or non-overwriting import of a note and local images."""
import argparse, hashlib, json, os, re, urllib.parse
from pathlib import Path

DEFAULT=Path(__file__).resolve().parents[1]/'references/vault.json'
IMAGE=re.compile(r'!\[([^\]\n]*)\]\(([^\s\)]+)\)')
ALLOWED={'.png','.jpg','.jpeg','.gif','.webp','.svg','.avif','.bmp'}
def sha(data):return hashlib.sha256(data).hexdigest()
def sha_file(path):
    digest=hashlib.sha256()
    with path.open('rb') as source:
        for chunk in iter(lambda:source.read(1024*1024),b''):digest.update(chunk)
    return digest.hexdigest()
def inside(root,path):
    root=root.resolve();path=path.resolve()
    if path!=root and root not in path.parents:raise ValueError('Path escapes allowed root: '+str(path))
    return path
def component(value):
    if not value or value in {'.','..'} or any(c in value for c in '/\\\x00\n\r') or value.startswith('.'):
        raise ValueError('Invalid path component: '+repr(value))
    return value

def prepare(a):
    cfg=json.loads(a.config.read_text(encoding='utf-8'));vault=Path(cfg['vault']).resolve()
    if not vault.is_dir():raise ValueError('Vault not found: '+str(vault))
    course=component(cfg.get('aliases',{}).get(a.course,a.course));base=inside(vault,vault/cfg['courses_dir']);folder=inside(base,base/course)
    if not folder.is_dir():raise ValueError('Course folder does not exist: '+course)
    src=[]
    for p in a.source:
        p=p.resolve()
        if not p.is_file():raise ValueError('Source missing: '+str(p))
        src.append({'name':p.name,'sha256':sha_file(p)})
    key=sha(json.dumps([x['sha256'] for x in src]).encode())
    receipts=inside(folder,folder/'.classroom-imports');existing=[]
    if receipts.is_dir():
        for p in receipts.glob('*.json'):
            try:
                rec=json.loads(p.read_text(encoding='utf-8'))
                if rec.get('source_key')==key:
                    target=inside(folder,folder/rec['note'])
                    if target.is_file():existing.append({'path':str(target),'modified_since_import':sha(target.read_bytes())!=rec.get('note_sha256')})
            except (ValueError,KeyError,OSError):continue
    return cfg,vault,course,folder,receipts,src,key,existing

def build_plan(a,state):
    cfg,vault,course,folder,receipts,src,key,existing=state
    if not a.title or not a.note:raise ValueError('--title and --note are required for plan/commit')
    title=component(a.title.removesuffix('.md'));target=inside(folder,folder/(title+'.md'))
    if target.exists():raise FileExistsError('Refusing to overwrite existing note: '+str(target))
    draft=a.note.resolve();text=draft.read_text(encoding='utf-8')
    if re.search(r'!\[\[',text):raise ValueError('Draft images must use standard Markdown local links, not wikilinks')
    matches=list(IMAGE.finditer(text))
    if len(matches)!=len(re.findall(r'!\[',text)):
        raise ValueError('Unsupported image syntax; use ![label](assets/simple-name.png)')
    noteid=sha(text.encode())[:12]
    assets=inside(vault,vault/cfg['attachments_dir']/course/(title+'-'+noteid))
    files={};mapping={}
    for m in matches:
        url=urllib.parse.urlsplit(m.group(2))
        if url.scheme or url.netloc or url.query or url.fragment:raise ValueError('Image must be a local file: '+m.group(2))
        raw=urllib.parse.unquote(url.path)
        if Path(raw).is_absolute():raise ValueError('Draft image must be relative')
        source=inside(draft.parent,draft.parent/raw)
        if not source.is_file() or source.suffix.lower() not in ALLOWED:raise ValueError('Unsupported or missing image: '+str(source))
        content=source.read_bytes();digest=sha(content);dest=inside(assets,assets/(digest[:20]+source.suffix.lower()))
        files[str(dest)]=(content,digest)
        mapping[m.group(2)]=urllib.parse.quote(os.path.relpath(dest,folder).replace(os.sep,'/'),safe='/')
    text=IMAGE.sub(lambda m:'!['+m.group(1)+']('+mapping[m.group(2)]+')',text)
    data=text.encode('utf-8');receipt={'source_key':key,'sources':src,'note':target.name,'note_sha256':sha(data),'images':[{'path':os.path.relpath(Path(p),vault),'sha256':v[1]} for p,v in files.items()]}
    recpath=inside(receipts,receipts/(sha(target.name.encode())[:16]+'.json'))
    for path,(content,digest) in files.items():
        d=Path(path)
        if d.exists() and (not d.is_file() or sha(d.read_bytes())!=digest):raise FileExistsError('Attachment conflict: '+path)
    if recpath.exists():raise FileExistsError('Import receipt already exists for title; choose a new version title')
    return target,data,files,recpath,receipt

def main():
    ap=argparse.ArgumentParser(description=__doc__);ap.add_argument('action',choices=['lookup','plan','commit']);ap.add_argument('--config',type=Path,default=DEFAULT);ap.add_argument('--course',required=True);ap.add_argument('--title');ap.add_argument('--note',type=Path);ap.add_argument('--source',type=Path,action='append',required=True);ap.add_argument('--new-version',action='store_true',help='Allow already-imported sources under a different, unused title')
    a=ap.parse_args();state=prepare(a);cfg,vault,course,folder,receipts,src,key,existing=state
    if a.action=='lookup':print(json.dumps({'course':course,'existing':existing},ensure_ascii=False));return
    if existing and not a.new_version:
        print(json.dumps({'status':'already_imported','existing':existing},ensure_ascii=False));return
    target,data,files,recpath,receipt=build_plan(a,state)
    if a.action=='plan':
        print(json.dumps({'status':'ready','note':str(target),'images':list(files),'source_count':len(src),'bytes':len(data)},ensure_ascii=False,indent=2));return
    # Reserve receipt and note without ever truncating another file. If any stage
    # fails, delete only files created by this invocation. Reused assets survive.
    created=[]
    try:
        for p,content in [(recpath,(json.dumps(receipt,ensure_ascii=False,indent=2)+'\n').encode())]:
            p.parent.mkdir(parents=True,exist_ok=True)
            f=p.open('xb');created.append(p)
            with f:f.write(content);f.flush();os.fsync(f.fileno())
        for path,(content,digest) in files.items():
            p=Path(path);p.parent.mkdir(parents=True,exist_ok=True)
            try:f=p.open('xb')
            except FileExistsError:
                if not p.is_file() or sha(p.read_bytes())!=digest:raise FileExistsError('Attachment changed during import')
                continue
            created.append(p)
            with f:f.write(content);f.flush();os.fsync(f.fileno())
        # Publish the note last, after all its attachments exist.
        f=target.open('xb');created.append(target)
        with f:f.write(data);f.flush();os.fsync(f.fileno())
    except BaseException:
        for p in reversed(created):
            try:p.unlink()
            except OSError:pass
        raise
    uri='obsidian://open?vault='+urllib.parse.quote(vault.name,safe='')+'&file='+urllib.parse.quote(str(target.relative_to(vault).with_suffix('')).replace(os.sep,'/'),safe='')
    print(json.dumps({'status':'imported','note':str(target),'image_count':len(files),'open':uri},ensure_ascii=False))
if __name__=='__main__':
    try:main()
    except (ValueError,OSError,KeyError) as e:raise SystemExit(str(e))
