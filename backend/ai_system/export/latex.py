"""Native LaTeX document assembly and PDF compilation. No Markdown conversion."""

import re
import shutil
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional

from .markdown_prep import run_command
from ai_system.sandbox.commands import sandbox_operation

TEMPLATE_PATH = Path(__file__).with_name("template.tex")
BUILD_DIR = ".build/latex"
COMPILE_TIMEOUT = 180


@dataclass
class LatexResult:
    tex_path: str
    pdf_path: Optional[str] = None
    pages: Optional[int] = None
    error: Optional[str] = None
    warnings: List[str] = field(default_factory=list)
    diagnostics: List[dict] = field(default_factory=list)
    log_path: Optional[str] = None
    exit_code: Optional[int] = None

    @property
    def success(self) -> bool:
        return self.pdf_path is not None and self.error is None


def escape_plain(text: str) -> str:
    """Escape registered plain text, preserving authored TeX elsewhere."""
    table = {
        "\\": r"\textbackslash{}", "&": r"\&", "%": r"\%", "$": r"\$", "#": r"\#",
        "_": r"\_", "{": r"\{", "}": r"\}", "~": r"\textasciitilde{}", "^": r"\textasciicircum{}",
    }
    return "".join(table.get(ch, ch) for ch in text)


def native_document(title: str, abstract: str, body: str) -> str:
    """Join native section fragments into the built-in ctex document."""
    values = {"TITLE": escape_plain(title), "ABSTRACT": abstract.strip(), "BODY": body.strip()}
    return re.sub(r"<<(TITLE|ABSTRACT|BODY)>>", lambda m: values[m.group(1)], TEMPLATE_PATH.read_text(encoding="utf-8"))


def fragment_issues(text: str) -> List[str]:
    """Deterministic checks fed to the existing section repair loop."""
    issues = []
    if re.search(r"(?m)^\s*(?:#{1,6}\s|```|\|.*\|\s*$)", text) or re.search(r"!\[[^\]]*\]\(", text):
        issues.append("LaTeX 模式不能使用 Markdown 标题、表格、图片或代码围栏，请改成原生 LaTeX。")
    if re.search(r"\\(?:documentclass|usepackage|input|include|includegraphics|write|openout|read|newcommand|renewcommand)\b|\\(?:begin|end)\{document\}", text):
        issues.append("只输出本节 LaTeX 正文，不要输出导言区、文档环境、文件操作或图片路径；插图使用登记图引用。")
    if re.search(r"\\begin\{figure\*?\}", text):
        issues.append("登记图引用必须独占一行，不要再包 figure 环境或添加图题和图标签；系统会生成唯一的 figure、caption 和 label。")
    depth = 0
    clean = re.sub(r"(?<!\\)%[^\n]*", "", text)
    for token in re.findall(r"(?<!\\)[{}]", clean):
        depth += 1 if token == "{" else -1
        if depth < 0:
            break
    if depth:
        issues.append("LaTeX 花括号未配对。")
    stack = []
    for command, env in re.findall(r"\\(begin|end)\{([^{}]+)\}", clean):
        if command == "begin":
            stack.append(env)
        elif not stack or stack.pop() != env:
            issues.append("LaTeX 环境未配对。")
            break
    else:
        if stack:
            issues.append("LaTeX 环境未配对。")
    return issues


def compile_diagnostics(log: str) -> List[dict]:
    """Read TeX's file/line diagnostics, including the offending command."""
    lines=log.splitlines();errors=[]
    for index,line in enumerate(lines):
        match=re.match(r'^(.*?\.(?:tex|sty|cls)):(\d+):\s*(.+)$',line)
        if not match and not line.startswith('! '):continue
        context='\n'.join(lines[max(0,index-1):index+7]).strip()
        errors.append({'file':match[1].removeprefix('./') if match else None,
                       'line':int(match[2]) if match else None,
                       'message':match[3] if match else line[2:], 'context':context})
    return errors[:5]


def result_reference_issues(text: str) -> List[str]:
    """Reject result IDs used as TeX counters, without rewriting authored refs."""
    clean = re.sub(r'(?m)(?<!\\)%.*$', '', text)
    keys = re.findall(r'\\(?:ref|eqref|autoref|[cC]ref)\*?\{([^{}]+)\}', clean)
    return [f'{key} 是登记结果 ID，不能用 LaTeX 交叉引用读取数值；请读取该结果的实际值、单位与适用条件，缺失或不明确时报告缺项。'
            for key in dict.fromkeys(keys) if re.fullmatch(r'(?:R:)?R\d+', key)]


