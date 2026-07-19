jscommand { const a=view.app; const hl=(c)=>{const ed=a.workspace.activeEditor?.editor; if(!ed)return; const cur=ed.getCursor(),line=ed.getLine(cur.line); let s=line.search(/\S/); if(s<0)return; const bullet=line.slice(s).match(/^(?:[-*+]|\d+\.)\s+(?:\[[ xX]\]\s+)?/); if(bullet)s+=bullet[0].length; ed.setSelection({line:cur.line,ch:s},{line:cur.line,ch:line.trimEnd().length}); a.commands.executeCommandById('highlightr-plugin:'+c);}; ['Win','Miss','Process','Thought','Thread'].forEach(c=>a.commands.addCommand({id:'hl-line-'+c.toLowerCase(),name:'Highlight Line '+c,callback:()=>hl(c)})); }

exmap hlWin obcommand hl-line-win
exmap hlMiss obcommand hl-line-miss
exmap hlProcess obcommand hl-line-process
exmap hlThought obcommand hl-line-thought
exmap hlThread obcommand hl-line-thread

nmap \w :hlWin<CR>
nmap \m :hlMiss<CR>
nmap \p :hlProcess<CR>
nmap \t :hlThought<CR>
nmap \h :hlThread<CR>
