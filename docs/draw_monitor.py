from pathlib import Path
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
from matplotlib.patches import FancyBboxPatch,FancyArrowPatch
plt.rcParams.update({'font.family':'DejaVu Sans','font.size':11,'pdf.fonttype':42,'svg.fonttype':'none'})
p=Path(__file__).parent
fig,ax=plt.subplots(figsize=(14,3.15));ax.set(xlim=(0,14),ylim=(0,3.15));ax.axis('off')
ink='#263445';blue='#e5edf7';green='#e5f1e8';red='#fbe4e2';gold='#fff0c9';brown='#946500'
def txt(x,y,s,fs=11,ha='center',bold=False,c=ink):ax.text(x,y,s,ha=ha,va='center',fontsize=fs,fontweight='bold' if bold else 'normal',color=c,linespacing=1.2)
def box(x,y,w,h,s,fc='white',ec=ink,fs=11):
 ax.add_patch(FancyBboxPatch((x,y),w,h,boxstyle='round,pad=.02,rounding_size=.05',fc=fc,ec=ec,lw=1));txt(x+w/2,y+h/2,s,fs)
def arr(a,b,c=ink,dash=False):ax.add_patch(FancyArrowPatch(a,b,arrowstyle='-|>',mutation_scale=10,lw=1,color=c,linestyle='--' if dash else '-',shrinkA=3,shrinkB=3))
txt(.1,2.96,'(a) Fault-triggered monitor',12,ha='left',bold=True)
box(.1,2.05,1.7,.5,'Provenance log',blue,fs=10.5)
box(2.28,2.05,1.9,.5,'Supplied fault',red,fs=10.5)
box(.1,.95,1.7,.6,'Coarse\nrestart set',blue)
box(2.28,.95,1.9,.6,'Fault-note builder',gold,brown,fs=10.5)
box(4.67,.95,1.8,.6,'Selective\nreplay',blue)
arr((.95,2.05),(.95,1.55));arr((3.23,2.05),(3.23,1.55),brown)
arr((1.8,2.3),(2.28,1.55),brown,True)
# selector fault input routed in the open gap above boxes
ax.plot([2.3,2.05,2.05,.95],[2.1,1.78,1.78,1.78],color=ink,lw=1);arr((.95,1.78),(.95,1.55))
ax.plot([.95,.95,5.57],[.95,.72,.72],color=ink,lw=1);arr((5.57,.72),(5.57,.95))
# Route fault note above replay input; no line passes through another box.
ax.plot([3.23,3.23,5.57],[1.55,1.82,1.82],color=brown,lw=1);arr((5.57,1.82),(5.57,1.55),brown)
txt(5.1,2.03,'FAULT_JSON',9,c=brown)
txt(3.25,.45,'Same restart set; note only on rerun prompts',10,bold=True)
txt(3.25,.12,'No fault detection or additional monitor model call',9)
ax.plot([6.83,6.83],[.1,3],color='#cbd2d9',lw=.8)
txt(7.1,2.96,'(b) Wrong hotel ID: reuse vs. rerun',12,ha='left',bold=True)
box(7.1,1.95,1.7,.5,'Flight research',green,fs=10)
box(7.1,.85,1.7,.5,'Hotel research',red,fs=10)
box(9.4,1.4,1.05,.55,'Plan',blue)
arr((8.8,2.2),(9.4,1.8));arr((8.8,1.1),(9.4,1.55))
for y,label in [(2.37,'Book flight'),(1.72,'Book hotel'),(1.07,'Save itinerary'),(.42,'Send email')]:
 box(11.6,y,2.1,.48,label,blue,fs=10);arr((10.45,1.67),(11.6,y+.24))
txt(9.22,.22,'Green: reused   Red/blue: rerun',8.5)
fig.subplots_adjust(left=.005,right=.995,top=.99,bottom=.02)
for ext in ('pdf','svg','png'):fig.savefig(p/f'monitor_compact.{ext}',dpi=300,facecolor='white')
