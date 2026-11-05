#!/usr/bin/env python3
"""Refresh two GitHub language SVGs with HyunCafe's cyan/blue/violet palette.

Uses only Python's standard library, obtains public statistics from the existing
GitHub Profile Summary Cards service, and fails closed if the upstream SVG
structure or safety properties differ from expectations. Never invents data.

No credentials needed.  Intended for a manually run Git Bash command and a
weekly GitHub Actions refresh after the profile PR is merged.
"""
from __future__ import annotations
import argparse
import hashlib
import html
import re
import sys
import urllib.error
import urllib.request
from pathlib import Path
from xml.etree import ElementTree as ET

ENDPOINT='https://github-profile-summary-cards.vercel.app/api/cards/'
PARAM='?username=HyunCafe&theme=tokyonight&title_color=0079FA&text_color=BFD9FF&bg_color=0B1120&border_color=315B91&icon_color=0079FA&chart_color=2100FA&animation=load'
SVG_NS='http://www.w3.org/2000/svg'
ET.register_namespace('', SVG_NS)
CARDS={
    'repos-per-language':'assests/lang-repos-themed.svg',
    'most-commit-language':'assests/lang-commits-themed.svg',
}
# Distinct but related hues. Same programming language retains its color on both cards.
COLORS={
    'javascript':'#34B9FF',
    'python':'#0079FA',
    'html':'#637CFF',
    'css':'#965BFA',
    'c#':'#C28BFF',
    'scss':'#DE8BFF',
    'typescript':'#4D9BFF',
    'shell':'#48CBDE',
    'powershell':'#60A8FF',
    'c++':'#B875FA',
    'c':'#7798FF',
    'ruby':'#D27DFF',
    'go':'#66CFEE',
    'java':'#7775FA',
}
OTHER=['#6AB7FA','#8B7BFF','#C69FFF','#37C8DD','#7DABFF','#B883FF']
FORBIDDEN={'script','foreignObject','image','iframe','object','embed','use','a','animate','animateMotion','animateTransform','set'}
INDEX_RE=re.compile(r'(?:^|;)\s*--gpsc-i\s*:\s*(\d+)(?=\s*(?:;|$))')
STYLE_FILL_RE=re.compile(r'(^|;)\s*fill\s*:\s*[^;]+',re.I)


def localname(tag: str)->str:
    return tag.rsplit('}',1)[-1]


def check_svg(text: str)->ET.Element:
    if len(text)>250_000 or not text.lstrip().startswith(('<svg','<?xml')):
        raise ValueError('Unexpected SVG size or document format')
    if any(k in text.lower() for k in ('<!doctype','<!entity','@import','javascript:', 'data:text/html')):
        raise ValueError('Disallowed construct in SVG')
    root=ET.fromstring(text)
    if localname(root.tag)!='svg': raise ValueError('Not an SVG')
    for node in root.iter():
        if localname(node.tag) in FORBIDDEN:raise ValueError('Forbidden SVG element: '+localname(node.tag))
        for key,val in node.attrib.items():
            a=localname(key).lower()
            if a.startswith('on') or a in {'src','srcset'}: raise ValueError('Forbidden SVG attribute')
            if a=='href' and not val.startswith('#'): raise ValueError('External resource reference')
            if 'url(' in val.lower() and re.search(r'url\(\s*["\']?(?!#)',val,re.I): raise ValueError('External URL reference')
        if localname(node.tag)=='style' and node.text:
            css=node.text.lower()
            if '@import' in css or re.search(r'url\(\s*["\']?(?!#)',css):raise ValueError('External CSS resource')
    return root


def index_of(node:ET.Element)->int:
    m=INDEX_RE.search(node.get('style',''))
    if not m:raise ValueError('Missing per-language animation/index marker')
    idx=int(m.group(1))
    if idx>20:raise ValueError('Unexpected language index')
    return idx


def set_fill(node:ET.Element, color:str)->None:
    # D3 sets arc fill in inline CSS; update that property, not other CSS.
    style=node.get('style','')
    if STYLE_FILL_RE.search(style):
        node.set('style',STYLE_FILL_RE.sub(lambda m:m.group(1)+'fill:'+color,style,count=1))
    else:
        node.set('style',(style.rstrip(';')+';' if style else '')+'fill:'+color)
    if 'fill' in node.attrib:node.set('fill',color)


