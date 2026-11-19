from pathlib import Path
from io import BytesIO
from zipfile import ZipFile
import re,sys
import pandas as pd
def july(df,t,p,cap,source,station):
    time=pd.to_datetime(df[t],errors='coerce')
    power=pd.to_numeric(df[p],errors='coerce')
    a=pd.DataFrame({'time':time,'mw':power}).dropna().drop_duplicates('time')
    a=a[(a.time.dt.month==7)&(a.mw>=0)&(a.mw<=cap)]
    for year,g in a.groupby(a.time.dt.year):
        yield [source,station,year,cap,len(g),round(g.mw.mean()/cap,6)]
def extract(folder):
    out=[]
    for zpath in Path(folder).glob('*.zip'):
        if 'PVOD' not in zpath.name and '国家电网' not in zpath.name:continue
        with ZipFile(zpath) as z:
            files=[x for x in z.namelist() if '__MACOSX' not in x]
            if 'PVOD' in zpath.name:
                meta=pd.read_csv(z.open(next(x for x in files if x.endswith('/metadata.csv')))).set_index('Station_ID')
                for f in files:
                    name=Path(f).stem
                    if not re.fullmatch(r'station\d+',name):continue
                    df=pd.read_csv(z.open(f))
                    out.extend(july(df,'date_time','power',meta.at[name,'Capacity']/1000,'PVOD',name))
            else:
                for f in files:
                    m=re.search(r'data_original/solar_stations/Solar station site (\d+) \(Nominal capacity-(\d+)MW\)',f)
                    if not m or not f.endswith('.xlsx'):continue
                    df=pd.read_excel(BytesIO(z.read(f)),usecols=[0,7])
                    out.extend(july(df,*df.columns,int(m[2]),'SGCC',m[1]))
    return pd.DataFrame(out,columns=['dataset','station','year','rated_MW','valid_july_samples','mean_power_over_rating']).sort_values(['dataset','station','year'])
if __name__=='__main__':extract(sys.argv[1]).to_csv(sys.stdout,index=False)
