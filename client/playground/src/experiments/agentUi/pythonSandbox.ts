// Runs an approved Python program in the browser: Pyodide (CPython compiled to WebAssembly) in a
// Web Worker. The program sees only the project's files in a virtual file system; it has no
// access to this machine, and a program that runs past its timeout is stopped by terminating the
// worker. Pyodide (~10 MB) downloads on the first run; the timeout starts after it has loaded.

const PYODIDE = 'https://cdn.jsdelivr.net/pyodide/v0.26.4/full/'

const WORKER = `
importScripts(${JSON.stringify(PYODIDE + 'pyodide.js')});
let py = null;
self.onmessage = async (e) => {
  const { files, entry } = e.data;
  try {
    if (!py) py = await loadPyodide({ indexURL: ${JSON.stringify(PYODIDE)} });
    self.postMessage({ type: 'started' });
    const out = [], err = [];
    py.setStdout({ batched: (s) => out.push(s) });
    py.setStderr({ batched: (s) => err.push(s) });
    // a fresh copy of the project, and no modules left over from the last run
    py.runPython("import shutil, os, sys\\nos.chdir('/')\\nshutil.rmtree('/project', ignore_errors=True)\\nos.makedirs('/project', exist_ok=True)\\nfor m in [k for k, v in list(sys.modules.items()) if (getattr(v, '__file__', '') or '').startswith('/project')]: del sys.modules[m]");
    for (const [path, content] of Object.entries(files)) {
      const full = '/project/' + path;
      const dir = full.slice(0, full.lastIndexOf('/'));
      py.FS.mkdirTree(dir);
      py.FS.writeFile(full, content);
    }
    let exit = 0;
    try {
      await py.runPythonAsync("import os, sys, runpy\\nos.chdir('/project')\\nsys.path.insert(0, '/project')\\nsys.argv = [" + JSON.stringify(entry) + "]\\ntry:\\n    runpy.run_path(" + JSON.stringify(entry) + ", run_name='__main__')\\nexcept SystemExit as ex:\\n    code = ex.code if isinstance(ex.code, int) else (0 if ex.code is None else 1)\\n    if code: raise SystemExit(code)\\nfinally:\\n    sys.path.remove('/project')");
    } catch (ex) {
      const text = String(ex && ex.message || ex);
      const m = text.match(/SystemExit: (\\d+)/);
      exit = m ? Number(m[1]) : 1;
      if (!m) err.push(text.trim());
    }
    self.postMessage({ type: 'done', stdout: out.join('\\n'), stderr: err.join('\\n'), exit });
  } catch (ex) {
    self.postMessage({ type: 'failed', message: String(ex && ex.message || ex) });
  }
};`

export type RunResult = { exit: number | null; stdout: string; stderr: string; timedOut: boolean; error?: string }

let worker: Worker | null = null

function getWorker() {
  if (!worker) worker = new Worker(URL.createObjectURL(new Blob([WORKER], { type: 'text/javascript' })))
  return worker
}

/** Run `entry` from `files`. onLoaded fires once Python is ready and the program starts. */
export function runPython(files: Record<string, string>, entry: string, timeoutS: number, onLoaded?: () => void): Promise<RunResult> {
  const w = getWorker()
  return new Promise((resolve) => {
    let timer = 0
    const finish = (r: RunResult) => { clearTimeout(timer); w.onmessage = null; w.onerror = null; resolve(r) }
    w.onmessage = (e: MessageEvent<{ type: string; stdout?: string; stderr?: string; exit?: number; message?: string }>) => {
      const m = e.data
      if (m.type === 'started') {
        onLoaded?.()
        timer = window.setTimeout(() => {
          w.terminate(); worker = null // the only way to stop a runaway program
          finish({ exit: null, stdout: '', stderr: '', timedOut: true })
        }, timeoutS * 1000)
      } else if (m.type === 'done') {
        finish({ exit: m.exit ?? 1, stdout: m.stdout ?? '', stderr: m.stderr ?? '', timedOut: false })
      } else if (m.type === 'failed') {
        worker = null; w.terminate()
        finish({ exit: null, stdout: '', stderr: '', timedOut: false, error: `Python couldn't start: ${m.message}` })
      }
    }
    w.onerror = () => { worker = null; w.terminate(); finish({ exit: null, stdout: '', stderr: '', timedOut: false, error: 'Python couldn\'t start (is the network available to download Pyodide?)' }) }
    w.postMessage({ files, entry })
  })
}

/** Stop a running program (cancel). */
export function stopPython() {
  worker?.terminate()
  worker = null
}
