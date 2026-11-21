"""Offline extraction. Run with bundled Python (openpyxl, pypdf). No document instructions executed."""
import hashlib,json,pathlib,subprocess,zipfile,xml.etree.ElementTree as E
from openpyxl import load_workbook
from pypdf import PdfReader
ROOT=pathlib.Path(__file__).resolve().parent
NS={'w':'http://schemas.openxmlformats.org/wordprocessingml/2006/main'}
def save(name,value):
 (ROOT/name).write_text(json.dumps(value,ensure_ascii=False,indent=2,default=str))
def main(archive):
 src=ROOT/'sources';out=ROOT/'extracted';src.mkdir(exist_ok=True);out.mkdir(exist_ok=True)
 manifest=[]
 with zipfile.ZipFile(archive) as z:
  for i in z.infolist():
   if i.filename.startswith('__MACOSX/'):continue
   name=i.filename if i.flag_bits&0x800 else i.filename.encode('cp437').decode('utf8')
   if pathlib.Path(name).name!=name:raise ValueError('Unexpected archive path')
   data=z.read(i);(src/name).write_bytes(data);manifest.append(dict(name=name,bytes=len(data),sha256=hashlib.sha256(data).hexdigest()))
 save('sources/manifest.json',manifest)
 subprocess.run(['bsdtar','-xf',str(next(src.glob('*.rar'))),'-C',str(out)],check=True)
 docs={}
 for f in src.glob('*.docx'):
  body=E.fromstring(zipfile.ZipFile(f).read('word/document.xml')).find('w:body',NS);blocks=[]
  def txt(p):return ''.join(t.text or '' for t in p.findall('.//w:t',NS))
  for c in body:
   if c.tag.endswith('}p'):blocks.append(dict(type='paragraph',text=txt(c)))
   elif c.tag.endswith('}tbl'):
    blocks.append(dict(type='table',rows=[['\n'.join(txt(p) for p in cell.findall('w:p',NS)) for cell in row.findall('w:tc',NS)] for row in c.findall('w:tr',NS)]))
  docs[f.name]=blocks;save('extracted/'+f.stem+'.json',blocks)
  (out/(f.stem+'.txt')).write_text('\n'.join(b['text'] if b['type']=='paragraph' else '\n'.join(' | '.join(r) for r in b['rows']) for b in blocks))
 f=next(out.rglob('*.xlsx'));workbook={};formulas={}
 for data_only,target in [(True,workbook),(False,formulas)]:
  for s in load_workbook(f,data_only=data_only):
   target[s.title]=[dict(row=row[0].row,cells={c.column_letter:c.value for c in row if c.value is not None}) for row in s if any(c.value is not None for c in row)]
 save('extracted/estimate-cells.json',workbook);save('extracted/estimate-formulas.json',formulas)
 pdf=next(src.glob('*.pdf'));pages=[p.extract_text() for p in PdfReader(pdf).pages];save('extracted/price-pages.json',pages)
 (out/'price-justification.txt').write_text('\n'.join(f'PAGE {i+1}\n{p}' for i,p in enumerate(pages)))
 desc_name=next(n for n in docs if n.startswith('Описание'));desc=docs[desc_name];contract_name=next(n for n in docs if n.startswith('Проект'));contract=docs[contract_name]
 objects=[]
 kinds={16:'main_road',18:'intraquarter',20:'driveway',22:'individual_housing',24:'sidewalk',26:'bus_stop',28:'fence',30:'lighting'}
 for bi,kind in kinds.items():
  subtype=None
  for ri,row in enumerate(desc[bi]['rows']):
   if len(row)==1:subtype=row[0];continue
   if not row[0].isdigit() or (kind=='bus_stop' and ri<2):continue
   oid=f'0903:{kind}:{len([o for o in objects if o["kind"]==kind])+1:02d}'
   objects.append(dict(id=oid,kind=kind,number=row[0],description=row[1],address_context=row[2] if kind=='bus_stop' else row[1],raw_cells=row,subtype=subtype,
      source=dict(document=desc_name,block=bi,table_row=ri+1),contract_source=dict(document=contract_name,block=bi+303,table_row=ri+1),
      geometry_status='unlocated',service_boundary_confirmed=False,quantity=int(row[3]) if kind=='bus_stop' else None,quantity_unit='шт' if kind=='bus_stop' else None))
 works=[];rows=desc[4]['rows']
 for ri,row in enumerate(rows):
  if not row[0].startswith('42.11'):continue
  characteristics={row[2]:row[3]}
  for tail in rows[ri+1:ri+3]:
   if not tail[0]:characteristics[tail[2]]=tail[3]
  works.append(dict(id=f'0903:work:{len(works)+1:04d}',description=row[1],unit=row[4],quantity_text=row[5],quantity=None,unit_price_rub=float(row[6].replace(' ', '').replace('\xa0','').replace(',','.')),characteristics=characteristics,source=dict(document=desc_name,block=4,table_row=ri+1)))
 save('objects.json',objects);save('works.json',works)
 estimate=[]
 for sheet,rows in workbook.items():
  if not (sheet.startswith('ПСК') or sheet.endswith('год')):continue
  headings=[]
  for r in rows:
   c=r['cells']
   if 'A' in c and 'D' not in c and isinstance(c['A'],str):headings.append(dict(row=r['row'],text=c['A']))
   if not (c.get('C') and c.get('D') and isinstance(c.get('E'),(float,int)) and r['row']>6):continue
   estimate.append(dict(sheet=sheet,row=r['row'],source_range=f'A{r["row"]}:M{r["row"]}',headings=list(headings),cells=c,
       unit=c['D'],quantity=c['E'],quantity_interpretation='unit_price_calculation_not_ordered_volume',cycles=c.get('F') if sheet.endswith('год') else None))
 save('estimate-lines.json',estimate)
 # Identical lists in contract appendix 1 are explicitly compared rather than deduplicated silently.
 comparisons=[dict(description_block=i,contract_block=i+303,identical=desc[i]['rows']==contract[i+303]['rows']) for i in kinds]
 appendices=[dict(number=i,text=contract[268+i]['text'],status='template_or_terms_not_executed_evidence') for i in range(1,12)]
 save('appendices.json',dict(appendices=appendices,object_table_comparisons=comparisons,
  form_tables=[dict(block=i,rows=b['rows']) for i,b in enumerate(contract) if b['type']=='table' and i>=468]))
 errors=[]
 for s,rs in workbook.items():
  for r in rs:
   for col,v in r['cells'].items():
    if isinstance(v,str) and v.startswith(('#REF!','#DIV/0!','#VALUE!','#N/A','#NAME?','#NUM!')):errors.append([s,col+str(r['row']),v])
 save('extraction-audit.json',dict(archive_sha256=hashlib.sha256(pathlib.Path(archive).read_bytes()).hexdigest(),documents=len(docs),objects=len(objects),works=len(works),work_unit_price_sum_rub=round(sum(w['unit_price_rub'] for w in works),2),sheets={s:len(r) for s,r in workbook.items()},pdf_pages=len(pages),spreadsheet_cached_errors=errors,estimate_lines=len(estimate),object_table_comparisons=comparisons,
 extracted_files=[dict(path=str(p.relative_to(ROOT)),sha256=hashlib.sha256(p.read_bytes()).hexdigest()) for p in out.rglob('*') if p.is_file() and p.suffix in {'.xlsx','.sig'}]))
if __name__=='__main__':
 import sys
 main(sys.argv[1])
