"""Trusted single-job XeLaTeX/BibTeX runner, executed inside the compiler sandbox."""
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import time

BUILD_DIR = '.build/latex'
RERUN = r'Rerun to get cross-references right|Label\(s\) may have changed|Please \(re\)run|rerunfilecheck Warning'


def compile_latex(tex_name, timeout):
    build = Path(BUILD_DIR)
    build.mkdir(parents=True, exist_ok=True)
    stem = Path(tex_name).stem
    for suffix in ('pdf', 'log'):
        (build / f'{stem}.{suffix}').unlink(missing_ok=True)
    transcript, steps = [], []
    result = {'exit_code': 0, 'failed_stage': None, 'timed_out': False,
              'log_path': f'{BUILD_DIR}/{stem}.log', 'steps': steps}

    def execute(argv, label, log_path, **options):
        started = time.monotonic()
        try:
            process = subprocess.run(argv, capture_output=True, timeout=timeout, **options)
            code, out, err = process.returncode, process.stdout, process.stderr
        except subprocess.TimeoutExpired as exc:
            code, out, err = 124, exc.stdout or b'', exc.stderr or b''
            result.update(timed_out=True, error=f'{label} 超过 {timeout} 秒，编译进程已停止')
        out, err = out.decode('utf-8', errors='replace'), err.decode('utf-8', errors='replace')
        log = log_path.read_text(errors='replace') if log_path.exists() else out + '\n' + err
        transcript.append(f'{label}, exit {code}\n{out}\n{err}\n{log}')
        steps.append({'stage': label, 'exit_code': code, 'seconds': round(time.monotonic() - started, 4)})
        result.update(exit_code=code, log_path=log_path.as_posix())
        return code, log

    argv = ['xelatex', '-no-shell-escape', '-file-line-error', '-interaction=nonstopmode',
            '-halt-on-error', f'-output-directory={BUILD_DIR}', tex_name]
    for iteration in range(3):
        code, log = execute(argv, f'xelatex pass {iteration + 1}', build / f'{stem}.log')
        if code:
            result['failed_stage'] = 'xelatex'
            break
        if iteration >= 1 and not re.search(RERUN, log):
            break
        if iteration == 0:
            aux = build / f'{stem}.aux'
            if aux.exists() and r'\bibdata{' in aux.read_text(errors='replace'):
                env = {**os.environ, **{key: str(Path.cwd()) + os.pathsep + os.environ.get(key, '')
                                       for key in ('BIBINPUTS', 'BSTINPUTS')}}
                code, _ = execute(['bibtex', stem], 'bibtex', build / f'{stem}.blg', cwd=build, env=env)
                if code:
                    result['failed_stage'] = 'bibtex'
                    break
    (build / 'compile.log').write_text('\n\n'.join(transcript), encoding='utf-8')
    return result


if __name__ == '__main__':
    result = compile_latex(sys.argv[1], int(sys.argv[2]))
    print(json.dumps(result, ensure_ascii=False))
    sys.exit(result['exit_code'])
