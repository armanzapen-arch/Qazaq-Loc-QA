#!/usr/bin/env python3
"""Conservative ARB localization QA for Russian -> Kazakh/Uzbek."""
from __future__ import annotations
import argparse, csv, html, json, re, sys
from collections import Counter
from pathlib import Path
from typing import Any

TOKEN = re.compile(r"[\w]+(?:[-’'][\w]+)*", re.UNICODE)
KAZ = set('әғқңөұүһіӘҒҚҢӨҰҮҺІ')
RUS_ONLY = set('ёъэЁЪЭ')
UZ_MARKERS = ('ў', 'ҳ')
# High-confidence common Kazakh spelling errors caused by Russian-letter lookalikes.
ORTHOGRAPHY_PAIRS = [('адеби','әдеби'),('арекет','әрекет'),('аким','әкім'),('жаналык','жаңалық'),('казак','қазақ'),('кызмет','қызмет'),('озгерту','өзгерту'),('суйемін','сүйемін'),('кундилик','күнделікті')]
ISSUE_ORDER = {'untranslated':1,'same_as_source':2,'placeholder_mismatch':3,'plural_mismatch':4,'glossary_violation':5,'length_overflow':6,'machine_translation_suspect':7,'inconsistent_term':8,'suffix_harmony':9,'orthography':10,'wrong_language':11}


def load_json(path: Path) -> dict[str, Any]:
    try:
        obj=json.loads(path.read_text(encoding='utf-8-sig'))
    except Exception as e: raise ValueError(f'{path}: JSON/ARB оқу қатесі: {e}')
    if not isinstance(obj,dict): raise ValueError(f'{path}: ARB түбірі объект болуы керек')
    return obj

def values(arb):
    return {k:v for k,v in arb.items() if not k.startswith('@') and not k.startswith('@@') and isinstance(v,str)}

def norm(s): return re.sub(r'\s+',' ',s).strip().casefold()
def placeholders(s):
    """Collect ICU arguments without mistaking plural branch braces for variables."""
    found = Counter()
    ident = re.compile(r"[A-Za-z_][\w.]*")

    def parse_message(i, stop_at_close=False):
        while i < len(s):
            if s[i] == '}':
                if stop_at_close:
                    return i + 1
                i += 1
            elif s[i] == '{':
                i = parse_argument(i)
            else:
                i += 1
        return i

    def parse_argument(start):
        i = start + 1
        while i < len(s) and s[i].isspace(): i += 1
        m = ident.match(s, i)
        if not m:
            return parse_message(start + 1, True)
        name = m.group(0); found[name] += 1; i = m.end()
        while i < len(s) and s[i].isspace(): i += 1
        if i >= len(s): return i
        if s[i] == '}': return i + 1
        if s[i] != ',': return parse_message(i, True)
        i += 1
        while i < len(s) and s[i].isspace(): i += 1
        fm = ident.match(s, i)
        if not fm: return parse_message(i, True)
        fmt = fm.group(0).casefold(); i = fm.end()
        if fmt in ('plural', 'selectordinal', 'select'):
            while i < len(s):
                while i < len(s) and (s[i].isspace() or s[i] == ','): i += 1
                if i >= len(s): return i
                if s[i] == '}': return i + 1
                # Selector, e.g. one, other, male, or =0; then parse its message body.
                while i < len(s) and not s[i].isspace() and s[i] not in '{}': i += 1
                while i < len(s) and s[i].isspace(): i += 1
                if i < len(s) and s[i] == '{':
                    i = parse_message(i + 1, True)
                elif i < len(s) and s[i] == '}':
                    return i + 1
                elif i < len(s):
                    i += 1
            return i
        # Non-select formatter, e.g. number/date/time: skip its body to the matching brace.
        depth = 1
        while i < len(s) and depth:
            if s[i] == '{': depth += 1
            elif s[i] == '}': depth -= 1
            i += 1
        return i

    parse_message(0)
    return found
def add(rows,key,kind,severity,src,tgt,evidence,suggestion='',confidence=.8):
    rows.append({'key':key,'issue_type':kind,'severity':severity,'source':src,'target':tgt,'evidence':evidence,'suggestion':suggestion,'confidence':confidence,'reviewed_by_human':False})

