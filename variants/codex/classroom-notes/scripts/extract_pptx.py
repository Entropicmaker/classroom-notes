#!/usr/bin/env python3
"""Extract PPTX text, tables, positioned picture references; no slide rendering."""
import argparse, hashlib, json, posixpath, re, zipfile
from pathlib import Path
import xml.etree.ElementTree as ET

NS = {'p':'http://schemas.openxmlformats.org/presentationml/2006/main',
      'a':'http://schemas.openxmlformats.org/drawingml/2006/main',
      'r':'http://schemas.openxmlformats.org/officeDocument/2006/relationships'}
def paragraphs(el):
    return [''.join(t.text or '' for t in p.findall('.//a:t',NS)) for p in el.findall('.//a:p',NS)]
def bounds(el):
    x=el.find('.//a:xfrm',NS)
    return {n: dict(v.attrib) for n in ['off','ext'] if x is not None and (v:=x.find('a:'+n,NS)) is not None}
def main():
    ap=argparse.ArgumentParser(description=__doc__);ap.add_argument('source',type=Path);ap.add_argument('--out',required=True,type=Path);a=ap.parse_args()
    out=a.out.resolve();out.mkdir(parents=True,exist_ok=True)
    if (out/'manifest.json').exists() or (out/'source.md').exists():raise ValueError('Output already populated; choose a new work directory')
    (out/'assets').mkdir(exist_ok=True)
    result={'source':str(a.source.resolve()),'sha256':hashlib.sha256(a.source.read_bytes()).hexdigest(),'slides':[]};md=[];seen={}
    with zipfile.ZipFile(a.source) as z:
        # Follow presentation order, not the numeric slide filenames.
        pres=ET.fromstring(z.read('ppt/presentation.xml'))
        rels={x.attrib['Id']:posixpath.normpath('ppt/'+x.attrib['Target']) for x in ET.fromstring(z.read('ppt/_rels/presentation.xml.rels')) if x.attrib.get('TargetMode')!='External'}
        order=[rels[x.attrib['{'+NS['r']+'}id']] for x in pres.findall('p:sldIdLst/p:sldId',NS)]
        for number,name in enumerate(order,1):
            root=ET.fromstring(z.read(name));rp=posixpath.dirname(name)+'/_rels/'+posixpath.basename(name)+'.rels';rm={}
            if rp in z.namelist():
                rm={x.attrib['Id']:posixpath.normpath(posixpath.dirname(name)+'/'+x.attrib['Target']) for x in ET.fromstring(z.read(rp)) if x.attrib.get('TargetMode')!='External'}
            slide={'number':number,'part':name,'paragraphs':paragraphs(root),'tables':[],'pictures':[],'text_shapes':[]}
            for sh in root.findall('.//p:sp',NS):
                texts=paragraphs(sh)
                if any(texts):slide['text_shapes'].append({'paragraphs':texts,'bounds':bounds(sh)})
            for tbl in root.findall('.//a:tbl',NS):
                slide['tables'].append([[' / '.join(paragraphs(c)) for c in row.findall('a:tc',NS)] for row in tbl.findall('a:tr',NS)])
            for j,pic in enumerate(root.findall('.//p:pic',NS),1):
                blip=pic.find('.//a:blip',NS); rid=None if blip is None else blip.attrib.get('{'+NS['r']+'}embed')
                if rid not in rm:
                    slide['pictures'].append({'warning':'Linked or unavailable picture','bounds':bounds(pic)});continue
                target=rm[rid]
                if not target.startswith('ppt/media/'):
                    slide['pictures'].append({'warning':'Unsupported image relationship','target':target});continue
                data=z.read(target);digest=hashlib.sha256(data).hexdigest();ext=Path(target).suffix.lower()
                if digest not in seen:
                    fn=f's{number:03}-img{j:02}-{digest[:8]}{ext}';p=out/'assets'/fn
                    with p.open('xb') as f:f.write(data)
                    seen[digest]='assets/'+fn
                slide['pictures'].append({'path':seen[digest],'sha256':digest,'bounds':bounds(pic),'crop':None if pic.find('.//a:srcRect',NS) is None else dict(pic.find('.//a:srcRect',NS).attrib)})
            md+=['',f'## 幻灯片 {number}','']+slide['paragraphs']
            for tbl in slide['tables']:
                md+=['','表格逐行（各单元格以 | 分隔）：']+[' | '.join(row) for row in tbl]
            if slide['pictures']:md+=['','图片：']+[x.get('path',x.get('warning','')) for x in slide['pictures']]
            result['slides'].append(slide)
    (out/'source.md').write_text('\n'.join(md),encoding='utf-8')
    (out/'manifest.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps({'slides':len(result['slides']),'unique_images':len(seen),'text':str(out/'source.md'),'manifest':str(out/'manifest.json')},ensure_ascii=False))
if __name__=='__main__':main()
