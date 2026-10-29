"""Import old document fixtures without exposing retired registration APIs."""
import json
from ai_system.graph.registry import Result, Claim, find_refs


def value(registry,sid,name,value,run_id,unit='',description='',meaning=None):
    ref='R'+str(max((int(k[1:]) for k in registry.results if k.startswith('R')),default=0)+1)
    try:
        parsed=json.loads(str(value));kind='series' if isinstance(parsed,list) else 'number' if isinstance(parsed,(int,float)) else 'text'
    except ValueError:kind='text'
    row=Result(ref,kind,sid,name,str(value),run_id,unit,description,meaning=meaning)
    registry.results[ref]=row;registry.save()
    return row


def figure(registry,sid,run_id,path,caption):
    ref='F'+str(max((int(k[1:]) for k in registry.results if k.startswith('F')),default=0)+1)
    path=path if path.startswith('runs/') else f'runs/{run_id}/artifacts/{path}'
    row=Result(ref,'figure',sid,caption,'',run_id,path=path)
    registry.results[ref]=row;registry.save()
    return row


def claims(registry,sid,findings):
    rows=[]
    for finding in findings:
        ref='C'+str(len(registry.claims)+1)
        row=Claim(ref,sid,finding['statement'],finding.get('strength','倾向'),find_refs(finding['statement']))
        registry.claims[ref]=row;rows.append(row)
    registry.save()
    return rows