def scan(source,target,glossary):
    rows=[]; sv=values(source); tv=values(target)
    for key,src in sv.items():
        tgt=tv.get(key)
        if tgt is None or not tgt.strip():
            evidence='Target key is missing.' if tgt is None else 'Target string is empty.'
            add(rows,key,'untranslated','high',src,tgt or '',evidence); continue
        if norm(src)==norm(tgt) and re.search(r'\w',src):
            add(rows,key,'same_as_source','medium',src,tgt,'Target matches the Russian source exactly; brand/name/number exceptions require review.','',.75)
        if placeholders(src)!=placeholders(tgt):
            add(rows,key,'placeholder_mismatch','high',src,tgt,f'Placeholder counts differ: source={dict(placeholders(src))}, target={dict(placeholders(tgt))}.','',.99)
        # Basic ICU structure check: balanced braces and plural/select category presence.
        for label,text in [('source',src),('target',tgt)]:
            if text.count('{')!=text.count('}'):
                add(rows,key,'plural_mismatch','high',src,tgt,f'Unbalanced ICU braces in {label}.','',.95)
                break
        if re.search(r'\{\s*[\w.]+\s*,\s*(plural|selectordinal)\s*,',src):
            if not re.search(r'\{\s*[\w.]+\s*,\s*(plural|selectordinal)\s*,',tgt):
                add(rows,key,'plural_mismatch','high',src,tgt,'ICU plural/selectordinal form exists in source but not target.','',.92)
        low=tgt.casefold()
        for term in glossary:
            ru=term['ru'].strip(); kk=term['kk'].strip()
            if ru and kk and ru.casefold() in src.casefold() and ru.casefold() in low and ru.casefold()!=kk.casefold():
                add(rows,key,'glossary_violation','medium',src,tgt,f"Term '{ru}' remains untranslated; glossary preference is '{kk}'.",kk,.85)
        meta=source.get('@'+key,{})
        desc=(meta.get('description','') if isinstance(meta,dict) else '').casefold()
        short_hint=any(x in (key+' '+desc).casefold() for x in ('button','tab','title','label','btn','кноп','вклад','заголов'))
        if short_hint and len(tgt)>max(len(src)*1.65, len(src)+8):
            add(rows,key,'length_overflow','low',src,tgt,f'Target has {len(tgt)} chars vs {len(src)} in source; key/description suggests a compact UI element.','',.65)
        # Static suffix after a runtime name may mismatch Kazakh vowel harmony.
        if re.search(r'\{\s*[^{}]+\s*\}\s*[-–]?(?:ға|ге|қа|ке|да|де|та|те|дан|ден|тан|тен)\b',low):
            add(rows,key,'suffix_harmony','medium',src,tgt,'A fixed Kazakh case suffix follows a runtime placeholder; vowel harmony may be wrong.','Rephrase to avoid attaching a suffix to the placeholder.',.78)
        has_target_mark=any(c in KAZ for c in tgt)
        has_russian=any(c in RUS_ONLY for c in tgt)
        if has_russian and not has_target_mark and re.search(r'[А-Яа-я]{4}',tgt):
            add(rows,key,'wrong_language','low',src,tgt,'Cyrillic text contains Russian-specific letters and no Kazakh-specific letters; human language review required.','',.55)
        elif any(c in tgt.casefold() for c in ('ў','ҳ')):
            add(rows,key,'wrong_language','medium',src,tgt,'Uzbek-specific letters detected in the Kazakh target.','',.8)
        # Avoid making an automated machine-translation claim; conservative suspicious phrase checks only.
        if re.search(r'\b(является|осуществлять|данный|который)\b',tgt.casefold()):
            add(rows,key,'machine_translation_suspect','low',src,tgt,'Russian function-word pattern remains in target text; review manually.','',.5)
        for wrong,right in ORTHOGRAPHY_PAIRS:
            if wrong in low:
                add(rows,key,'orthography','medium',src,tgt,f"Possible Kazakh orthography error: '{wrong}' may need to be '{right}'. Verify in context.",tgt.replace(wrong,right),.72)
                break
    # Term consistency needs phrase alignment; avoid noisy whole-string comparisons.
    rows.sort(key=lambda x:(ISSUE_ORDER.get(x['issue_type'],99),x['key']))
    return rows,sv,tv

def write_html(rows,path):
    cols=['key','issue_type','severity','source','target','evidence','suggestion','confidence','reviewed_by_human']
    trs=[]
    for r in rows:
        cells=''.join('<td>'+html.escape(str(r[c]))+'</td>' for c in cols)
        trs.append('<tr>'+cells+'</tr>')
    doc='''<!doctype html><html lang="kk"><meta charset="utf-8"><title>LocQA есебі</title><style>body{font:14px Arial,sans-serif;margin:2rem;color:#203047}h1{color:#14324f}table{border-collapse:collapse;width:100%;font-size:12px}th,td{border:1px solid #ccd5df;padding:7px;vertical-align:top}th{background:#14324f;color:white;position:sticky;top:0}tr:nth-child(even){background:#eef3f7}.high{color:#a21d24;font-weight:bold}</style><h1>Qazaq Loc QA — табылған мәселелер</h1><p>Есеп автоматты эвристикамен жасалған. Әр табылымды адам тексеруі керек.</p><table><thead><tr>'''+''.join('<th>'+c+'</th>' for c in cols)+'''</tr></thead><tbody>'''+''.join(trs)+'''</tbody></table></html>'''
    path.write_text(doc,encoding='utf-8')

def main():
    ap=argparse.ArgumentParser(description='ARB localization QA'); sub=ap.add_subparsers(dest='cmd',required=True)
    c=sub.add_parser('check'); c.add_argument('--source',required=True); c.add_argument('--target',required=True); c.add_argument('--glossary',required=True); c.add_argument('--out',required=True); c.add_argument('--html',default='report.html'); c.add_argument('--fixed',default='')
    a=ap.parse_args()
    try:
        src=load_json(Path(a.source)); tgt=load_json(Path(a.target))
        with open(a.glossary,encoding='utf-8-sig',newline='') as f: glossary=list(csv.DictReader(f))
        rows,sv,tv=scan(src,tgt,glossary)
        Path(a.out).write_text(json.dumps(rows,ensure_ascii=False,indent=2),encoding='utf-8')
        write_html(rows,Path(a.html))
        if a.fixed:
            fixed=dict(tgt)
            # Make human review state explicit for every existing translated string, without changing translation text.
            for key,val in values(tgt).items():
                meta=fixed.get('@'+key,{})
                if not isinstance(meta,dict): meta={}
                meta=dict(meta); meta['x-reviewed-by-human']=False
                fixed['@'+key]=meta
            Path(a.fixed).write_text(json.dumps(fixed,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
        counts=Counter(r['issue_type'] for r in rows)
        print(f'Checked {len(sv)} source and {len(tv)} target strings; {len(rows)} findings.')
        print(json.dumps(counts,ensure_ascii=False))
        return 0
    except Exception as e:
        print(str(e),file=sys.stderr); return 2
if __name__=='__main__': raise SystemExit(main())
