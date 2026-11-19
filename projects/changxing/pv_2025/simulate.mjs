import fs from "node:fs";
import {fileURLToPath} from "node:url";
process.chdir(fileURLToPath(new URL(".",import.meta.url)));
function calculate(epw, reference, julyLoad) {
  const lines = epw.trim().split(/\r?\n/).slice(8);
  if (lines.length !== 8760 || reference.length !== 24) throw Error("Unexpected input length");
  const hours = lines.map((line, i) => {
    const fields = line.split(",");
    const start = new Date(Date.UTC(2025, 0, 1, i));
    const hour = start.getUTCHours();
    const ghi = Number(fields[13]);
    if (+fields[0] !== 2025 || +fields[1] !== start.getUTCMonth()+1 || +fields[2] !== start.getUTCDate() || +fields[3] !== hour+1 || !(ghi >= 0 && ghi < 9999)) throw Error("Invalid weather row "+i);
    return {time:start.toISOString().slice(0,19).replace("T"," "), hour, ghi};
  });
  const means = Array(24).fill(0);
  for (const r of hours) means[r.hour] += r.ghi / 365;
  const annual = ["interval_start_CST,GHI_Wh_m2,pv_kWh"];
  const july = ["interval_start_CST,load_kWh,pv_kWh,load_minus_pv_kWh"];
  const simulated = Array(24).fill(0);
  const loads = julyLoad.trim().split(/\r?\n/);
  const index = loads.shift().split(",").indexOf("total_kWh");
  if (index < 0 || loads.length !== 744) throw Error("Invalid July load file");
  for (const r of hours) {
    if (reference[r.hour] && !means[r.hour]) throw Error("Missing daylight");
    const pv = reference[r.hour] ? reference[r.hour]*r.ghi/means[r.hour] : 0;
    simulated[r.hour] += pv / 365;
    annual.push([r.time,r.ghi,pv.toFixed(6)].join(","));
    if (r.time.startsWith("2025-07")) {
      const row = loads[july.length-1].split(",");
      if (r.time !== row[0]) throw Error("Unaligned timestamps");
      const load = Number(row[index]);
      july.push([r.time,load.toFixed(6),pv.toFixed(6),(load-pv).toFixed(6)].join(","));
    }
  }
  const calibration = ["hour_start,reference_kW,annual_mean_kW,error_kW",...reference.map((v,h)=>[h,v,simulated[h].toFixed(6),(simulated[h]-v).toFixed(6)].join(","))];
  return {"pv_hourly_2025.csv":annual.join("\n")+"\n","july_load_pv.csv":july.join("\n")+"\n","calibration_24h.csv":calibration.join("\n")+"\n"};
}
const read=p=>fs.readFileSync(p,"utf8");
const ref=read("reference_24h.csv").trim().split(/\r?\n/).slice(1).map(s=>Number(s.split(",")[1]));
const outputs=calculate(read("../weather/CHN_Changxing_ERA5_31.00N_120.00E_2025.epw"),ref,read("../charging_2025/total_hourly_2025_07.csv"));
for(const [name,data] of Object.entries(outputs)) fs.writeFileSync(name,data);