def source_diagnostics(workspace: Path, tex_name: str) -> List[dict]:
    # Reuse the project's dependency discovery so split-file documents work too.
    from services.chat_services.paper_project import files
    sources = {name: row['text'] for name,row in files(workspace, main=tex_name).items() if name.endswith('.tex')}
    clean = '\n'.join(re.sub(r'(?m)(?<!\\)%.*$', '', text) for text in sources.values())
    diagnostics = []
    commands = re.findall(r'\\(begin|end)\s*\{document\}', clean)
    if sorted(commands) != ['begin', 'end']:
        missing = ['\\'+cmd+'{document}' for cmd in ('begin','end') if cmd not in commands]
        message = ('缺少 '+ '、'.join(missing)) if missing else '文档起止结构重复'
        diagnostics.append({'file':tex_name, 'line':None, 'message':message,
            'context':'源码可能被截断；请核对原要求、已有结果和全文，恢复缺失内容后再编译，不能仅补文档结尾。\n'+sources.get(tex_name,'')[-600:]})
    for name,text in sources.items():
        diagnostics.extend({'file':name,'line':None,'message':message,'context':''} for message in result_reference_issues(text))
        from ai_system.graph.registry import REF_PATTERN
        if REF_PATTERN.search(text):
            diagnostics.append({'file':name,'line':None,'message':'正文仍有未解析的登记结果引用，须核对实际结果后修复', 'context':''})
    return diagnostics


@sandbox_operation
async def compile_pdf(workspace: Path, tex_name: str = "paper.tex") -> LatexResult:
    """One delivery gate for native source, compiler status and final references."""
    result = LatexResult(tex_path=tex_name, log_path=f'{BUILD_DIR}/compile.log')
    build = workspace / BUILD_DIR
    build.mkdir(parents=True, exist_ok=True)
    stem = Path(tex_name).stem
    pdf = build / f"{stem}.pdf"
    pdf.unlink(missing_ok=True)
    transcript = []

    def finish(error=None):
        result.error = error
        (workspace/result.log_path).write_text('\n\n'.join(transcript + ([error] if error else [])), encoding='utf-8')
        return result

    def summary():
        first = result.diagnostics[0]
        location = first['file'] or ''
        if first['line'] is not None: location += f" 第{first['line']}行"
        return (location+'：' if location else '')+first['message']+'\n'+first['context']

    result.diagnostics = source_diagnostics(workspace, tex_name)
    if result.diagnostics:
        transcript.append('\n\n'.join(d['message']+'\n'+d['context'] for d in result.diagnostics))
        return finish('LaTeX 源码检查失败：'+summary())
    cmd = ["xelatex", "-no-shell-escape", "-file-line-error", "-interaction=nonstopmode", "-halt-on-error", f"-output-directory={BUILD_DIR}", tex_name]
    log_file = build / f"{stem}.log"
    log_file.unlink(missing_ok=True)
    log = ''
    try:
        for iteration in range(3):  # bibliography, then resolve cross-references
            code, out, err = await run_command(cmd, workspace, timeout=COMPILE_TIMEOUT)
            result.exit_code = code
            log = log_file.read_text(encoding='utf-8', errors='replace') if log_file.exists() else out+'\n'+err
            transcript.append(f'xelatex pass {iteration+1}, exit {code}\n'+out+'\n'+err+'\n'+log)
            if code != 0: break
            if iteration == 0:
                aux = build / f"{stem}.aux"
                if aux.exists() and r'\bibdata{' in aux.read_text(errors='replace'):
                    code, out, err = await run_command(['bibtex', f'{BUILD_DIR}/{stem}'], workspace, timeout=COMPILE_TIMEOUT)
                    result.exit_code = code
                    blg = build/f'{stem}.blg'
                    biblog = blg.read_text(errors='replace') if blg.exists() else out+'\n'+err
                    transcript.append(f'bibtex, exit {code}\n'+out+'\n'+err+'\n'+biblog)
                    if code != 0:
                        result.diagnostics = compile_diagnostics(biblog)
                        return finish('参考文献编译失败：'+(summary() if result.diagnostics else biblog.strip()[:1200]))
    except Exception as exc:
        from ai_system.research.review_cache import error_detail
        transcript.append(getattr(exc, 'sandbox_stdout', '') + '\n' + getattr(exc, 'sandbox_stderr', ''))
        if log_file.exists(): transcript.append(log_file.read_text(errors='replace'))
        return finish('编译进程未完成：'+error_detail(exc))
    result.diagnostics = compile_diagnostics(log)
    undefined = [line for line in log.splitlines() if re.search(r'(?:Reference|Citation).*undefined|There were undefined (?:references|citations)', line)]
    if undefined:
        result.diagnostics.append({'file':None,'line':None,'message':'存在未定义的交叉引用或文献引用', 'context':'\n'.join(undefined)})
    if result.exit_code != 0 or result.diagnostics or not pdf.exists():
        return finish('xelatex 编译失败：'+(summary() if result.diagnostics else f'退出码 {result.exit_code}；'+log.strip()[:1200]))

    target = workspace / f"{stem}.pdf"
    from services.file_services.file_helper import atomic_write
    atomic_write(target, lambda f: f.write(pdf.read_bytes()))
    result.pdf_path = target.name
    pages = re.search(r"Output written on .*?\((\d+) pages?", log, re.DOTALL)
    result.pages = int(pages.group(1)) if pages else None
    missing = sorted(set(re.findall(r"Missing character: There is no (\S+)", log)))
    result.warnings = [f"缺字形：{' '.join(missing[:10])}"] if missing else []
    return finish()
