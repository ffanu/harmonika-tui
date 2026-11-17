// The installer supplies its verified interpreter path, including paths with
// spaces. No shell, credentials, config contents, or user commands enter argv.
export function helperCommand(options={}, env=process.env, platform=process.platform) {
  const python=options.python ?? env.HARMONIKA_PYTHON ?? env.HARMONIKA_PYTHON_EXECUTABLE ?? (platform==='win32'?'python':'python3');
  if(typeof python!=='string'||!python.trim()||/[\r\n\0]/.test(python))throw Error('invalid_python_command');
  return [python,'-I'];
}
