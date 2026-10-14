import json
import math
from functools import lru_cache
from pathlib import Path

ROOT=Path(__file__).parent
INDUSTRIES=[
 ('Plumbers','plumber',['plumber','plumbing contractor','plumbing service']),
 ('Towing companies','towing',['towing service','tow truck service','towing company']),
 ('Electricians','electrician',['electrician','electrical contractor','electrical service']),
 ('HVAC companies','hvac',['hvac contractor','heating and cooling contractor','air conditioning contractor']),
 ('Roofers','roofer',['roofing contractor','roofer','roofing company']),
 ('General contractors','general contractor',['general contractor','construction company','building contractor']),
 ('Remodelers','remodeler',['remodeler','remodeling contractor','home renovation contractor']),
 ('Handyman businesses','handyman',['handyman','handyman service','home repair service']),
 ('Stucco contractors','stucco',['stucco contractor','stucco repair','stucco company']),
 ('Plastering contractors','plastering',['plastering contractor','plaster contractor','plaster repair']),
 ('Landscapers','landscaper',['landscaper','landscaping company','landscaping contractor']),
 ('Cleaners','cleaner',['cleaning service','house cleaning service','commercial cleaning service'])
]
for label in ['Auto repair shops','Accountants','Appliance repair','Architects','Attorneys','Bakeries','Barbers','Beauty salons','Bookkeepers','Carpet cleaners','Chiropractors','Concrete contractors','Dentists','Dog groomers','Dry cleaners','Fence contractors','Florists','Flooring contractors','Garage door repair','Gyms','Home inspectors','Insurance agencies','Interior designers','Junk removal','Lawn care','Locksmiths','Massage therapists','Moving companies','Painters','Pest control','Pet boarding','Photographers','Pool contractors','Pressure washing','Property managers','Real estate agents','Restaurants','Septic services','Solar installers','Tree services','Veterinarians','Water damage restoration','Welders','Window installers','Window cleaners']:
    INDUST=label.lower();INDUSTRIES.append((label,INDUST,[INDUST,INDUST+' businesses']))

@lru_cache(maxsize=1)
def locations():
    return json.loads((ROOT/'datasets/us-locations.json').read_text(encoding='utf-8'))

@lru_cache(maxsize=1)
def location_index():return {item['id']:item for item in locations()}

def suggestions(kind,query):
    query=' '.join(query.lower().split())
    if kind=='location':
        items=locations()
        matches=[item for item in items if query in item['label'].lower() or (item['kind']=='state' and query in item['state'].lower())]
        matches.sort(key=lambda item:(not item['label'].lower().startswith(query),item['kind']!='state',item['label']))
        return matches[:10]
    values=[{'id':category,'label':label,'category':category,'wordings':wordings} for label,category,wordings in INDUSTRIES]
    return sorted([item for item in values if query in item['label'].lower() or query in item['category']],key=lambda item:(not item['label'].lower().startswith(query),item['label']))[:10]

def industry(value):
    key=' '.join(value.lower().split())
    for label,category,wordings in INDUSTRIES:
        if key in (label.lower(),category):return {'label':label,'category':category,'wordings':wordings}
    return {'label':value.strip(),'category':key,'wordings':[key,key+' businesses']}

def tile_key(wording,lat,lon):return f"{wording.lower()}|{lat:.5f}|{lon:.5f}"

def plan_tiles(location,wordings,completed=(),radius_km=20,limit=20):
    from scoring import settings
    step=settings()['scraper']['tile_degrees']
    lat,lon=location['lat'],location['lon']
    if location['kind']=='state':south,west,north,east=location['bounds']
    else:
        delta=radius_km/111.32;longitude_delta=delta/max(.15,math.cos(math.radians(lat)))
        south,north=lat-delta,lat+delta;west,east=lon-longitude_delta,lon+longitude_delta
    done=set(completed);candidates=[]
    if location['kind']=='state':
        ymin,ymax=math.floor(south/step),math.ceil(north/step)
        xmin,xmax=math.floor(west/step),math.ceil(east/step)
        cy=min(ymax,max(ymin,math.floor(lat/step)));cx=min(xmax,max(xmin,math.floor(lon/step)))
        extent=max(cy-ymin,ymax-cy,cx-xmin,xmax-cx)
        for radius in range(extent+1):
            perimeter=[(cy,cx)] if radius==0 else [(cy-radius,x) for x in range(cx-radius,cx+radius+1)]+[(cy+radius,x) for x in range(cx-radius,cx+radius+1)]+[(y,cx-radius) for y in range(cy-radius+1,cy+radius)]+[(y,cx+radius) for y in range(cy-radius+1,cy+radius)]
            for y,x in perimeter:
                if not(ymin<=y<=ymax and xmin<=x<=xmax):continue
                centre_lat=round((y+.5)*step,5);centre_lon=round(((x+.5)*step+180)%360-180,5)
                for wording in wordings:
                    key=tile_key(wording,centre_lat,centre_lon)
                    if key not in done:candidates.append({'key':key,'lat':centre_lat,'lon':centre_lon,'wording':wording})
                    if len(candidates)>=limit:
                        total=(ymax-ymin+1)*(xmax-xmin+1)*len(wordings)
                        return candidates,max(len(candidates),total-len(done))
        return candidates,len(candidates)
    # Fixed global lattice: overlapping city searches also reuse completed tiles.
    for y in range(math.floor(south/step),math.ceil(north/step)+1):
        for x in range(math.floor(west/step),math.ceil(east/step)+1):
            centre_lat=round((y+.5)*step,5);unwrapped_lon=(x+.5)*step;centre_lon=round((unwrapped_lon+180)%360-180,5)
            if location['kind']!='state':
                distance=math.hypot((centre_lat-lat)*111.32,(unwrapped_lon-lon)*111.32*math.cos(math.radians(lat)))
                if distance>radius_km+step*111.32/2:continue
            else:distance=math.hypot(centre_lat-lat,unwrapped_lon-lon)
            for wording in wordings:
                key=tile_key(wording,centre_lat,centre_lon)
                if key not in done:candidates.append({'key':key,'lat':centre_lat,'lon':centre_lon,'wording':wording,'distance':distance})
    candidates.sort(key=lambda item:(item['distance'],item['lat'],item['lon'],item['wording']))
    return candidates[:limit],len(candidates)
