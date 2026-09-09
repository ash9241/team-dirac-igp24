"""Render the essay's data figures. Requires matplotlib; inputs ship with the site."""
from pathlib import Path
import json,csv,datetime
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import matplotlib.dates as mdates
from matplotlib.ticker import FuncFormatter,MaxNLocator
SITE=Path(__file__).resolve().parent.parent
OUT=SITE/'figures'
OUT.mkdir(exist_ok=True)
BG='#f0f1f3'; BLUE='#244bff'; INK='#222831'; GRAY='#656b76'
plt.rcParams.update({'font.family':'DejaVu Sans','font.size':15,'figure.facecolor':BG,'axes.facecolor':BG,'text.color':INK,'axes.labelcolor':GRAY,'xtick.color':GRAY,'ytick.color':GRAY,'axes.spines.top':False,'axes.spines.right':False,'axes.spines.left':False,'axes.spines.bottom':False,'svg.fonttype':'path'})
def save(fig,name):
 for ext in ['svg','png']:fig.savefig(OUT/(name+'.'+ext),dpi=160,facecolor=BG)
 plt.close(fig)
d=json.loads((SITE/'evidence/diversity.json').read_text())
a=d['earlier']; b=d['pilot']
for mobile in [False,True]:
 fig=plt.figure(figsize=(4.8,5.7) if mobile else (11,4.6))
 ax=fig.add_axes([.04,.1,.92,.79] if mobile else [.02,.13,.96,.76])
 labels=['Earlier portfolio · 1,000 rows','GQ-96 first stage · 48 rows']
 values=[100*a['top_three_rows']/a['rows'],100*b['top_three_rows']/b['verified_rows']]
 for y,value,label,color in zip([1.2,0],values,labels,[INK,BLUE]):
  ax.barh(y,100,height=.31,color='#d9dce2')
  ax.barh(y,value,height=.31,color=color)
  ax.text(0,y+.31,label,fontsize=14 if mobile else 17,va='bottom',color=color)
  ax.text(value-2 if value>70 else value+2,y,f'{value:g}%',ha='right' if value>70 else 'left',va='center',fontsize=21 if mobile else 24,color='white' if value>70 else color,weight='bold')
 ax.set_xlim(0,100);ax.set_ylim(-.48,1.85);ax.set_yticks([])
 ax.set_xticks([0,25,50,75,100],[f'{n}%' for n in [0,25,50,75,100]])
 ax.tick_params(length=0,pad=12,labelsize=12 if mobile else 14)
 save(fig,'diversity'+('-mobile' if mobile else ''))
rows=list(csv.DictReader((SITE/'evidence/checkpoints.csv').open()))
dates=[datetime.datetime.fromisoformat(r['date']) for r in rows]
for mobile in [False,True]:
 fig,axes=plt.subplots(2 if mobile else 1,1 if mobile else 2,figsize=(4.8,8.7) if mobile else (11,4.6))
 fig.subplots_adjust(left=.15 if mobile else .075,right=.90 if mobile else .97,top=.88 if mobile else .78,bottom=.07 if mobile else .15,hspace=.66,wspace=.32)
 for ax,key,title,color in zip(axes,['scoreable_pairs','score'],['Scoreable pairs','Official score'],[BLUE,INK]):
  values=[float(r[key]) for r in rows]
  ax.plot(dates,values,color=color,lw=2.3,marker='o',markersize=5)
  ax.grid(axis='y',color='#d2d6de',lw=.8);ax.set_axisbelow(True)
  ax.set_title(title,loc='left',fontsize=17 if mobile else 19,pad=27,color=color)
  ax.set_ylim(0,max(values)*1.24);ax.set_xlim(dates[0]-datetime.timedelta(days=3),dates[-1]+datetime.timedelta(days=3))
  ax.set_xticks([dates[0],datetime.datetime(2026,8,1),dates[-1]],['Jul 4','Aug 1','Sep 1'])
  ax.yaxis.set_major_locator(MaxNLocator(4))
  ax.yaxis.set_major_formatter(FuncFormatter(lambda v,p,metric=key: f'{v/1000:g}k' if metric=='scoreable_pairs' and v else f'{v:,.0f}'))
  ax.tick_params(length=0,pad=10,labelsize=12 if mobile else 13)
  ax.annotate(f'{values[-1]:,.0f}' if key=='scoreable_pairs' else f'{values[-1]:,.3f}',(dates[-1],values[-1]),xytext=(-2,-25 if key=='score' else 14),textcoords='offset points',ha='right',fontsize=17,color=color,weight='bold')
  if key=='score':
   i=values.index(max(values));ax.annotate(f'{values[i]:,.3f}',(dates[i],values[i]),xytext=(6,16),textcoords='offset points',fontsize=13,color=GRAY)
 save(fig,'trajectory'+('-mobile' if mobile else ''))
print('Four responsive figures rendered as SVG and PNG.')
