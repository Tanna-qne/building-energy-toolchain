import fs from 'node:fs';
import {fileURLToPath} from 'node:url';
process.chdir(fileURLToPath(new URL('.',import.meta.url)));
function model(epw,reference,july) {
  const ref=reference.trim().split(/\r?\n/).slice(1).map(s=>Number(s.split(',')[1]));
  const loads=july.trim().split(/\r?\n/).slice(1).map(s=>s.split(','));
  const lines=epw.trim().split(/\r?\n/).slice(8);
  if(lines.length!==8760||loads.length!==744||ref.length!==24) throw Error('Input lengths');
  const rad=Math.PI/180,tilt=15*rad,lat=31*rad;
  const mean=Array(24).fill(0),annual=['interval_start_CST,ac_kWh_per_kWp'];
  const obs=[];
  for(const [i,line] of lines.entries()){
    const f=line.split(',').map(Number),d=new Date(Date.UTC(2025,0,1,i)),h=d.getUTCHours();
    if(f[0]!==2025||f[1]!==d.getUTCMonth()+1||f[2]!==d.getUTCDate()||f[3]!==h+1)throw Error('EPW timestamp '+i);
    const [ghi,dni,dhi]=f.slice(13,16),temp=f[6];
    if(![ghi,dni,dhi].every(v=>Number.isFinite(v)&&v>=0&&v<9999)||!Number.isFinite(temp)||temp>=99)throw Error('EPW data '+i);
    const g=2*Math.PI/365*(Math.floor(i/24)+(h+0.5-12)/24);
    const dec=0.006918-0.399912*Math.cos(g)+0.070257*Math.sin(g)-0.006758*Math.cos(2*g)+0.000907*Math.sin(2*g)-0.002697*Math.cos(3*g)+0.00148*Math.sin(3*g);
    const eot=229.18*(0.000075+0.001868*Math.cos(g)-0.032077*Math.sin(g)-0.014615*Math.cos(2*g)-0.040849*Math.sin(2*g));
    const ha=(15*(h+0.5-12)+eot/4)*rad; // longitude 120 E, UTC+8
    const cosInc=Math.sin(lat-tilt)*Math.sin(dec)+Math.cos(lat-tilt)*Math.cos(dec)*Math.cos(ha);
    const poa=dni*Math.max(0,cosInc)+dhi*(1+Math.cos(tilt))/2+0.2*ghi*(1-Math.cos(tilt))/2;
    const cell=temp+(49-20)*poa/800;
    const dc=poa/1000*Math.max(0,1-0.0047*(cell-25))*0.86;
    const ac=Math.min(1/1.1,dc*0.96);
    const time=d.toISOString().slice(0,19).replace('T',' ');
    mean[h]+=ac/365;obs.push({time,ac});annual.push([time,ac.toFixed(9)].join(','));
  }
  const sum=a=>a.reduce((x,y)=>x+y,0);
  const capacity=sum(ref)/sum(mean); // energy-equivalent DC capacity
  const errors=mean.map((v,h)=>capacity*v-ref[h]);
  const j=['interval_start_CST,load_kWh,ac_kWh_per_kWp,physical_base_kWh,reference_base_kWh,reference_kWh_per_equivalent_kWp'];
  for(const r of obs.filter(r=>r.time.startsWith('2025-07'))){
    const row=loads[j.length-1];if(row[0]!==r.time)throw Error('July time');
    j.push([r.time,row[1],r.ac.toFixed(9),(r.ac*capacity).toFixed(6),row[2],(Number(row[2])/capacity).toFixed(9)].join(','));
  }
  const metrics={dc_equivalent_kWp:capacity,ac_rating_kW:capacity/1.1,annual_yield_kWh_per_kWp:365*sum(mean),july_yield_kWh_per_kWp:sum(obs.filter(r=>r.time.startsWith('2025-07')).map(r=>r.ac)),RMSE_kW:Math.sqrt(sum(errors.map(v=>v*v))/24),MAE_kW:sum(errors.map(Math.abs))/24,max_abs_kW:Math.max(...errors.map(Math.abs)),worst_hour:errors.map(Math.abs).indexOf(Math.max(...errors.map(Math.abs)))};
  return {'annual_unit.csv':annual.join('\n')+'\n','july_unit.csv':j.join('\n')+'\n','typical_day.csv':['hour_start,reference_kW,physical_kW,signed_error_kW',...ref.map((v,h)=>[h,v,(mean[h]*capacity).toFixed(6),errors[h].toFixed(6)].join(','))].join('\n')+'\n','summary.json':JSON.stringify(metrics,null,2)+'\n'};
}
const read=p=>fs.readFileSync(p,'utf8');
const result=model(read('../../weather/CHN_Changxing_ERA5_31.00N_120.00E_2025.epw'),read('../reference_24h.csv'),read('../july_load_pv.csv'));
for(const [name,data] of Object.entries(result)) fs.writeFileSync(name,data);
