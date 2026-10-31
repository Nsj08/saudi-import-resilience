"""Source for two deterministic figures; Matplotlib is optional."""
import csv,textwrap
from fractions import Fraction as F
from pathlib import Path
import matplotlib.pyplot as plt
ROOT=Path(__file__).resolve().parents[1]
rows=list(csv.DictReader(open(ROOT/'results'/'certificates.csv')))
vals={m:[] for m in 'GOP'};full={m:0 for m in 'GOP'}
for r in rows:
 if r['config']=='primary' and r['year']=='2025' and r['status']=='strict' and r['metric']=='fraction' and r['k']=='3':
  x=r['certificate_radius']
  if x=='certified_full_domain':full[r['model']]+=1
  else:vals[r['model']].append(float(F(x)))
fig,ax=plt.subplots(figsize=(7.2,4.3))
ax.boxplot([vals[m] for m in 'GOP'],tick_labels=['G global','O origin-shared','P product-specific'],showfliers=False)
ax.set_ylabel('Exact first-loss radius δ (allowance fraction)')
ax.set_title('Primary 2025 strict top-three cases, fraction metric')
caption='Finite n: '+', '.join(f'{m}={len(vals[m])}' for m in 'GOP')+'; full-domain excluded: '+', '.join(f'{m}={full[m]}' for m in 'GOP')+'. Equal marginal ranges; independently choosable coordinates in a hypothetical Cartesian set, not probabilistic independence or measured country/product correlation.'
fig.text(.5,.012,textwrap.fill(caption,115),ha='center',fontsize=7)
fig.tight_layout(rect=[0,.14,1,1]);fig.savefig(ROOT/'figures'/'certificate_radii.png',dpi=180);plt.close(fig)
# Root-frozen synthetic worked H1 example.
c1,c2=F(1,5),F(39,200);a1,a2=F(3,5),F(61,100);g=F(1,4)
pair=[(float(c1-g*a1),float(c2-g*a2)),(float(c1-a1*F(26,100)),float(c2-a2*F(24,100)))]
fig,ax=plt.subplots(figsize=(7.2,4.3))
for label,values,color in [('p',[pair[0][0],pair[1][0]],'#1f77b4'),('q',[pair[0][1],pair[1][1]],'#d95f02')]:ax.plot([0,1],values,marker='o',linewidth=2,label=label,color=color)
ax.set_xticks([0,1],['Center γ₀=.25','P witness'])
ax.set_ylabel('Residual share of annual value');ax.set_title('Synthetic example: product-specific rates reverse p and q')
ax.legend(frameon=False);fig.text(.5,.015,'At center: p=.0500, q=.0425; witness rates tₚ=.26, t_q=.24 give p=.0440, q=.0486. Synthetic only.',ha='center',fontsize=8)
fig.tight_layout(rect=[0,.07,1,1]);fig.savefig(ROOT/'figures'/'synthetic_reversal.png',dpi=180);plt.close(fig)
