"""Reconcile source tables without repairing their discrepancies."""
import json,pathlib,re,collections,csv
ROOT=pathlib.Path(__file__).resolve().parent

def save(n,v):(ROOT/n).write_text(json.dumps(v,ensure_ascii=False,indent=2))
def operation(s):
 s=s.lower()
 if 'перевоз' in s:return 'haul'
 if 'погруз' in s:return 'load'
 if 'противоголол' in s or 'фрикцион' in s:return 'deice'
 if 'вал' in s:return 'snowbank'
 if 'снег' in s and ('очист' in s or 'расчист' in s):return 'snow_clear'
 if 'снег' in s:return 'snow_other'
 if 'ремонт' in s or 'устро' in s or 'замен' in s:return 'repair'
 return 'other_maintenance'
def main():
 works=json.loads((ROOT/'works.json').read_text());book=json.loads((ROOT/'extracted/estimate-cells.json').read_text());est=json.loads((ROOT/'estimate-lines.json').read_text())
 est=[r for r in est if isinstance(r['cells'].get('C'),str) and isinstance(r['unit'],str)]
 for r in est:
  r['record_kind']='embedded_resource_code_not_unit' if r['unit'] in {'121-342','411-0001'} else 'estimate_calculation_line';r['operation_category']=operation(r['cells']['C']);r['work_quantity']=r['cells'].get('G') if r['sheet'].endswith('год') else r['quantity']
  r['unit_dimensions']='area' if 'м2' in r['unit'].replace(' ','') else 'volume' if 'м3' in r['unit'].replace(' ','') else 'length' if 'км' in r['unit'] or 'м'==r['unit'].strip() else 'mass' if r['unit'].strip()=='т' else 'count_or_other'
 prices=[];diffs=[];offset=0;year_conflicts=[]
 for year in range(2026,2030):
  sheet='ПСК 26 г' if year==2026 else 'ПСК 27-29 гг';col={2026:'F',2027:'F',2028:'G',2029:'H'}[year]
  rows=[dict(sheet=sheet,row=r['row'],cells=r['cells'],unit=r['cells']['D'],quantity=r['cells'].get('E')) for r in book[sheet] if isinstance(r['cells'].get('C'),str) and isinstance(r['cells'].get('D'),str) and isinstance(r['cells'].get(col),(int,float)) and r['row']>6];subset=works[offset:offset+len(rows)]
  for w,r in zip(subset,rows):
   head=w['description'].split('\n')[0]
   bounds=[85,142,179,222] if year==2026 else [169,264,327,394]
   scope=sum(r['row']>=b for b in bounds)
   w['applicable_object_kinds']=[['main_road','sidewalk','bus_stop','fence','lighting'],['intraquarter'],['driveway'],['individual_housing'],['intraquarter','driveway','individual_housing']][scope]
   w['scope_link_status']='document_work_family_not_customer_order'
   w['year_by_estimate_sequence']=year;w['year_text']=re.search(r'20\d\d',w['description']).group();w['operation_category']=operation(w['description']);w['estimate_reference']=dict(sheet=sheet,row=r['row'],price_cell=col+str(r['row']),unit=r['unit'],calculation_quantity=r['quantity'])
   if int(w['year_text'])!=year:year_conflicts.append(dict(work_id=w['id'],year_text=w['year_text'],sequence_year=year,source=w['source']))
   d=round(w['unit_price_rub']-r['cells'][col],2)
   if d:diffs.append(dict(work_id=w['id'],source=w['source'],description=w['description'],docx_price=w['unit_price_rub'],xlsx_price=r['cells'][col],difference_rub=d,sheet=sheet,cell=col+str(r['row'])))
  prices.append(dict(year=year,rows=len(rows),docx_sum_rub=round(sum(w['unit_price_rub'] for w in subset),2),xlsx_sum_rub=round(sum(r['cells'][col] for r in rows),2)));offset+=len(rows)
 assert offset==len(works)
 save('works.json',works);save('estimate-lines.json',est)
 save('estimate-analysis.json',dict(by_year=prices,price_differences=diffs,year_text_conflicts=year_conflicts,units=dict(collections.Counter(r['unit'] for r in est)),operations=dict(collections.Counter(w['operation_category'] for w in works)),ordered_volume_known=False,cycles_interpretation='unit_rate_calculation_not_frequency_commitment',signature_status='detached_sig_preserved_cryptographically_not_verified'))
 with (ROOT/'objects.csv').open('w') as f:
  rows=json.loads((ROOT/'objects.json').read_text());writer=csv.DictWriter(f,fieldnames=['id','kind','number','description','address_context','subtype','quantity','quantity_unit','geometry_status','service_boundary_confirmed'],extrasaction='ignore');writer.writeheader();writer.writerows(rows)
 print(prices,'price discrepancies',len(diffs),'year conflicts',len(year_conflicts))
if __name__=='__main__':main()
