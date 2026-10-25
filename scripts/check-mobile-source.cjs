// Owner-only verification of synthetic generated source, never an executor for caller code.
const fs=require('node:fs');const ts=require(process.env.HARMONIKA_TYPESCRIPT_MODULE||'typescript');
const input=JSON.parse(fs.readFileSync(process.argv[2],'utf8'));let checked=0;let errors=[];
for (const [skill,result] of Object.entries(input)) {
 for (const f of result.files||[]) {
  if (!/\.(tsx?|jsx?|json)$/.test(f.path)) continue;
  if (f.path.endsWith('.json')) {
   const parsed=ts.parseConfigFileTextToJson(f.path,f.content);
   if(parsed.error)errors.push({skill,path:f.path,code:parsed.error.code,message:ts.flattenDiagnosticMessageText(parsed.error.messageText,' ')});
  } else {
   const kind=f.path.endsWith('.tsx')?ts.ScriptKind.TSX:f.path.endsWith('.jsx')?ts.ScriptKind.JSX:f.path.endsWith('.ts')?ts.ScriptKind.TS:ts.ScriptKind.JS;
   const source=ts.createSourceFile(f.path,f.content,ts.ScriptTarget.ES2022,true,kind);
   for (const e of source.parseDiagnostics) errors.push({skill,path:f.path,code:e.code,message:ts.flattenDiagnosticMessageText(e.messageText,' ')});
  }
  checked++;
 }
}
console.log(JSON.stringify({syntax_files_checked:checked,errors}));if(errors.length)process.exit(1);
