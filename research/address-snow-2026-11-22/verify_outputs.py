"""Check comparable masks and physical controls; export the known photo cell."""
import csv
import json
from pathlib import Path
import numpy as np

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
with (HERE/'samples.csv').open() as f:
    samples=list(csv.DictReader(f))
model_keys={(r['case_id'],r['time'],r['row'],r['col']) for r in samples if r['variant']=='model'}
# Enforce a common ground/building mask for every control, including no-buildings.
samples=[r for r in samples if (r['case_id'],r['time'],r['row'],r['col']) in model_keys]
with (HERE/'samples.csv').open('w',newline='') as f:
    w=csv.DictWriter(f,fieldnames=list(samples[0]));w.writeheader();w.writerows(samples)
names={'SNOW-001':'Бохняка, 3','SNOW-002':'50 лет Октября, 4/2','SNOW-003':'Рыбаков, 13/2'}
summary=[]
for case,time,variant in sorted({(r['case_id'],r['time'],r['variant']) for r in samples}):
    for radius in [5,10,20,40]:
        rows=[r for r in samples if (r['case_id'],r['time'],r['variant'])==(case,time,variant)
              and float(r['distance_to_building_m'])<=radius]
        if not rows: continue
        high=max(rows,key=lambda r:float(r['snow_cm']))
        values=[float(r['snow_cm']) for r in rows]
        summary.append(dict(case_id=case,address=names[case],time=time,variant=variant,radius_m=radius,
            cells=len(rows),min_cm=min(values),median_cm=float(np.median(values)),max_cm=max(values),
            max_lon=high['lon'],max_lat=high['lat']))
with (HERE/'comparison.csv').open('w',newline='') as f:
    w=csv.DictWriter(f,fieldnames=list(summary[0]));w.writeheader();w.writerows(summary)
indexed={(r['case_id'],r['time'],r['row'],r['col'],r['variant']):float(r['snow_cm']) for r in samples}
control_differences={}
for variant in ['calm','no_buildings','archive']:
    differences=[]
    for (case,time,row,col,name),value in indexed.items():
        if name!=variant: continue
        reference=indexed[(case,time,row,col,'model' if variant=='archive' else 'background')]
        differences.append(abs(value-reference))
    control_differences[variant]=max(differences) if differences else None
    if variant!='archive': assert max(differences)<.0011
run=json.loads((HERE/'run.json').read_text())
assert run['weather_extended']
assert all(len(t['hours'])==192 for t in run['tiles'])
assert all(t['archive_input_max_abs_difference']==0 for t in run['tiles'])
assert len({r['case_id'] for r in summary})==3
assert all(np.isfinite(float(r['snow_cm'])) and float(r['snow_cm'])>=0 for r in samples)
focused=json.loads((ROOT/'research/visual-snow-2026-11-21/focused/samples.json').read_text())
photo=[]
for time,zones in focused['cases']['house-42']['frames'].items():
    for cell in zones['Торец 0–25 м']:
        row,col=str(cell['row']),str(cell['col'])
        key=('SNOW-002',time,row,col,'model')
        if key in indexed:
            photo.append(dict(time=time,row=row,col=col,model_cm=indexed[key],archive_cm=cell['danil_cm'],
                              background_cm=indexed[(*key[:-1],'background')]))
result={'status':'passed','common_mask_samples':len(samples),'summary_rows':len(summary),
        'max_abs_difference_cm':control_differences,'photo_sector':photo}
(HERE/'verification.json').write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n')
print(json.dumps(result,ensure_ascii=False,indent=2))
for r in summary:
    if r['radius_m']==20 and r['variant'] in ['model','background','reverse']:
        print(r['case_id'],r['time'],r['variant'],r['cells'],round(r['min_cm'],1),round(r['median_cm'],1),round(r['max_cm'],1))
