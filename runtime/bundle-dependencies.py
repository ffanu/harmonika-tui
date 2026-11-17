"""Build small, platform-neutral CJS bundles; never ship node_modules/native build tools."""
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import tempfile

SOURCE = Path(__file__).resolve().parent
ENTRIES = {
    'sdk/client/index.js': '@modelcontextprotocol/sdk/client/index.js',
    'sdk/client/stdio.js': '@modelcontextprotocol/sdk/client/stdio.js',
    'sdk/server/index.js': '@modelcontextprotocol/sdk/server/index.js',
    'sdk/server/stdio.js': '@modelcontextprotocol/sdk/server/stdio.js',
    'sdk/types.js': '@modelcontextprotocol/sdk/types.js',
    'ajv2020.cjs': 'ajv/dist/2020.js',
}

def bundle(destination, *, dependency_root=None):
    destination = Path(destination)
    if dependency_root is None:
        with tempfile.TemporaryDirectory(prefix='harmonika-build-deps-') as temp:
            root = Path(temp)
            for name in ('package.json', 'package-lock.json'):
                shutil.copy2(SOURCE/'dependencies'/name, root/name)
            npm = shutil.which('npm.cmd' if __import__('os').name == 'nt' else 'npm')
            if not npm:
                raise ValueError('npm_required_only_for_release_build')
            subprocess.run([npm, 'ci', '--ignore-scripts', '--no-audit', '--no-fund'], cwd=root, check=True, timeout=420,
                           shell=(__import__('os').name == 'nt'))
            return bundle(destination, dependency_root=root)
    dependency_root = Path(dependency_root).resolve()
    node = shutil.which('node')
    if not node:
        raise ValueError('node_required_for_release_build')
    destination.mkdir(parents=True, exist_ok=True)
    script = r"""
const path=require('node:path'),fs=require('node:fs');
const root=process.argv[1],out=process.argv[2],entries=JSON.parse(process.argv[3]);
const esbuild=require(path.join(root,'node_modules/esbuild'));
const used=new Set();
for(const [name,modulePath] of Object.entries(entries)) {
 const entry=require.resolve(modulePath,{paths:[root]});
 const result=esbuild.buildSync({entryPoints:[entry],outfile:path.join(out,name),bundle:true,
   platform:'node',format:'cjs',target:'node18',minifyWhitespace:true,metafile:true,
   legalComments:'linked',logLevel:'error'});
 for(const input of Object.keys(result.metafile.inputs)) {
  let dir=path.dirname(path.resolve(input));
  if(!dir.startsWith(path.join(root,'node_modules')+path.sep))continue;
  while(dir!==root&&!fs.existsSync(path.join(dir,'package.json')))dir=path.dirname(dir);
  if(dir!==root)used.add(dir);
 }
}
const notices=[];
for(const dir of [...used].sort()){
 const pkg=JSON.parse(fs.readFileSync(path.join(dir,'package.json'),'utf8'));
 notices.push('=== '+pkg.name+' '+pkg.version+' ('+String(pkg.license||'see license')+') ===');
 for(const name of fs.readdirSync(dir).filter(n=>/^(?:licen[sc]e|notice|copying)(?:[.-]|$)/i.test(n))) {
  if(fs.statSync(path.join(dir,name)).isFile())notices.push(fs.readFileSync(path.join(dir,name),'utf8'));
 }
}
fs.writeFileSync(path.join(out,'THIRD-PARTY-NOTICES.txt'),notices.join('\n\n')+'\n');
"""
    subprocess.run([node, '-e', script, str(dependency_root), str(destination.resolve()), json.dumps(ENTRIES)],
                   check=True, timeout=180)
    (destination/'sdk/package.json').write_text('{"type":"commonjs"}\n', encoding='utf-8')
    packages={name: json.loads((dependency_root/'node_modules'/name/'package.json').read_text(encoding='utf-8'))['version']
              for name in ('@modelcontextprotocol/sdk', 'ajv')}
    lock=SOURCE/'dependencies/package-lock.json'
    (destination/'dependencies.json').write_text(json.dumps({
        'packages':packages, 'node_minimum':18,
        'lock_sha256':hashlib.sha256(lock.read_bytes()).hexdigest(),
        'bundled':True, 'build_tools_included':False,
    }, indent=2)+'\n', encoding='utf-8')
    return packages

if __name__ == '__main__':
    import sys
    bundle(Path(sys.argv[1]))
