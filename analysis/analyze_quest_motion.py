import re,ast,json
from pathlib import Path
import numpy as np
s=Path('/home/pjm/.codex/attachments/a63b46f9-9cd1-4121-89f1-62b470085389/已粘贴的文本.txt').read_text()
pat=r"\(\{'l': array\((\[\[.*?\]\])\), 'r': array\((\[\[.*?\]\])\)\}, (\{[^\n]*\})\)"
rows=list(re.finditer(pat,s,re.S)); a=np.array([[ast.literal_eval(m[1]),ast.literal_eval(m[2])] for m in rows]); p=a[:,:,:3,3]
print('frames',len(a),'empty',s.count('({}, {})'))
for h,name in enumerate(['left','right']):
 r=a[:,h,:3,:3]; print(name,'min/max/span m',p[:,h].min(0),p[:,h].max(0),np.ptp(p[:,h],axis=0),'rotation orth err',np.max(np.linalg.norm(r.transpose(0,2,1)@r-np.eye(3),axis=(1,2))),'det',np.min(np.linalg.det(r)),np.max(np.linalg.det(r)))
 print('steps percentiles m',np.percentile(np.linalg.norm(np.diff(p[:,h],axis=0),axis=1),[50,90,99,100]))
for start in range(0,len(a),10):
 q=p[start:start+10]; print('window',start+1,start+len(q),'span cm',np.round(np.ptp(q,axis=0)*100,1).tolist(),'path cm',np.round(np.linalg.norm(np.diff(q,axis=0),axis=2).sum(0)*100,1).tolist())
buttons=[ast.literal_eval(m[3]) for m in rows]; print('nonzero inputs',{k:sum(bool(np.any(b.get(k,0))) for b in buttons) for k in buttons[0]})
np.savez('analysis/quest_motion.npz',matrices=a)
np.savetxt('analysis/quest_positions.csv',np.c_[np.arange(1,len(a)+1),p.reshape(len(a),6)],delimiter=',',header='sample,left_x,left_y,left_z,right_x,right_y,right_z',comments='')
try:
 import matplotlib; matplotlib.use('Agg'); import matplotlib.pyplot as plt
 fig,axs=plt.subplots(3,1,figsize=(12,8),sharex=True)
 for j in range(3):
  for h,label in enumerate(['Left','Right']): axs[j].plot(np.arange(1,len(a)+1),100*p[:,h,j],label=label)
  axs[j].set_ylabel('XYZ'[j]+' (cm)');axs[j].grid(alpha=.3);axs[j].legend()
 axs[-1].set_xlabel('Printed sample index (no timestamps)');fig.suptitle('Quest controller position relative to headset');fig.tight_layout();fig.savefig('analysis/quest_positions.png',dpi=160)
except ImportError as e:print(e)