def color_for(language:str)->str:
    language=language.lower().strip()
    if language in COLORS:return COLORS[language]
    i=int.from_bytes(hashlib.sha256(language.encode()).digest()[:2],'big')%len(OTHER)
    return OTHER[i]


def recolor(svg_content:str,expected_title:str)->tuple[bytes,dict[str,str]]:
    root=check_svg(svg_content)
    if expected_title not in svg_content:raise ValueError('Unexpected language card title')
    labels={}
    rects={}
    arcs={}
    title_seen=False
    for node in root.iter():
        tag=localname(node.tag)
        classes=node.get('class','').split()
        if 'gpsc-item' in classes and tag=='text':
            label=''.join(node.itertext()).strip()
            idx=index_of(node)
            # The upstream service now marks the CARD TITLE as gpsc-item with
            # --gpsc-i: 0, the same index as the first actual language.
            # Exclude that exact heading, not arbitrary duplicate labels.
            if label==expected_title:
                if title_seen or idx!=0:raise ValueError('Unexpected card-title structure')
                title_seen=True
                continue
            if idx in labels:raise ValueError('Duplicate language label index')
            labels[idx]=label
        elif 'gpsc-item' in classes and tag=='rect':
            idx=index_of(node)
            if idx in rects:raise ValueError('Duplicate legend index')
            rects[idx]=node
        elif tag=='g' and 'arc' in classes:
            idx=index_of(node)
            paths=[child for child in node if localname(child.tag)=='path']
            if len(paths)!=1 or idx in arcs:raise ValueError('Unexpected donut arc structure')
            arcs[idx]=paths[0]
    if not labels or len(labels)>10 or set(labels)!=set(rects) or set(labels)!=set(arcs):
        raise ValueError(f'Upstream donut structure changed: {len(labels)} labels, {len(rects)} legend items, {len(arcs)} arcs')
    mapping={}
    for idx,language in labels.items():
        col=color_for(language)
        rects[idx].set('fill',col)
        # Remove stale inline fill if supplied upstream in addition to the attribute.
        if re.search(r'(^|;)\s*fill\s*:',rects[idx].get('style',''),re.I):set_fill(rects[idx],col)
        set_fill(arcs[idx],col)
        mapping[language]=col
    # The root SVG contains only allowed elements and local fragment CSS references.
    data=ET.tostring(root,encoding='utf-8',xml_declaration=True)
    if len(data)>250_000:raise ValueError('Recolored SVG unexpectedly large')
    return data,mapping


def fetch_card(card:str)->str:
    url=ENDPOINT+card+PARAM
    req=urllib.request.Request(url,headers={'User-Agent':'HyunCafe-profile-colors/1.0','Accept':'image/svg+xml'})
    with urllib.request.urlopen(req,timeout=30) as response:
        if response.geturl()!=url:raise ValueError('Unexpected URL redirect')
        raw=response.read(250_001)
        if len(raw)>250_000:raise ValueError('Oversized upstream SVG')
        try:return raw.decode('utf-8-sig')
        except UnicodeDecodeError as exc:raise ValueError('Upstream did not return UTF-8 SVG') from exc


def refresh(repo:Path):
    # Download + validate both BEFORE touching any target.
    ready={}
    for card,rel in CARDS.items():
        data,mapping=recolor(fetch_card(card),'Top Languages by Repo' if card=='repos-per-language' else 'Top Languages by Commit')
        ready[rel]=data
        print('PASS:',card,', '.join(f'{k}={v}' for k,v in mapping.items()))
    for rel,data in ready.items():
        target=repo/rel
        target.parent.mkdir(parents=True,exist_ok=True)
        if target.exists() and target.read_bytes()==data:continue
        tmp=target.with_suffix(target.suffix+'.tmp')
        if tmp.exists():raise ValueError('Refusing to overwrite existing temporary file '+str(tmp))
        with tmp.open('xb') as f:f.write(data)
        tmp.replace(target)
        print('UPDATED:',rel)
    print('PASS: Two live statistics cards refreshed with consistent language colors')


def main():
    p=argparse.ArgumentParser()
    p.add_argument('--repo',type=Path,default=Path('.'))
    args=p.parse_args()
    root=args.repo.expanduser().resolve()
    if not root.is_dir() or not (root/'README.md').is_file():raise ValueError('Not a README repository root')
    refresh(root)

if __name__=='__main__':
    try:main()
    except (ValueError,ET.ParseError,urllib.error.URLError,OSError) as e:
        print('STOP:',e,file=sys.stderr)
        sys.exit(1)
